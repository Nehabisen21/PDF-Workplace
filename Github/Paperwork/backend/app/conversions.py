import io
import os
import re
import shutil
import subprocess
import tempfile
import warnings
import zipfile
import zlib
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from xml.etree.ElementTree import ParseError
from xml.sax.saxutils import escape

import cairosvg
from defusedxml.common import DefusedXmlException
import defusedxml.ElementTree as SafeET
import extract_msg
import pymupdf
import rarfile
from PIL import Image, ImageSequence
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont

from .pdf_ops import MAX_IMAGE_PIXELS, MAX_PDF_PAGES, _render_scale, process_pdf


MAX_ARCHIVE_ENTRIES = 500
MAX_CONTAINER_ENTRIES = 10_000
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_CONVERSION_OUTPUT_BYTES = 200 * 1024 * 1024
MAX_CONVERSION_PIXELS = 50_000_000
MAX_CONVERSION_SECONDS = 120
MAX_EMAIL_BODY_BYTES = 2 * 1024 * 1024
MAX_SVG_BYTES = 10 * 1024 * 1024
MAX_SVG_DIMENSION = 10_000
MAX_SVG_PIXELS = 40_000_000
UNICODE_FONT_NAME = "PDFWorkplaceUnicode"
UNICODE_FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")

IMAGE_FORMATS = frozenset({"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp"})
DOCUMENT_FORMATS = frozenset({"docx", "doc", "odt"})
SPREADSHEET_FORMATS = frozenset({"xlsx", "xls", "ods"})
PRESENTATION_FORMATS = frozenset({"pptx", "ppt", "odp"})
EBOOK_FORMATS = frozenset({"epub", "mobi", "azw3", "fb2"})
COMIC_FORMATS = frozenset({"cbz", "cbr"})
EMAIL_FORMATS = frozenset({"eml", "msg"})
SUPPORTED_CONVERSION_SOURCES = frozenset(
    {"pdf", "svg"} | IMAGE_FORMATS | DOCUMENT_FORMATS | SPREADSHEET_FORMATS
    | PRESENTATION_FORMATS | EBOOK_FORMATS | COMIC_FORMATS | EMAIL_FORMATS
)

_OFFICE_FAMILIES = (
    DOCUMENT_FORMATS,
    SPREADSHEET_FORMATS,
    PRESENTATION_FORMATS,
)

if UNICODE_FONT_PATH.is_file() and UNICODE_FONT_NAME not in pdfmetrics.getRegisteredFontNames():
    pdfmetrics.registerFont(TTFont(UNICODE_FONT_NAME, str(UNICODE_FONT_PATH)))
    registerFontFamily(
        UNICODE_FONT_NAME,
        normal=UNICODE_FONT_NAME,
        bold=UNICODE_FONT_NAME,
        italic=UNICODE_FONT_NAME,
        boldItalic=UNICODE_FONT_NAME,
    )


def conversion_targets(source_format: str) -> set[str]:
    if source_format == "pdf":
        return IMAGE_FORMATS | {"cbz"}
    if source_format == "svg":
        return {"pdf", "png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"}
    if source_format in IMAGE_FORMATS:
        return (IMAGE_FORMATS - {source_format}) | {"pdf"}
    if source_format in COMIC_FORMATS:
        return {"pdf"} | ({"cbz"} if source_format == "cbr" else set())
    if source_format in EMAIL_FORMATS:
        return {"pdf"}
    if source_format in EBOOK_FORMATS:
        return (EBOOK_FORMATS - {source_format}) | {"pdf"}
    for family in _OFFICE_FAMILIES:
        if source_format in family:
            return (family - {source_format}) | {"pdf"}
    return set()


def _safe_stem(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-_")
    return (stem or "converted-file")[:80]


def _natural_key(value: str) -> list[tuple[int, int | str]]:
    return [
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
    ]


def _require_matching_extension(filename: str, source_format: str) -> None:
    suffix = Path(filename).suffix.lower().lstrip(".")
    equivalent = {
        "jpg": {"jpg", "jpeg"},
        "jpeg": {"jpg", "jpeg"},
        "tif": {"tif", "tiff"},
        "tiff": {"tif", "tiff"},
    }
    allowed = equivalent.get(source_format, {source_format})
    if suffix not in allowed:
        raise ValueError(
            f"Every selected file must use the chosen .{source_format} source format."
        )


def _validate_zip_container(data: bytes, source_format: str) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_CONTAINER_ENTRIES:
                raise ValueError("The uploaded package contains too many entries.")
            if sum(entry.file_size for entry in entries) > MAX_ARCHIVE_BYTES * 2:
                raise ValueError("The uncompressed package exceeds the 200 MB limit.")
            entry_names = {entry.filename for entry in entries}
            required_entries = {
                "docx": {"word/document.xml"},
                "xlsx": {"xl/workbook.xml"},
                "pptx": {"ppt/presentation.xml"},
                "epub": {"META-INF/container.xml"},
            }.get(source_format, set())
            if not required_entries.issubset(entry_names):
                raise ValueError("The package contents do not match the selected source format.")
            expected_mimetypes = {
                "odt": b"application/vnd.oasis.opendocument.text",
                "ods": b"application/vnd.oasis.opendocument.spreadsheet",
                "odp": b"application/vnd.oasis.opendocument.presentation",
                "epub": b"application/epub+zip",
            }
            if source_format in expected_mimetypes:
                try:
                    if archive.read("mimetype") != expected_mimetypes[source_format]:
                        raise ValueError(
                            "The package contents do not match the selected source format."
                        )
                except KeyError as exc:
                    raise ValueError(
                        "The package contents do not match the selected source format."
                    ) from exc
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("The uploaded package is not a readable ZIP-based file.") from exc


def _validate_signature(filename: str, data: bytes, source_format: str) -> None:
    if not data:
        raise ValueError("The uploaded file is empty.")
    if source_format == "pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("The uploaded file is not a valid PDF.")
    elif source_format in IMAGE_FORMATS:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
                    expected = {
                        "jpg": "JPEG", "jpeg": "JPEG", "tif": "TIFF", "tiff": "TIFF",
                    }.get(source_format, source_format.upper())
                    if image.format != expected:
                        raise ValueError("The file contents do not match the selected image format.")
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError("The image exceeds the safe pixel limit.") from exc
        except (OSError, SyntaxError) as exc:
            raise ValueError("The uploaded file is not a valid image.") from exc
    elif source_format == "svg":
        if len(data) > MAX_SVG_BYTES:
            raise ValueError("SVG files must be no larger than 10 MB.")
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise ValueError("SVG files must not contain document types or entities.")
        try:
            root = SafeET.fromstring(data)
        except (DefusedXmlException, ParseError) as exc:
            raise ValueError("The uploaded file is not a valid SVG.") from exc
        if not root.tag.endswith("svg"):
            raise ValueError("The uploaded file is not a valid SVG.")
        for element in root.iter():
            for key, value in element.attrib.items():
                if key.endswith(("href", "base")) and value and not value.startswith(("data:", "#")):
                    raise ValueError("SVG files cannot reference external resources.")
                if re.search(r"url\(\s*['\"]?(?!data:|#)", value, re.IGNORECASE):
                    raise ValueError("SVG files cannot reference external resources.")
            for text in (element.text or "", element.tail or ""):
                if re.search(
                    r"(?:url\(\s*['\"]?(?!data:|#)|@import\s+['\"]?(?!data:|#))",
                    text,
                    re.IGNORECASE,
                ):
                    raise ValueError("SVG files cannot reference external resources.")
    elif source_format in {"cbz", "epub", "docx", "xlsx", "pptx", "ods", "odp"}:
        if not data.startswith(b"PK"):
            raise ValueError("The file contents do not match the selected ZIP-based format.")
        _validate_zip_container(data, source_format)
    elif source_format == "cbr":
        if not data.startswith((b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00")):
            raise ValueError("The uploaded file is not a supported RAR archive.")
    elif source_format in {"doc", "xls", "ppt", "msg"}:
        if not data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
            raise ValueError("The file contents do not match the selected format.")
    elif source_format == "mobi":
        if len(data) < 68 or data[60:68] != b"BOOKMOBI":
            raise ValueError("The uploaded file is not a valid MOBI ebook.")
    elif source_format == "azw3":
        if len(data) < 68 or data[60:68] not in {b"BOOKMOBI", b"TPZ0"}:
            raise ValueError("The uploaded file is not a valid AZW3 ebook.")
    elif source_format == "eml" and b"@" not in data[:4096]:
        raise ValueError("The uploaded file does not look like an email message.")
    elif source_format == "fb2" and b"<" not in data[:4096]:
        raise ValueError("The uploaded file is not a valid FB2 ebook.")
    _require_matching_extension(filename, source_format)


def _image_frames(data: bytes, filename: str) -> list[Image.Image]:
    frames = []
    total_pixels = 0
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("The image exceeds the safe pixel limit.")
                for frame in ImageSequence.Iterator(image):
                    if frame.width * frame.height > MAX_IMAGE_PIXELS:
                        raise ValueError("An image frame exceeds the safe pixel limit.")
                    total_pixels += frame.width * frame.height
                    if total_pixels > MAX_CONVERSION_PIXELS:
                        raise ValueError("Image conversions are limited to 50 million pixels per file.")
                    frames.append(frame.convert("RGBA" if "A" in frame.getbands() else "RGB"))
                    if len(frames) > MAX_PDF_PAGES:
                        raise ValueError(f"Image conversions cannot exceed {MAX_PDF_PAGES} pages.")
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("The image exceeds the safe pixel limit.") from exc
    except OSError as exc:
        raise ValueError(f"{filename} could not be decoded as an image.") from exc
    if not frames:
        raise ValueError("The image does not contain any readable frames.")
    return frames


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _svg_dimensions(data: bytes) -> tuple[int, int]:
    root = SafeET.fromstring(data)
    view_box = root.attrib.get("viewBox", "").replace(",", " ").split()
    try:
        if len(view_box) == 4:
            aspect = float(view_box[2]) / float(view_box[3])
        else:
            width = float(re.sub(r"[^0-9.eE+-]", "", root.attrib.get("width", "300")))
            height = float(re.sub(r"[^0-9.eE+-]", "", root.attrib.get("height", "150")))
            aspect = width / height
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("The SVG must declare valid dimensions or a viewBox.") from exc
    if not 0 < aspect < float("inf"):
        raise ValueError("The SVG has invalid dimensions.")
    width = min(MAX_SVG_DIMENSION, max(1, round(1800 * min(aspect, 1))))
    height = min(MAX_SVG_DIMENSION, max(1, round(width / aspect)))
    if width * height > MAX_SVG_PIXELS:
        raise ValueError("The SVG exceeds the safe pixel limit.")
    return width, height


def _convert_svg(data: bytes, target_format: str) -> bytes:
    try:
        if target_format == "pdf":
            return cairosvg.svg2pdf(bytestring=data)
        width, height = _svg_dimensions(data)
        png = cairosvg.svg2png(bytestring=data, output_width=width, output_height=height)
        if target_format == "png":
            return png
        with Image.open(io.BytesIO(png)) as image:
            output = io.BytesIO()
            save_format = "JPEG" if target_format in {"jpg", "jpeg"} else target_format.upper()
            converted = image.convert("RGB") if save_format in {"JPEG", "BMP"} else image
            converted.save(output, format=save_format)
            return output.getvalue()
    except (OSError, ValueError, zlib.error) as exc:
        raise ValueError("The selected SVG could not be rendered.") from exc


def _pdf_to_images(data: bytes, target_format: str, stem: str) -> list[tuple[str, bytes]]:
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except pymupdf.FileDataError as exc:
        raise ValueError("The uploaded file is not a readable PDF.") from exc
    outputs = []
    try:
        if document.needs_pass:
            raise ValueError("Unlock the PDF before converting it.")
        if not 1 <= document.page_count <= MAX_PDF_PAGES:
            raise ValueError(f"PDF conversions must contain 1 to {MAX_PDF_PAGES} pages.")
        scale = _render_scale(document, 120 / 72)
        for page_number, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            output = io.BytesIO()
            save_format = {
                "jpg": "JPEG", "jpeg": "JPEG", "tif": "TIFF", "tiff": "TIFF",
            }.get(target_format, target_format.upper())
            image.save(output, format=save_format)
            extension = "jpg" if target_format == "jpeg" else target_format
            outputs.append((f"{stem}-page-{page_number:04d}.{extension}", output.getvalue()))
    finally:
        document.close()
    return outputs


def _archive_images(filename: str, data: bytes, source_format: str) -> list[tuple[str, bytes]]:
    files = []
    if source_format == "cbz":
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError("The uploaded CBZ archive is not readable.") from exc
        with archive:
            entries = [
                entry for entry in archive.infolist()
                if not entry.is_dir() and Path(entry.filename).suffix.lower().lstrip(".") in IMAGE_FORMATS
            ]
            if not entries or len(entries) > MAX_ARCHIVE_ENTRIES:
                raise ValueError(f"Comic archives must contain 1 to {MAX_ARCHIVE_ENTRIES} images.")
            if any(entry.file_size > MAX_CONVERSION_OUTPUT_BYTES for entry in entries):
                raise ValueError("A comic archive image is too large to process.")
            if sum(entry.file_size for entry in entries) > MAX_ARCHIVE_BYTES:
                raise ValueError("The uncompressed comic archive exceeds the 100 MB limit.")
            for entry in sorted(entries, key=lambda item: _natural_key(item.filename)):
                try:
                    files.append((Path(entry.filename).name, archive.read(entry)))
                except (OSError, RuntimeError, zipfile.BadZipFile, zlib.error) as exc:
                    raise ValueError("A comic archive image could not be decoded.") from exc
    else:
        try:
            with tempfile.TemporaryDirectory(prefix="pdf-workplace-rar-") as directory:
                archive_path = Path(directory) / "comic.cbr"
                archive_path.write_bytes(data)
                with rarfile.RarFile(archive_path) as archive:
                    entries = [
                        entry for entry in archive.infolist()
                        if not entry.isdir() and Path(entry.filename).suffix.lower().lstrip(".") in IMAGE_FORMATS
                    ]
                    if not entries or len(entries) > MAX_ARCHIVE_ENTRIES:
                        raise ValueError(
                            f"Comic archives must contain 1 to {MAX_ARCHIVE_ENTRIES} images."
                        )
                    if sum(entry.file_size for entry in entries) > MAX_ARCHIVE_BYTES:
                        raise ValueError("The uncompressed comic archive exceeds the 100 MB limit.")
                    for entry in sorted(entries, key=lambda item: _natural_key(item.filename)):
                        files.append((Path(entry.filename).name, archive.read(entry)))
        except rarfile.RarCannotExec as exc:
            raise ValueError("CBR conversion requires the RAR archive support included in Docker.") from exc
        except rarfile.Error as exc:
            raise ValueError("The uploaded CBR archive could not be read.") from exc
    if not files:
        raise ValueError("The comic archive does not contain supported image files.")
    normalized = []
    page_count = 0
    total_pixels = 0
    for image_name, image_data in files:
        _require_matching_extension(image_name, Path(image_name).suffix.lower().lstrip("."))
        frames = _image_frames(image_data, image_name)
        page_count += len(frames)
        total_pixels += sum(frame.width * frame.height for frame in frames)
        if total_pixels > MAX_CONVERSION_PIXELS:
            raise ValueError("Comic conversions are limited to 50 million pixels per archive.")
        if page_count > MAX_PDF_PAGES:
            raise ValueError(f"Comic conversions cannot exceed {MAX_PDF_PAGES} pages.")
        normalized.extend(
            (f"page-{page_count - len(frames) + index:04d}.png", _png_bytes(frame))
            for index, frame in enumerate(frames, start=1)
        )
    return normalized


def _images_to_pdf(images: list[tuple[str, bytes]]) -> tuple[bytes, int]:
    return process_pdf("images_to_pdf", images, {})


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _email_pdf(filename: str, data: bytes, source_format: str) -> bytes:
    if len(data) > MAX_EMAIL_BODY_BYTES:
        raise ValueError("Email files must be no larger than 2 MB.")
    if source_format == "eml":
        message = BytesParser(policy=policy.default).parsebytes(data)
        values = {
            "From": str(message.get("from", "")),
            "To": str(message.get("to", "")),
            "Cc": str(message.get("cc", "")),
            "Date": str(message.get("date", "")),
            "Subject": str(message.get("subject", "")),
        }
        body = ""
        for part in message.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                try:
                    body = part.get_content()
                except (LookupError, UnicodeDecodeError):
                    payload = part.get_payload(decode=True) or b""
                    body = payload.decode("utf-8", errors="replace")
                if body:
                    break
        if not body:
            parser = _PlainText()
            for part in message.walk():
                if part.get_content_type() == "text/html" and not part.get_filename():
                    parser.feed(part.get_content())
                    body = "\n".join(parser.parts)
                    break
    else:
        with tempfile.TemporaryDirectory(prefix="pdf-workplace-msg-") as directory:
            message_path = Path(directory) / "message.msg"
            message_path.write_bytes(data)
            parsed = None
            try:
                parsed = extract_msg.Message(str(message_path))
                values = {
                    "From": str(parsed.sender or ""),
                    "To": str(parsed.to or ""),
                    "Cc": str(parsed.cc or ""),
                    "Date": str(parsed.date or ""),
                    "Subject": str(parsed.subject or ""),
                }
                body = str(parsed.body or "")
            except (
                OSError,
                ValueError,
                UnicodeError,
                extract_msg.exceptions.ExMsgBaseException,
            ) as exc:
                raise ValueError("The uploaded MSG email could not be read.") from exc
            finally:
                if parsed is not None:
                    parsed.close()
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output, pagesize=A4, rightMargin=20 * mm, leftMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
    )
    styles = getSampleStyleSheet()
    if UNICODE_FONT_NAME in pdfmetrics.getRegisteredFontNames():
        for style in styles.byName.values():
            style.fontName = UNICODE_FONT_NAME
    story = [Paragraph(escape(values["Subject"] or Path(filename).stem), styles["Title"]), Spacer(1, 8 * mm)]
    for label in ("From", "To", "Cc", "Date", "Subject"):
        if values[label]:
            story.append(Paragraph(f"<b>{label}:</b> {escape(values[label])}", styles["BodyText"]))
            story.append(Spacer(1, 2 * mm))
    story.append(Spacer(1, 5 * mm))
    for line in (body or "(No readable message body)").splitlines():
        story.append(Paragraph(escape(line) or "&nbsp;", styles["BodyText"]))
        story.append(Spacer(1, 2 * mm))
    document.build(story)
    return output.getvalue()


def _run_office(source_path: Path, target_format: str, working_dir: Path) -> bytes:
    executable = shutil.which("soffice")
    if executable is None:
        raise ValueError("Office conversion requires LibreOffice, which is not installed.")
    profile = (working_dir / "lo-profile").as_uri()
    try:
        result = subprocess.run(
            [
                executable, "--headless", "--nologo", "--nodefault", "--nofirststartwizard",
                f"-env:UserInstallation={profile}", "--convert-to", target_format,
                "--outdir", str(working_dir), str(source_path),
            ],
            cwd=working_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=MAX_CONVERSION_SECONDS,
            check=False,
            env={**os.environ, "HOME": str(working_dir)},
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Office conversion exceeded the 120-second time limit.") from exc
    output_path = working_dir / f"{source_path.stem}.{target_format}"
    if result.returncode != 0 or not output_path.is_file():
        raise ValueError("LibreOffice could not convert this file to the selected format.")
    if output_path.stat().st_size > MAX_CONVERSION_OUTPUT_BYTES:
        raise ValueError("The converted output exceeds the 200 MB per-job limit.")
    output = output_path.read_bytes()
    if not output:
        raise ValueError("LibreOffice produced an empty output file.")
    return output


def _run_calibre(data: bytes, source_format: str, target_format: str) -> bytes:
    executable = shutil.which("ebook-convert")
    if executable is None:
        raise ValueError("Ebook conversion requires Calibre, which is not installed.")
    with tempfile.TemporaryDirectory(prefix="pdf-workplace-ebook-") as directory:
        source = Path(directory) / f"source.{source_format}"
        destination = Path(directory) / f"result.{target_format}"
        source.write_bytes(data)
        try:
            result = subprocess.run(
                [executable, str(source), str(destination)],
                cwd=directory,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=MAX_CONVERSION_SECONDS,
                check=False,
                env={**os.environ, "HOME": directory},
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Ebook conversion exceeded the 120-second time limit.") from exc
        if result.returncode != 0 or not destination.is_file():
            raise ValueError("Calibre could not convert this ebook to the selected format.")
        if destination.stat().st_size > MAX_CONVERSION_OUTPUT_BYTES:
            raise ValueError("The converted output exceeds the 200 MB per-job limit.")
        output = destination.read_bytes()
        if not output:
            raise ValueError("Calibre produced an empty output file.")
        return output


def _convert_one(
    filename: str, data: bytes, source_format: str, target_format: str
) -> tuple[list[tuple[str, bytes]], int]:
    stem = _safe_stem(filename)
    if source_format == "pdf":
        if target_format == "cbz":
            pages = _pdf_to_images(data, "png", stem)
            return [(f"{stem}.cbz", _make_zip(pages))], len(pages)
        pages = _pdf_to_images(data, target_format, stem)
        return pages, len(pages)
    if source_format == "svg":
        suffix = "jpg" if target_format == "jpeg" else target_format
        return [(f"{stem}.{suffix}", _convert_svg(data, target_format))], 0
    if source_format in IMAGE_FORMATS:
        frames = _image_frames(data, filename)
        if target_format == "pdf":
            return [], len(frames)
        converted = []
        save_format = {
            "jpg": "JPEG", "jpeg": "JPEG", "tif": "TIFF", "tiff": "TIFF",
        }.get(target_format, target_format.upper())
        ext = "jpg" if target_format == "jpeg" else target_format
        for index, frame in enumerate(frames, start=1):
            image = frame.convert("RGB") if save_format in {"JPEG", "BMP"} else frame
            output = io.BytesIO()
            image.save(output, format=save_format)
            suffix = "" if len(frames) == 1 else f"-page-{index:04d}"
            converted.append((f"{stem}{suffix}.{ext}", output.getvalue()))
        return converted, len(frames)
    if source_format in COMIC_FORMATS:
        pages = _archive_images(filename, data, source_format)
        if target_format == "pdf":
            return [], len(pages)
        return [(f"{stem}.cbz", _make_zip(pages))], len(pages)
    if source_format in EMAIL_FORMATS:
        return [(f"{stem}.pdf", _email_pdf(filename, data, source_format))], 0
    if source_format in EBOOK_FORMATS:
        return [(f"{stem}.{target_format}", _run_calibre(data, source_format, target_format))], 0
    if any(source_format in family for family in _OFFICE_FAMILIES):
        with tempfile.TemporaryDirectory(prefix="pdf-workplace-office-") as directory:
            working_dir = Path(directory)
            source_path = working_dir / f"source.{source_format}"
            source_path.write_bytes(data)
            return [
                (f"{stem}.{target_format}", _run_office(source_path, target_format, working_dir))
            ], 0
    raise ValueError("The selected source and target formats are not supported.")


def _make_zip(files: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        used_names: set[str] = set()
        for filename, data in files:
            safe_name = Path(filename).name
            stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
            index = 2
            while safe_name.casefold() in used_names:
                safe_name = f"{stem}-{index}{suffix}"
                index += 1
            used_names.add(safe_name.casefold())
            archive.writestr(safe_name, data)
    return output.getvalue()


def _pdf_page_count(data: bytes) -> int:
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except pymupdf.FileDataError as exc:
        raise ValueError("The conversion engine produced an unreadable PDF.") from exc
    try:
        if not 1 <= document.page_count <= MAX_PDF_PAGES:
            raise ValueError(f"Converted PDFs must contain 1 to {MAX_PDF_PAGES} pages.")
        return document.page_count
    finally:
        document.close()


def convert_documents(
    documents: list[tuple[str, bytes]], source_format: str, target_format: str
) -> tuple[bytes, str, int]:
    source_format = source_format.strip().lower()
    target_format = target_format.strip().lower()
    if source_format not in SUPPORTED_CONVERSION_SOURCES:
        raise ValueError("Choose a supported source format.")
    if target_format not in conversion_targets(source_format):
        raise ValueError("This source and target format combination is not supported.")
    if not documents:
        raise ValueError("Upload at least one file to convert.")
    if len(documents) > 20:
        raise ValueError("Convert no more than 20 files at a time.")
    if sum(len(data) for _, data in documents) > 100 * 1024 * 1024:
        raise ValueError("The total upload must be no larger than 100 MB.")
    for filename, data in documents:
        _validate_signature(filename, data, source_format)

    if target_format == "pdf" and source_format in IMAGE_FORMATS | COMIC_FORMATS:
        image_inputs = []
        total_pixels = 0
        total_image_bytes = 0
        for filename, data in documents:
            stem = _safe_stem(filename)
            if source_format in IMAGE_FORMATS:
                pages = []
                for index, frame in enumerate(_image_frames(data, filename), start=1):
                    total_pixels += frame.width * frame.height
                    if total_pixels > MAX_CONVERSION_PIXELS:
                        raise ValueError("Image-to-PDF conversion is limited to 50 million pixels per job.")
                    pages.append((f"{stem}-page-{index:04d}.png", _png_bytes(frame)))
            else:
                pages = _archive_images(filename, data, source_format)
                for _, page_data in pages:
                    with Image.open(io.BytesIO(page_data)) as image:
                        total_pixels += image.width * image.height
                if total_pixels > MAX_CONVERSION_PIXELS:
                    raise ValueError("Comic-to-PDF conversion is limited to 50 million pixels per job.")
            image_inputs.extend(pages)
            total_image_bytes += sum(len(page_data) for _, page_data in pages)
            if total_image_bytes > 100 * 1024 * 1024:
                raise ValueError("The converted images exceed the 100 MB processing limit.")
        content, _, page_count = _images_to_pdf(image_inputs)
        if len(content) > MAX_CONVERSION_OUTPUT_BYTES:
            raise ValueError("The converted output exceeds the 200 MB per-job limit.")
        output_name = f"{_safe_stem(documents[0][0])}.pdf" if len(documents) == 1 else "converted-images.pdf"
        return content, output_name, page_count

    converted_files: list[tuple[str, bytes]] = []
    page_count = 0
    for filename, data in documents:
        outputs, converted_pages = _convert_one(filename, data, source_format, target_format)
        page_count += converted_pages
        if target_format == "pdf":
            page_count += sum(
                _pdf_page_count(result)
                for output_name, result in outputs
                if output_name.lower().endswith(".pdf")
            )
        converted_files.extend(outputs)
        if sum(len(result) for _, result in converted_files) > MAX_CONVERSION_OUTPUT_BYTES:
            raise ValueError("The converted output exceeds the 200 MB per-job limit.")

    if len(converted_files) == 1:
        filename, content = converted_files[0]
        if len(content) > MAX_CONVERSION_OUTPUT_BYTES:
            raise ValueError("The converted output exceeds the 200 MB per-job limit.")
        return content, filename, page_count
    archive = _make_zip(converted_files)
    if len(archive) > MAX_CONVERSION_OUTPUT_BYTES:
        raise ValueError("The converted output exceeds the 200 MB per-job limit.")
    return archive, f"converted-{target_format}.zip", page_count
