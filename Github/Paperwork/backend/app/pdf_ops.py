import io
import json
import math
import os
import re
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pymupdf
import numpy as np
from pypdf import PasswordType, PdfReader, PdfWriter
from pypdf.constants import UserAccessPermissions
from pypdf.errors import PdfReadError, PdfStreamError
from PIL import Image, ImageEnhance, ImageOps
from reportlab.lib.colors import Color
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

try:
    import cv2
except ImportError:
    cv2 = None


MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
MAX_TOTAL_UPLOAD_BYTES = int(os.getenv("MAX_TOTAL_UPLOAD_BYTES", str(100 * 1024 * 1024)))
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "500"))
MAX_RENDERED_PIXELS = 50_000_000
MAX_IMAGE_PIXELS = 40_000_000


def _require_vision_backend():
    if cv2 is None:
        raise ValueError(
            "Scanned-photo splitting and automatic form detection require the optional "
            "OpenCV package. Rebuild with INSTALL_VISION=true."
        )
    return cv2


SUPPORTED_OPERATIONS = {
    "merge",
    "split",
    "crop",
    "rotate",
    "reorder",
    "extract",
    "compress",
    "scale_pages",
    "booklet_imposition",
    "poster_pdf",
    "watermark",
    "stamp",
    "redact",
    "sanitize",
    "flatten",
    "images_to_pdf",
    "pdf_to_images",
    "convert",
    "protect",
    "unlock",
    "unlock_forms",
    "change_permissions",
    "remove_pages",
    "remove_blank_pages",
    "remove_annotations",
    "remove_images",
    "remove_password",
    "remove_certificate_sign",
    "auto_form_detection",
    "auto_rename",
    "automate",
    "add_text",
    "add_image",
    "annotate",
    "form_editor",
    "adjust_colors",
    "repair",
    "split_scanned_photos",
    "overlay",
    "color_transform",
}


def _validate_uploads(documents: Sequence[tuple[str, bytes]]) -> None:
    if not documents:
        raise ValueError("Upload at least one file.")
    if len(documents) > 20:
        raise ValueError("Upload no more than 20 files at a time.")
    total_size = sum(len(data) for _, data in documents)
    if any(len(data) > MAX_UPLOAD_BYTES for _, data in documents):
        raise ValueError(f"Each file must be no larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if total_size > MAX_TOTAL_UPLOAD_BYTES:
        raise ValueError(
            f"The total upload must be no larger than {MAX_TOTAL_UPLOAD_BYTES // (1024 * 1024)} MB."
        )


def _readers(
    documents: Sequence[tuple[str, bytes]],
    password: str = "",
    *,
    require_owner_password: bool = False,
) -> list[PdfReader]:
    _validate_uploads(documents)
    readers: list[PdfReader] = []
    try:
        for _, data in documents:
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                password_type = reader.decrypt(password)
                if not password_type:
                    raise ValueError("The PDF password is missing or incorrect.")
                if require_owner_password and password_type != PasswordType.OWNER_PASSWORD:
                    raise ValueError("Enter the PDF owner password to change its permissions.")
            if len(reader.pages) == 0:
                raise ValueError("Each PDF must contain at least one page.")
            readers.append(reader)
    except (PdfReadError, PdfStreamError) as exc:
        raise ValueError("One of the uploaded files is not a readable PDF.") from exc

    if sum(len(reader.pages) for reader in readers) > MAX_PDF_PAGES:
        raise ValueError(f"The combined document must not exceed {MAX_PDF_PAGES} pages.")
    return readers


def _parse_pages(value: str, page_count: int) -> list[int]:
    pages: list[int] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            raise ValueError("Page selections cannot contain empty entries.")
        match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", token)
        if match is None:
            raise ValueError(f"Invalid page selection: {token}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start or end > page_count:
            raise ValueError(f"Page range '{token}' must be between 1 and {page_count}.")
        pages.extend(range(start, end + 1))
    if len(set(pages)) != len(pages):
        raise ValueError("Page selections cannot contain duplicates.")
    return pages


def _writer_bytes(writer: PdfWriter) -> bytes:
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _single_pdf(pages: Sequence, selection: Sequence[int] | None = None) -> bytes:
    writer = PdfWriter()
    selected_pages = pages if selection is None else [pages[index - 1] for index in selection]
    for page in selected_pages:
        writer.add_page(page)
    return _writer_bytes(writer)


def _zip_split_pages(pages: Sequence, groups: Sequence[Sequence[int]]) -> bytes:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as result:
        for index, group in enumerate(groups, start=1):
            result.writestr(f"part-{index}.pdf", _single_pdf(pages, group))
    return archive.getvalue()


def _page_groups(page_count: int, pages_per_group: int) -> list[list[int]]:
    return [
        list(range(start, min(start + pages_per_group, page_count + 1)))
        for start in range(1, page_count + 1, pages_per_group)
    ]


def _chapter_groups(reader: PdfReader, document_bytes: bytes, mode: str) -> list[list[int]]:
    headings: list[tuple[int, str]] = []
    try:
        document = pymupdf.open(stream=document_bytes, filetype="pdf")
    except pymupdf.FileDataError as exc:
        raise ValueError("The uploaded file is not a readable PDF.") from exc
    try:
        for level, title, page_number in document.get_toc(simple=True):
            if (mode == "chapters" and level == 1) or (mode == "sections" and level == 2):
                headings.append((page_number, title))
    finally:
        document.close()

    if not headings:
        pattern = (
            re.compile(r"^\s*chapter\s+[\w.-]+", re.IGNORECASE)
            if mode == "chapters"
            else re.compile(r"^\s*(?:section\s+)?\d+(?:\.\d+)+\b", re.IGNORECASE)
        )
        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if any(pattern.search(line) for line in text.splitlines()[:8]):
                headings.append((page_number, ""))
    starts = sorted({1, *(page for page, _ in headings if 1 <= page <= len(reader.pages))})
    if len(starts) < 2:
        label = "chapter" if mode == "chapters" else "section"
        raise ValueError(f"No {label} boundaries were found in the PDF.")
    return [
        list(range(start, (starts[index + 1] if index + 1 < len(starts) else len(reader.pages) + 1)))
        for index, start in enumerate(starts)
    ]


def _split_by_size(pages: Sequence, max_bytes: int) -> list[bytes]:
    chunks: list[bytes] = []
    current: list[int] = []
    for page_number in range(1, len(pages) + 1):
        candidate = _single_pdf(pages, [*current, page_number])
        if len(candidate) <= max_bytes:
            current.append(page_number)
            continue
        if not current:
            raise ValueError(
                f"Page {page_number} is larger than the requested maximum chunk size."
            )
        chunks.append(_single_pdf(pages, current))
        current = [page_number]
        if len(_single_pdf(pages, current)) > max_bytes:
            raise ValueError(
                f"Page {page_number} is larger than the requested maximum chunk size."
            )
    if current:
        chunks.append(_single_pdf(pages, current))
    return chunks


def _divider_groups(pages: Sequence, marker: str) -> list[list[int]]:
    if not marker:
        raise ValueError("Enter text that identifies each divider page.")
    groups: list[list[int]] = []
    current: list[int] = []
    for page_number, page in enumerate(pages, start=1):
        text = (page.extract_text() or "").casefold()
        if marker.casefold() in text:
            if current:
                groups.append(current)
                current = []
        else:
            current.append(page_number)
    if current:
        groups.append(current)
    if not groups or all(len(group) == len(pages) for group in groups):
        raise ValueError("No pages containing the divider text were found.")
    return groups


def _printable_chunks(document_bytes: bytes) -> tuple[bytes, int]:
    source = pymupdf.open(stream=document_bytes, filetype="pdf")
    output = pymupdf.open()
    try:
        tile_width, tile_height = 595.0, 842.0
        for source_page in source:
            page_rect = source_page.rect
            if page_rect.is_empty or page_rect.is_infinite:
                raise ValueError(
                    "Printable chunks cannot be created from a page with invalid dimensions."
                )
            for y in range(0, math.ceil(page_rect.height), int(tile_height)):
                for x in range(0, math.ceil(page_rect.width), int(tile_width)):
                    clip = pymupdf.Rect(
                        x,
                        y,
                        min(x + tile_width, page_rect.width),
                        min(y + tile_height, page_rect.height),
                    )
                    page = output.new_page(width=clip.width, height=clip.height)
                    page.show_pdf_page(page.rect, source, source_page.number, clip=clip)
                    if output.page_count > MAX_PDF_PAGES:
                        raise ValueError(
                            f"Printable chunks cannot exceed {MAX_PDF_PAGES} pages."
                        )
        return output.tobytes(garbage=4, deflate=True, clean=True), output.page_count
    finally:
        output.close()
        source.close()


def _watermark_page(width: float, height: float, text: str) -> PdfReader:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.saveState()
    pdf.setFillColor(Color(0.35, 0.35, 0.35, alpha=0.28))
    pdf.setFont("Helvetica-Bold", min(40, max(16, width / 14)))
    pdf.translate(width / 2, height / 2)
    pdf.rotate(35)
    pdf.drawCentredString(0, 0, text[:120])
    pdf.restoreState()
    pdf.save()
    buffer.seek(0)
    return PdfReader(buffer)


def _stamp_page(
    width: float,
    height: float,
    text: str,
    image_data: bytes | None,
    position: str,
) -> PdfReader:
    has_text = bool(text.strip())
    if not has_text and image_data is None:
        raise ValueError("Enter stamp text, upload a stamp image, or provide both.")
    margin = min(24, min(width, height) * 0.05)
    if has_text and image_data:
        stamp_width, stamp_height = min(280, width * 0.72), min(72, height * 0.3)
    elif image_data:
        stamp_width, stamp_height = min(140, width * 0.3), min(110, height * 0.35)
    else:
        stamp_width, stamp_height = min(260, width * 0.72), min(58, height * 0.25)
    positions = {
        "center": ((width - stamp_width) / 2, (height - stamp_height) / 2),
        "top_left": (margin, height - stamp_height - margin),
        "top_right": (width - stamp_width - margin, height - stamp_height - margin),
        "bottom_left": (margin, margin),
        "bottom_right": (width - stamp_width - margin, margin),
    }
    if position not in positions:
        raise ValueError("Choose a supported stamp position.")
    x, y = positions[position]

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.setStrokeColor(Color(0.72, 0.12, 0.18, alpha=0.9))
    pdf.setFillColor(Color(1, 1, 1, alpha=0.84))
    pdf.roundRect(x, y, stamp_width, stamp_height, 7, stroke=1, fill=1)
    if image_data:
        image_width = min(stamp_height - 12, stamp_width * (0.32 if has_text else 0.8))
        image_x = x + 6
        image_y = y + (stamp_height - image_width) / 2
        try:
            pdf.drawImage(
                ImageReader(io.BytesIO(image_data)),
                image_x,
                image_y,
                width=image_width,
                height=image_width,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )
        except (OSError, ValueError) as exc:
            raise ValueError("The stamp image must be a readable PNG or JPEG.") from exc
    if has_text:
        text_x = x + (stamp_height if image_data else 8)
        text_width = max(1, stamp_width - (text_x - x) - 8)
        pdf.setFillColor(Color(0.65, 0.08, 0.12, alpha=0.95))
        pdf.setFont("Helvetica-Bold", min(24, max(10, stamp_height * 0.36)))
        pdf.drawCentredString(text_x + text_width / 2, y + stamp_height * 0.42, text[:80])
    pdf.save()
    buffer.seek(0)
    return PdfReader(buffer)


def _permission_flags(value: str | bytes | None) -> UserAccessPermissions:
    if value is None:
        actions = {"print", "copy", "edit", "forms"}
    else:
        raw_actions = value.decode("utf-8") if isinstance(value, bytes) else value
        actions = {action.strip() for action in raw_actions.split(",") if action.strip()}
    permission_map = {
        "print": UserAccessPermissions.PRINT | UserAccessPermissions.PRINT_TO_REPRESENTATION,
        "copy": UserAccessPermissions.EXTRACT | UserAccessPermissions.EXTRACT_TEXT_AND_GRAPHICS,
        "edit": (
            UserAccessPermissions.MODIFY
            | UserAccessPermissions.ADD_OR_MODIFY
            | UserAccessPermissions.ASSEMBLE_DOC
        ),
        "forms": UserAccessPermissions.FILL_FORM_FIELDS,
    }
    unknown = actions - set(permission_map)
    if unknown:
        raise ValueError("Choose valid PDF permissions.")
    flags = UserAccessPermissions(0)
    for action in actions:
        flags |= permission_map[action]
    return flags


def _validate_password(password: str, label: str, *, required: bool = True) -> None:
    password_bytes = password.encode("utf-8")
    if not password_bytes and not required:
        return
    if len(password_bytes) < 8 or len(password_bytes) > 127:
        raise ValueError(f"{label} must be between 8 and 127 UTF-8 bytes.")


def _validate_stamp_image(image_data: bytes, image_type: str) -> None:
    if image_type not in {"png", "jpg", "jpeg"}:
        raise ValueError("Use a PNG or JPEG stamp image.")
    try:
        image = pymupdf.open(stream=image_data, filetype=image_type)
    except pymupdf.FileDataError as exc:
        raise ValueError("The stamp image must be a readable PNG or JPEG.") from exc
    try:
        if image.page_count != 1:
            raise ValueError("Choose a single-page stamp image.")
        page = image[0]
        if (
            page.rect.is_empty
            or page.rect.is_infinite
            or page.rect.width * page.rect.height > MAX_IMAGE_PIXELS
        ):
            raise ValueError("The stamp image exceeds the image dimension limit.")
    finally:
        image.close()


def _flatten_pdf(document_bytes: bytes, mode: str) -> tuple[bytes, int]:
    if mode not in {"forms_annotations", "forms_only"}:
        raise ValueError("Choose a supported flatten method.")
    try:
        document = pymupdf.open(stream=document_bytes, filetype="pdf")
    except pymupdf.FileDataError as exc:
        raise ValueError("The uploaded file is not a readable PDF.") from exc
    try:
        items = []
        scale = 2
        rendered_pixels = 0
        for page in document:
            widgets = list(page.widgets() or [])
            annotations = list(page.annots() or []) if mode == "forms_annotations" else []
            for kind, entries in (("widget", widgets), ("annotation", annotations)):
                for entry in entries:
                    rect = entry.rect & page.rect
                    if rect.is_empty or rect.is_infinite:
                        raise ValueError("An annotation has an invalid appearance area.")
                    rendered_pixels += math.ceil(rect.width * scale) * math.ceil(
                        rect.height * scale
                    )
                    items.append((page, kind, entry, rect))
        if rendered_pixels > MAX_RENDERED_PIXELS:
            raise ValueError("Flattening exceeds the PDF rendering limit.")
        for page, kind, entry, rect in items:
            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(scale, scale),
                clip=rect,
                alpha=False,
                annots=True,
            )
            page.insert_image(
                rect,
                stream=pixmap.tobytes("png"),
                overlay=True,
                keep_proportion=False,
            )
            if kind == "widget":
                page.delete_widget(entry)
            else:
                page.delete_annot(entry)
        return document.tobytes(garbage=4, deflate=True, clean=True), document.page_count
    except pymupdf.FileDataError as exc:
        raise ValueError("The uploaded file is not a readable PDF.") from exc
    finally:
        document.close()


def _unlock_pdf_forms(document_bytes: bytes) -> tuple[bytes, int]:
    try:
        document = pymupdf.open(stream=document_bytes, filetype="pdf")
    except pymupdf.FileDataError as exc:
        raise ValueError("The uploaded file is not a readable PDF.") from exc
    try:
        fields = 0
        unlocked = 0
        for page in document:
            for widget in list(page.widgets() or []):
                fields += 1
                if widget.field_flags & pymupdf.PDF_FIELD_IS_READ_ONLY:
                    widget.field_flags &= ~pymupdf.PDF_FIELD_IS_READ_ONLY
                    widget.update()
                    unlocked += 1
        if fields == 0:
            raise ValueError("No PDF form fields were found.")
        if unlocked == 0:
            raise ValueError("No read-only PDF form fields were found.")
        return document.tobytes(garbage=4, deflate=True, clean=True), document.page_count
    except pymupdf.FileDataError as exc:
        raise ValueError("The uploaded file is not a readable PDF.") from exc
    finally:
        document.close()


def _sanitize_pdf(document_bytes: bytes) -> tuple[bytes, int]:
    try:
        document = pymupdf.open(stream=document_bytes, filetype="pdf")
    except pymupdf.FileDataError as exc:
        raise ValueError("The uploaded file is not a readable PDF.") from exc
    try:
        document.scrub(
            attached_files=True,
            clean_pages=True,
            embedded_files=True,
            hidden_text=True,
            javascript=True,
            metadata=True,
            redactions=True,
            remove_links=True,
            reset_fields=True,
            reset_responses=True,
            thumbnails=True,
            xml_metadata=True,
        )
        for page in document:
            for annotation in list(page.annots() or []):
                page.delete_annot(annotation)
        return document.tobytes(garbage=4, deflate=True, clean=True), document.page_count
    except pymupdf.FileDataError as exc:
        raise ValueError("The uploaded file is not a readable PDF.") from exc
    finally:
        document.close()


def _remove_pages(document_bytes: bytes, selection: str) -> tuple[bytes, int]:
    reader = PdfReader(io.BytesIO(document_bytes), strict=True)
    selected = set(_parse_pages(selection, len(reader.pages)))
    retained = [index for index in range(1, len(reader.pages) + 1) if index not in selected]
    if not retained:
        raise ValueError("At least one page must remain in the PDF.")
    return _single_pdf(reader.pages, retained), len(retained)


def _page_is_blank(page: pymupdf.Page) -> bool:
    if list(page.widgets() or []) or list(page.annots() or []):
        return False
    scale = 0.35
    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(scale, scale),
        colorspace=pymupdf.csGRAY,
        alpha=False,
    )
    pixels = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.stride
    )[:, : pixmap.width]
    nonwhite = int(np.count_nonzero(pixels < 245))
    return nonwhite <= max(4, int(pixels.size * 0.001))


def _remove_blank_pages(document_bytes: bytes) -> tuple[bytes, int]:
    document = pymupdf.open(stream=document_bytes, filetype="pdf")
    output = pymupdf.open()
    try:
        retained = [
            index for index, page in enumerate(document) if not _page_is_blank(page)
        ]
        if not retained:
            raise ValueError("All pages appear blank; no output PDF was produced.")
        if len(retained) == document.page_count:
            raise ValueError("No blank pages were found.")
        for index in retained:
            output.insert_pdf(document, from_page=index, to_page=index)
        return output.tobytes(garbage=4, deflate=True, clean=True), output.page_count
    finally:
        output.close()
        document.close()


def _remove_annotations(document_bytes: bytes) -> tuple[bytes, int]:
    document = pymupdf.open(stream=document_bytes, filetype="pdf")
    try:
        removed = 0
        for page in document:
            annotations = list(page.annots() or [])
            for annotation in annotations:
                page.delete_annot(annotation)
            removed += len(annotations)
        if not removed:
            raise ValueError("No annotations were found.")
        return document.tobytes(garbage=4, deflate=True, clean=True), document.page_count
    finally:
        document.close()


def _remove_images(document_bytes: bytes) -> tuple[bytes, int]:
    document = pymupdf.open(stream=document_bytes, filetype="pdf")
    try:
        image_references = {
            image[0]
            for page in document
            for image in page.get_images(full=True)
            if image[0] > 0
        }
        if not image_references:
            raise ValueError("No embedded images were found.")
        for page in document:
            page_references = {
                image[0] for image in page.get_images(full=True) if image[0] in image_references
            }
            for reference in page_references:
                page.delete_image(reference)
                image_references.discard(reference)
        return document.tobytes(garbage=4, deflate=True, clean=True), document.page_count
    finally:
        document.close()


def _remove_certificate_signatures(document_bytes: bytes) -> tuple[bytes, int]:
    document = pymupdf.open(stream=document_bytes, filetype="pdf")
    try:
        removed = 0
        for page in document:
            widgets = list(page.widgets() or [])
            for widget in widgets:
                if widget.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
                    page.delete_widget(widget)
                    removed += 1
        if not removed:
            raise ValueError("No certificate signature fields were found.")
        return document.tobytes(garbage=4, deflate=True, clean=True), document.page_count
    finally:
        document.close()


def _repair_pdf(document_bytes: bytes) -> tuple[bytes, int]:
    try:
        document = pymupdf.open(stream=document_bytes, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise ValueError("The PDF could not be repaired.") from exc
    try:
        if not document.page_count:
            raise ValueError("The PDF has no recoverable pages.")
        repaired = document.tobytes(garbage=4, deflate=True, clean=True)
        return repaired, document.page_count
    finally:
        document.close()


def _auto_rename(document_bytes: bytes) -> tuple[bytes, str, int]:
    reader = PdfReader(io.BytesIO(document_bytes), strict=True)
    title = str((reader.metadata or {}).get("/Title") or "").strip()
    if title.casefold() in {"untitled", "unknown"}:
        title = ""
    if not title:
        for page in reader.pages:
            lines = (page.extract_text() or "").splitlines()
            title = next((line.strip() for line in lines if line.strip()), "")
            if title:
                break
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip(".-_")[:80]
    return document_bytes, f"{stem or 'document'}.pdf", len(reader.pages)


def _parse_hex_color(value: str, label: str) -> tuple[float, float, float]:
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError(f"{label} must be a six-digit hex color such as #336699.")
    return tuple(int(value[index : index + 2], 16) / 255 for index in (1, 3, 5))


def _insert_text(
    document_bytes: bytes, options: dict[str, str | bytes], *, annotation: bool = False
) -> tuple[bytes, int]:
    document = pymupdf.open(stream=document_bytes, filetype="pdf")
    try:
        text = str(options.get("annotation_text" if annotation else "text", "")).strip()
        if not text or len(text) > 2000:
            raise ValueError("Enter text between 1 and 2000 characters.")
        try:
            page_number = int(options.get("page_number", "1"))
            x = float(options.get("x", "72"))
            y = float(options.get("y", "72"))
            font_size = float(options.get("font_size", "12"))
        except ValueError as exc:
            raise ValueError("Enter valid page, position, and font-size values.") from exc
        if not 1 <= page_number <= document.page_count:
            raise ValueError(f"Page must be between 1 and {document.page_count}.")
        if not all(math.isfinite(value) for value in (x, y, font_size)):
            raise ValueError("Position and font size must be finite numbers.")
        page = document[page_number - 1]
        if x < 0 or y < 0 or x > page.rect.width or y > page.rect.height:
            raise ValueError("The selected text position is outside the page.")
        if annotation:
            page.add_text_annot((x, y), text)
        else:
            if not 4 <= font_size <= 96:
                raise ValueError("Font size must be between 4 and 96 points.")
            color = _parse_hex_color(str(options.get("text_color", "#222222")), "Text color")
            page.insert_text((x, y), text, fontsize=font_size, color=color)
        return document.tobytes(garbage=4, deflate=True, clean=True), document.page_count
    finally:
        document.close()


def _insert_image(
    document_bytes: bytes, image_data: bytes, image_type: str, options: dict[str, str | bytes]
) -> tuple[bytes, int]:
    _validate_stamp_image(image_data, image_type)
    document = pymupdf.open(stream=document_bytes, filetype="pdf")
    try:
        try:
            page_number = int(options.get("page_number", "1"))
            x = float(options.get("x", "72"))
            y = float(options.get("y", "72"))
            width = float(options.get("width", "144"))
            height = float(options.get("height", "100"))
        except ValueError as exc:
            raise ValueError("Enter valid page, position, width, and height values.") from exc
        if not 1 <= page_number <= document.page_count:
            raise ValueError(f"Page must be between 1 and {document.page_count}.")
        if not all(math.isfinite(value) for value in (x, y, width, height)):
            raise ValueError("Image dimensions and position must be finite numbers.")
        page = document[page_number - 1]
        rectangle = pymupdf.Rect(x, y, x + width, y + height)
        if width <= 0 or height <= 0 or rectangle != rectangle & page.rect:
            raise ValueError("The image must fit inside the selected page.")
        page.insert_image(rectangle, stream=image_data, keep_proportion=True)
        return document.tobytes(garbage=4, deflate=True, clean=True), document.page_count
    finally:
        document.close()


def _render_scale(document: pymupdf.Document, preferred: float = 1.5) -> float:
    page_area = sum(page.rect.width * page.rect.height for page in document)
    if page_area <= 0:
        raise ValueError("The PDF has invalid page dimensions.")
    scale = min(preferred, math.sqrt(MAX_RENDERED_PIXELS / page_area))
    while sum(
        math.ceil(page.rect.width * scale) * math.ceil(page.rect.height * scale)
        for page in document
    ) > MAX_RENDERED_PIXELS:
        scale *= 0.99
    if scale < 0.5:
        raise ValueError("This PDF is too large to render at a useful resolution.")
    return scale


def _raster_transform(
    document_bytes: bytes, transform
) -> tuple[bytes, int]:
    document = pymupdf.open(stream=document_bytes, filetype="pdf")
    output = pymupdf.open()
    try:
        scale = _render_scale(document, 120 / 72)
        for source_page in document:
            pixmap = source_page.get_pixmap(
                matrix=pymupdf.Matrix(scale, scale),
                colorspace=pymupdf.csRGB,
                alpha=False,
            )
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            transformed = transform(image)
            page = output.new_page(width=source_page.rect.width, height=source_page.rect.height)
            page.insert_image(page.rect, stream=_image_png_bytes(transformed))
        return output.tobytes(garbage=4, deflate=True, clean=True), output.page_count
    finally:
        output.close()
        document.close()


def _image_png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _adjust_colors(document_bytes: bytes, options: dict[str, str | bytes]) -> tuple[bytes, int]:
    try:
        brightness = float(options.get("brightness", "1"))
        contrast = float(options.get("contrast", "1"))
    except ValueError as exc:
        raise ValueError("Brightness and contrast must be numbers.") from exc
    if (
        not math.isfinite(brightness)
        or not math.isfinite(contrast)
        or not 0.1 <= brightness <= 3
        or not 0.1 <= contrast <= 3
    ):
        raise ValueError("Brightness and contrast must be between 0.1 and 3.")

    def transform(image: Image.Image) -> Image.Image:
        image = ImageEnhance.Brightness(image).enhance(brightness)
        return ImageEnhance.Contrast(image).enhance(contrast)

    return _raster_transform(document_bytes, transform)


def _replace_or_invert_color(
    document_bytes: bytes, options: dict[str, str | bytes]
) -> tuple[bytes, int]:
    mode = str(options.get("color_mode", "invert"))
    if mode == "invert":
        return _raster_transform(document_bytes, ImageOps.invert)
    if mode != "replace":
        raise ValueError("Choose color replacement or inversion.")
    source = str(options.get("source_color", "#000000"))
    replacement = str(options.get("replacement_color", "#FFFFFF"))
    source_rgb = np.array(
        [round(value * 255) for value in _parse_hex_color(source, "Source color")],
        dtype=np.int16,
    )
    replacement_rgb = tuple(
        round(value * 255) for value in _parse_hex_color(replacement, "Replacement color")
    )
    try:
        tolerance = int(options.get("color_tolerance", "16"))
    except ValueError as exc:
        raise ValueError("Color tolerance must be an integer from 0 to 255.") from exc
    if not 0 <= tolerance <= 255:
        raise ValueError("Color tolerance must be an integer from 0 to 255.")

    def transform(image: Image.Image) -> Image.Image:
        pixels = np.asarray(image).copy()
        mask = np.max(np.abs(pixels.astype(np.int16) - source_rgb), axis=2) <= tolerance
        pixels[mask] = replacement_rgb
        return Image.fromarray(pixels, mode="RGB")

    return _raster_transform(document_bytes, transform)


def _overlay_pdfs(documents: Sequence[tuple[str, bytes]]) -> tuple[bytes, int]:
    if len(documents) != 2:
        raise ValueError("Overlay requires a base PDF and an overlay PDF.")
    base = pymupdf.open(stream=documents[0][1], filetype="pdf")
    overlay = pymupdf.open(stream=documents[1][1], filetype="pdf")
    try:
        if overlay.page_count not in {1, base.page_count}:
            raise ValueError("The overlay PDF must have one page or match the base PDF page count.")
        for index, page in enumerate(base):
            page.show_pdf_page(
                page.rect,
                overlay,
                index if overlay.page_count > 1 else 0,
                overlay=True,
            )
        return base.tobytes(garbage=4, deflate=True, clean=True), base.page_count
    finally:
        overlay.close()
        base.close()


PAGE_SIZE_POINTS = {
    "A3": (841.89, 1190.55),
    "A4": (595.28, 841.89),
    "A5": (420.94, 595.28),
    "LETTER": (612.0, 792.0),
    "LEGAL": (612.0, 1008.0),
    "TABLOID": (792.0, 1224.0),
}


def _named_page_size(name: str) -> tuple[float, float]:
    key = (name or "").strip().upper()
    if key not in PAGE_SIZE_POINTS:
        supported = ", ".join(sorted(PAGE_SIZE_POINTS))
        raise ValueError(f"Unsupported page size '{name}'. Choose one of: {supported}.")
    return PAGE_SIZE_POINTS[key]


def _as_bool(value: str | bytes | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _scale_pages(document_bytes: bytes, options: dict[str, str | bytes]) -> tuple[bytes, int]:
    try:
        scale_factor = float(options.get("scale_factor", "1"))
    except ValueError as exc:
        raise ValueError("Enter the scale factor as a number.") from exc
    if not math.isfinite(scale_factor) or not 0.1 <= scale_factor <= 10:
        raise ValueError("Scale factor must be between 0.1 and 10.")
    page_size = str(options.get("page_size", "") or "KEEP").strip().upper() or "KEEP"
    if page_size != "KEEP":
        _named_page_size(page_size)
    source = pymupdf.open(stream=document_bytes, filetype="pdf")
    output = pymupdf.open()
    try:
        for index in range(source.page_count):
            src_page = source[index]
            src_rect = src_page.rect
            if page_size == "KEEP":
                target_w, target_h = src_rect.width, src_rect.height
            else:
                target_w, target_h = _named_page_size(page_size)
            if src_rect.width <= 0 or src_rect.height <= 0:
                raise ValueError("The source page has invalid dimensions.")
            scale = min(target_w / src_rect.width, target_h / src_rect.height) * scale_factor
            new_page = output.new_page(width=target_w, height=target_h)
            drawn_w = src_rect.width * scale
            drawn_h = src_rect.height * scale
            x = (target_w - drawn_w) / 2
            y = (target_h - drawn_h) / 2
            new_page.show_pdf_page(
                pymupdf.Rect(x, y, x + drawn_w, y + drawn_h),
                source,
                index,
                overlay=True,
            )
        return output.tobytes(garbage=4, deflate=True, clean=True), output.page_count
    finally:
        output.close()
        source.close()


def _pad_to_multiple_of_4(n: int) -> int:
    return (n + 3) // 4 * 4


def _booklet_imposition(document_bytes: bytes, options: dict[str, str | bytes]) -> tuple[bytes, int]:
    add_border = _as_bool(options.get("add_border"))
    spine_location = str(options.get("spine_location", "") or "LEFT").strip().upper() or "LEFT"
    add_gutter = _as_bool(options.get("add_gutter"))
    try:
        gutter_size = float(options.get("gutter_size", "0"))
    except ValueError as exc:
        raise ValueError("Enter the gutter size as a number.") from exc
    double_sided = _as_bool(options.get("double_sided"))
    duplex_pass = str(options.get("duplex_pass", "") or "BOTH").strip().upper() or "BOTH"
    flip_on_short_edge = _as_bool(options.get("flip_on_short_edge"))
    if spine_location not in {"LEFT", "RIGHT"}:
        raise ValueError("Choose a spine location of LEFT or RIGHT.")
    if duplex_pass not in {"BOTH", "FIRST", "SECOND"}:
        raise ValueError("Choose a duplex pass of BOTH, FIRST, or SECOND.")

    source = pymupdf.open(stream=document_bytes, filetype="pdf")
    output = pymupdf.open()
    try:
        total = source.page_count
        if total == 0:
            raise ValueError("The PDF has no pages.")
        padded = _pad_to_multiple_of_4(total)
        portrait = source[0].rect
        if portrait.width <= 0 or portrait.height <= 0:
            raise ValueError("The source page has invalid dimensions.")
        # Booklet paper is landscape so the folded result reads portrait.
        page_size = pymupdf.Rect(0, 0, portrait.height, portrait.width)
        cell_w = page_size.width / 2
        cell_h = page_size.height
        gutter_max = page_size.width / 2 - 1
        if gutter_size < 0:
            gutter_size = 0
        elif gutter_size > gutter_max:
            gutter_size = gutter_max
        include_front = duplex_pass in {"BOTH", "FIRST"}
        include_back = duplex_pass in {"BOTH", "SECOND"}
        for sheet in range(padded // 4):
            front_left = padded - 1 - sheet * 2
            front_right = sheet * 2
            back_left = sheet * 2 + 1
            back_right = padded - 2 - sheet * 2
            sides: list[tuple[int, int]] = []
            if include_front:
                sides.append((front_left, front_right))
            if include_back:
                if double_sided and flip_on_short_edge:
                    sides.append((back_right, back_left))
                else:
                    sides.append((back_left, back_right))
            rtl = spine_location == "RIGHT"
            left_col, right_col = (1, 0) if rtl else (0, 1)
            for left, right in sides:
                out_page = output.new_page(width=page_size.width, height=page_size.height)
                g = gutter_size if add_gutter else 0.0
                for page_index, column in ((left, left_col), (right, right_col)):
                    if page_index < 0 or page_index >= total:
                        continue
                    cell_x = column * cell_w + (g / 2 if column == left_col else -g / 2)
                    cell_rect = pymupdf.Rect(
                        cell_x, 0, cell_x + cell_w - g / 2, cell_h
                    )
                    out_page.show_pdf_page(
                        cell_rect,
                        source,
                        page_index,
                        overlay=True,
                    )
                    if add_border:
                        out_page.draw_rect(
                            cell_rect, color=(0, 0, 0), width=0.5, overlay=True
                        )
        return output.tobytes(garbage=4, deflate=True, clean=True), output.page_count
    finally:
        output.close()
        source.close()


def _poster_pdf(document_bytes: bytes, options: dict[str, str | bytes]) -> tuple[bytes, int]:
    page_size_name = str(options.get("page_size", "") or "A4").strip() or "A4"
    target_w, target_h = _named_page_size(page_size_name)
    try:
        x_factor = int(options.get("x_factor", "1"))
        y_factor = int(options.get("y_factor", "1"))
    except ValueError as exc:
        raise ValueError("Enter the X and Y factors of the output grid.") from exc
    if not 1 <= x_factor <= 20 or not 1 <= y_factor <= 20:
        raise ValueError("Grid factors must be between 1 and 20.")
    right_to_left = _as_bool(options.get("right_to_left"))
    source = pymupdf.open(stream=document_bytes, filetype="pdf")
    output = pymupdf.open()
    try:
        total = source.page_count
        if total * x_factor * y_factor > 100_000:
            raise ValueError("The poster grid would produce more than 100000 pages.")
        for index in range(total):
            page_rect = source[index].rect
            cell_w = page_rect.width / x_factor
            cell_h = page_rect.height / y_factor
            if cell_w <= 0 or cell_h <= 0:
                raise ValueError("The source page has invalid dimensions.")
            for row in range(y_factor):
                for col in range(x_factor):
                    actual_col = x_factor - 1 - col if right_to_left else col
                    crop = pymupdf.Rect(
                        page_rect.x0 + actual_col * cell_w,
                        page_rect.y1 - (row + 1) * cell_h,
                        page_rect.x0 + (actual_col + 1) * cell_w,
                        page_rect.y1 - row * cell_h,
                    )
                    new_page = output.new_page(width=target_w, height=target_h)
                    scale = min(target_w / cell_w, target_h / cell_h)
                    drawn_w = cell_w * scale
                    drawn_h = cell_h * scale
                    x = (target_w - drawn_w) / 2
                    y = (target_h - drawn_h) / 2
                    new_page.show_pdf_page(
                        pymupdf.Rect(x, y, x + drawn_w, y + drawn_h),
                        source,
                        index,
                        clip=crop,
                        overlay=True,
                    )
        return output.tobytes(garbage=4, deflate=True, clean=True), output.page_count
    finally:
        output.close()
        source.close()


def _get_form_fields(document_bytes: bytes) -> list[dict[str, str | int]]:
    document = pymupdf.open(stream=document_bytes, filetype="pdf")
    try:
        if document.needs_pass:
            raise ValueError("Unlock the PDF before loading its form fields.")
        fields = []
        for page_number, page in enumerate(document, start=1):
            for widget in page.widgets() or []:
                fields.append(
                    {
                        "name": widget.field_name,
                        "type": widget.field_type_string,
                        "value": str(widget.field_value or ""),
                        "page": page_number,
                    }
                )
        return fields
    finally:
        document.close()


def _edit_form_fields(
    document_bytes: bytes, values_json: str
) -> tuple[bytes, int]:
    try:
        values = json.loads(values_json)
    except json.JSONDecodeError as exc:
        raise ValueError("Form values must be valid JSON.") from exc
    if not isinstance(values, dict) or not values:
        raise ValueError("Provide a JSON object of form field names and values.")
    if len(values) > 500:
        raise ValueError("Update no more than 500 form fields at a time.")
    document = pymupdf.open(stream=document_bytes, filetype="pdf")
    try:
        seen: set[str] = set()
        updated = 0
        for page in document:
            for widget in list(page.widgets() or []):
                name = widget.field_name
                if name not in values:
                    continue
                value = values[name]
                if not isinstance(value, (str, bool, int, float)):
                    raise ValueError(f"Value for field '{name}' must be text or a boolean.")
                if isinstance(value, str) and len(value) > 10_000:
                    raise ValueError(f"Value for field '{name}' is too long.")
                if widget.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
                    widget.field_value = widget.on_state() if bool(value) else "Off"
                else:
                    widget.field_value = str(value)
                widget.update()
                seen.add(name)
                updated += 1
        unknown = set(values) - seen
        if unknown:
            raise ValueError(f"Unknown form field: {sorted(unknown)[0]}")
        if not updated:
            raise ValueError("No matching form fields were found.")
        return document.tobytes(garbage=4, deflate=True, clean=True), document.page_count
    finally:
        document.close()


def _detect_form_fields(document_bytes: bytes) -> tuple[bytes, int]:
    vision = _require_vision_backend()
    document = pymupdf.open(stream=document_bytes, filetype="pdf")
    try:
        scale = _render_scale(document, 1.5)
        total_pixels = sum(
            math.ceil(page.rect.width * scale) * math.ceil(page.rect.height * scale)
            for page in document
        )
        if total_pixels > MAX_RENDERED_PIXELS:
            raise ValueError("Form detection exceeds the PDF rendering limit.")
        created = 0
        for page_index, page in enumerate(document):
            if list(page.widgets() or []):
                continue
            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(scale, scale),
                colorspace=pymupdf.csGRAY,
                alpha=False,
            )
            gray = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.stride
            )[:, : pixmap.width]
            binary = vision.threshold(
                gray, 0, 255, vision.THRESH_BINARY_INV | vision.THRESH_OTSU
            )[1]
            kernel = vision.getStructuringElement(vision.MORPH_RECT, (3, 3))
            binary = vision.morphologyEx(binary, vision.MORPH_CLOSE, kernel)
            contours, _ = vision.findContours(
                binary, vision.RETR_LIST, vision.CHAIN_APPROX_SIMPLE
            )
            candidates = []
            for contour in contours:
                perimeter = vision.arcLength(contour, True)
                polygon = vision.approxPolyDP(contour, 0.02 * perimeter, True)
                if len(polygon) != 4 or not vision.isContourConvex(polygon):
                    continue
                x, y, width, height = vision.boundingRect(polygon)
                area_ratio = (width * height) / (pixmap.width * pixmap.height)
                aspect = width / max(1, height)
                is_checkbox = 0.001 <= area_ratio <= 0.03 and 0.8 <= aspect <= 1.2
                is_text_field = (
                    0.003 <= area_ratio <= 0.2
                    and width >= 45
                    and height >= 14
                    and 1.8 <= aspect <= 20
                )
                if is_checkbox or is_text_field:
                    candidates.append((x, y, width, height, is_checkbox, width * height))
            candidates.sort(key=lambda item: item[5], reverse=True)
            accepted: list[tuple[int, int, int, int, bool, int]] = []
            for candidate in candidates:
                x, y, width, height, _, _ = candidate
                if any(
                    abs(x - other[0]) <= 3
                    and abs(y - other[1]) <= 3
                    and abs(width - other[2]) <= 6
                    and abs(height - other[3]) <= 6
                    for other in accepted
                ):
                    continue
                accepted.append(candidate)
                if len(accepted) >= 100:
                    break
            for field_index, (x, y, width, height, is_checkbox, _) in enumerate(
                accepted, start=1
            ):
                widget = pymupdf.Widget()
                widget.field_name = f"auto_p{page_index + 1}_{field_index}"
                widget.field_type = (
                    pymupdf.PDF_WIDGET_TYPE_CHECKBOX
                    if is_checkbox
                    else pymupdf.PDF_WIDGET_TYPE_TEXT
                )
                widget.field_value = False if is_checkbox else ""
                widget.rect = pymupdf.Rect(
                    x / scale,
                    y / scale,
                    (x + width) / scale,
                    (y + height) / scale,
                )
                page.add_widget(widget)
                created += 1
        if not created:
            raise ValueError(
                "No clear rectangular form fields were detected. Add fields manually or use a PDF with visible boxes."
            )
        return document.tobytes(garbage=4, deflate=True, clean=True), document.page_count
    finally:
        document.close()


def _split_scanned_photos(document_bytes: bytes) -> tuple[bytes, int]:
    vision = _require_vision_backend()
    source = pymupdf.open(stream=document_bytes, filetype="pdf")
    output = pymupdf.open()
    try:
        scale = _render_scale(source, 1.5)
        total_pixels = sum(
            math.ceil(page.rect.width * scale) * math.ceil(page.rect.height * scale)
            for page in source
        )
        if total_pixels > MAX_RENDERED_PIXELS:
            raise ValueError("Photo detection exceeds the PDF rendering limit.")
        for page in source:
            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(scale, scale),
                colorspace=pymupdf.csGRAY,
                alpha=False,
            )
            gray = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.stride
            )[:, : pixmap.width]
            edges = vision.Canny(gray, 35, 110)
            kernel = vision.getStructuringElement(vision.MORPH_RECT, (7, 7))
            closed = vision.morphologyEx(edges, vision.MORPH_CLOSE, kernel)
            contours, _ = vision.findContours(
                closed, vision.RETR_EXTERNAL, vision.CHAIN_APPROX_SIMPLE
            )
            candidates: list[pymupdf.Rect] = []
            for contour in contours:
                perimeter = vision.arcLength(contour, True)
                polygon = vision.approxPolyDP(contour, 0.025 * perimeter, True)
                if len(polygon) != 4 or not vision.isContourConvex(polygon):
                    continue
                x, y, width, height = vision.boundingRect(polygon)
                ratio = (width * height) / (pixmap.width * pixmap.height)
                if ratio < 0.04 or ratio > 0.97 or width < 40 or height < 40:
                    continue
                candidates.append(
                    pymupdf.Rect(x / scale, y / scale, (x + width) / scale, (y + height) / scale)
                )
            candidates.sort(key=lambda rectangle: rectangle.get_area(), reverse=True)
            selected: list[pymupdf.Rect] = []
            for candidate in candidates:
                if any(
                    (candidate & existing).get_area() / candidate.get_area() > 0.85
                    for existing in selected
                ):
                    continue
                selected.append(candidate)
            if len(selected) < 2:
                image_rectangles = {
                    tuple(round(value, 1) for value in rectangle)
                    for image in page.get_images(full=True)
                    for rectangle in page.get_image_rects(image[0])
                    if not rectangle.is_empty
                }
                if len(image_rectangles) > 1:
                    selected = [pymupdf.Rect(rectangle) for rectangle in image_rectangles]
                else:
                    selected = [page.rect]
            for rectangle in selected:
                cropped = output.new_page(width=rectangle.width, height=rectangle.height)
                cropped.show_pdf_page(cropped.rect, source, page.number, clip=rectangle)
                if output.page_count > MAX_PDF_PAGES:
                    raise ValueError(
                        f"Photo splitting cannot create more than {MAX_PDF_PAGES} pages."
                    )
        return output.tobytes(garbage=4, deflate=True, clean=True), output.page_count
    finally:
        output.close()
        source.close()


def _automate_pdf(
    document_bytes: bytes, steps_json: str, options: dict[str, str | bytes]
) -> tuple[bytes, int]:
    try:
        steps = json.loads(steps_json)
    except json.JSONDecodeError as exc:
        raise ValueError("Choose valid automation steps.") from exc
    allowed = {"repair", "remove_blank_pages", "remove_annotations", "remove_images", "compress"}
    if (
        not isinstance(steps, list)
        or not steps
        or len(steps) > 5
        or any(not isinstance(step, str) or step not in allowed for step in steps)
        or len(set(steps)) != len(steps)
    ):
        raise ValueError("Choose one or more valid, non-repeating automation steps.")
    content = document_bytes
    page_count = 0
    for step in steps:
        content, _, page_count = process_pdf(
            step, [("automated.pdf", content)], options
        )
    return content, page_count


def _images_to_pdf(documents: Sequence[tuple[str, bytes]]) -> tuple[bytes, str, int]:
    _validate_uploads(documents)
    output = pymupdf.open()
    try:
        for filename, data in documents:
            extension = Path(filename).suffix.lower().lstrip(".")
            if extension not in {"png", "jpg", "jpeg"}:
                raise ValueError("Use PNG or JPEG image files.")
            try:
                image = pymupdf.open(stream=data, filetype=extension)
                try:
                    if image.page_count != 1:
                        raise ValueError(f"{filename} must contain exactly one image.")
                    image_page = image[0]
                    if image_page.rect.width * image_page.rect.height > MAX_IMAGE_PIXELS:
                        raise ValueError(f"{filename} exceeds the image dimension limit.")
                    image_pdf = image.convert_to_pdf()
                finally:
                    image.close()
            except pymupdf.FileDataError as exc:
                raise ValueError(f"{filename} is not a readable PNG or JPEG image.") from exc
            image_document = pymupdf.open(stream=image_pdf, filetype="pdf")
            try:
                output.insert_pdf(image_document)
            finally:
                image_document.close()
            if output.page_count > MAX_PDF_PAGES:
                raise ValueError(f"The combined document must not exceed {MAX_PDF_PAGES} pages.")
        return output.tobytes(garbage=4, deflate=True, clean=True), "images.pdf", output.page_count
    finally:
        output.close()


def _pdf_to_images(document: pymupdf.Document) -> tuple[bytes, str, int]:
    scale = 150 / 72
    rendered_pixels = sum(
        math.ceil(page.rect.width * scale) * math.ceil(page.rect.height * scale)
        for page in document
    )
    if rendered_pixels > MAX_RENDERED_PIXELS:
        raise ValueError("The PDF is too large to render within the image conversion limit.")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as result:
        for index, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            result.writestr(f"page-{index:03}.png", pixmap.tobytes("png"))
    return archive.getvalue(), "page-images.zip", document.page_count


def process_pdf(
    operation: str,
    documents: Sequence[tuple[str, bytes]],
    options: dict[str, str | bytes],
) -> tuple[bytes, str, int]:
    if operation not in SUPPORTED_OPERATIONS:
        raise ValueError("Unsupported PDF operation.")
    if operation == "convert":
        raise ValueError("Use the format-conversion handler for this operation.")
    if operation == "images_to_pdf":
        return _images_to_pdf(documents)
    if operation in {"repair", "automate"}:
        _validate_uploads(documents)
        if len(documents) != 1:
            raise ValueError(f"{operation.replace('_', ' ').capitalize()} requires exactly one PDF.")
        if operation == "repair":
            output, count = _repair_pdf(documents[0][1])
            return output, "repaired.pdf", count
        output, count = _automate_pdf(
            documents[0][1], str(options.get("automation_steps", "[]")), options
        )
        return output, "automated.pdf", count

    password = ""
    if operation in {"unlock", "remove_password"}:
        password = str(options.get("unlock_password", ""))
    elif operation == "unlock_forms":
        password = str(options.get("form_password", ""))
    elif operation == "change_permissions":
        password = str(options.get("source_password", ""))
    readers = _readers(
        documents,
        password=password,
        require_owner_password=operation == "change_permissions",
    )
    page_count = sum(len(reader.pages) for reader in readers)

    if operation == "overlay":
        if len(documents) != 2:
            raise ValueError("Overlay requires exactly two PDFs.")
        output, count = _overlay_pdfs(documents)
        return output, "overlay.pdf", count

    if operation == "merge":
        if len(readers) < 2:
            raise ValueError("Merge requires at least two PDFs.")
        writer = PdfWriter()
        for reader in readers:
            for page in reader.pages:
                writer.add_page(page)
        return _writer_bytes(writer), "merged.pdf", page_count

    if operation == "split" and options.get("split_mode", "pages") == "document_count":
        try:
            documents_per_group = int(options.get("split_value", ""))
        except ValueError as exc:
            raise ValueError("Enter how many PDFs to include in each output file.") from exc
        if not 1 <= documents_per_group <= 20:
            raise ValueError("Document count must be between 1 and 20.")
        if len(readers) < 2:
            raise ValueError("Document-count splitting requires at least two PDFs.")
        all_pages = [page for item in readers for page in item.pages]
        page_offsets = [0]
        for item in readers:
            page_offsets.append(page_offsets[-1] + len(item.pages))
        groups = [
            list(range(start, min(start + documents_per_group, len(readers))))
            for start in range(0, len(readers), documents_per_group)
        ]
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as result:
            for index, document_group in enumerate(groups, start=1):
                selected_pages = [
                    page_number
                    for document_index in document_group
                    for page_number in range(
                        page_offsets[document_index] + 1,
                        page_offsets[document_index + 1] + 1,
                    )
                ]
                result.writestr(
                    f"part-{index}.pdf",
                    _single_pdf(all_pages, selected_pages),
                )
        return archive.getvalue(), "split-documents.zip", page_count

    if len(readers) != 1:
        raise ValueError(f"{operation.capitalize()} requires exactly one PDF.")
    reader = readers[0]
    pages = reader.pages

    if operation == "split":
        mode = options.get("split_mode", "pages")
        if mode == "pages":
            groups = [
                part.strip()
                for part in options.get("groups", "").split(";")
                if part.strip()
            ]
            if not groups:
                raise ValueError(
                    "Enter page ranges separated by semicolons, for example 1-3;4-6."
                )
            selected_groups = [_parse_pages(group, len(pages)) for group in groups]
            return _zip_split_pages(pages, selected_groups), "split-pages.zip", page_count

        if mode in {"chapters", "sections"}:
            selected_groups = _chapter_groups(reader, documents[0][1], mode)
            return (
                _zip_split_pages(pages, selected_groups),
                f"split-{mode}.zip",
                page_count,
            )

        if mode == "file_size":
            try:
                max_size_mb = float(options.get("split_value", ""))
            except ValueError as exc:
                raise ValueError("Enter a maximum file size in megabytes.") from exc
            if not math.isfinite(max_size_mb) or not 0.1 <= max_size_mb <= 100:
                raise ValueError("Maximum file size must be between 0.1 and 100 MB.")
            chunks = _split_by_size(pages, int(max_size_mb * 1024 * 1024))
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as result:
                for index, content in enumerate(chunks, start=1):
                    result.writestr(f"part-{index}.pdf", content)
            return archive.getvalue(), "split-file-size.zip", page_count

        if mode == "page_count":
            try:
                pages_per_group = int(options.get("split_value", ""))
            except ValueError as exc:
                raise ValueError("Enter the number of pages per output file.") from exc
            if not 1 <= pages_per_group <= MAX_PDF_PAGES:
                raise ValueError(f"Page count must be between 1 and {MAX_PDF_PAGES}.")
            return (
                _zip_split_pages(pages, _page_groups(len(pages), pages_per_group)),
                "split-page-count.zip",
                page_count,
            )

        if mode == "page_divider":
            selected_groups = _divider_groups(pages, options.get("divider_text", "").strip())
            return (
                _zip_split_pages(pages, selected_groups),
                "split-page-divider.zip",
                page_count,
            )

        if mode == "printable_chunks":
            try:
                output, output_pages = _printable_chunks(documents[0][1])
            except pymupdf.FileDataError as exc:
                raise ValueError("The uploaded file is not a readable PDF.") from exc
            return output, "printable-chunks.pdf", output_pages

        raise ValueError("Choose a supported split method.")

    if operation == "crop":
        try:
            crop_x = float(options.get("crop_x", ""))
            crop_y = float(options.get("crop_y", ""))
            crop_width = float(options.get("crop_width", ""))
            crop_height = float(options.get("crop_height", ""))
        except ValueError as exc:
            raise ValueError("Select a crop area before processing the PDF.") from exc
        values = (crop_x, crop_y, crop_width, crop_height)
        if (
            not all(math.isfinite(value) for value in values)
            or crop_x < 0
            or crop_y < 0
            or crop_width <= 0
            or crop_height <= 0
            or crop_x + crop_width > 1
            or crop_y + crop_height > 1
        ):
            raise ValueError("The selected crop area is outside the page.")
        try:
            document = pymupdf.open(stream=documents[0][1], filetype="pdf")
            try:
                for page in document:
                    visible = page.rect
                    if visible.is_empty or visible.is_infinite:
                        raise ValueError("The selected crop area is not valid for every page.")
                    selected = pymupdf.Rect(
                        visible.x0 + crop_x * visible.width,
                        visible.y0 + crop_y * visible.height,
                        visible.x0 + (crop_x + crop_width) * visible.width,
                        visible.y0 + (crop_y + crop_height) * visible.height,
                    )
                    selected = selected * page.derotation_matrix
                    selected = selected & page.cropbox
                    if selected.is_empty or selected.is_infinite:
                        raise ValueError("The selected crop area is not valid for every page.")
                    page.set_cropbox(selected)
                output = document.tobytes(garbage=4, deflate=True, clean=True)
            finally:
                document.close()
        except pymupdf.FileDataError as exc:
            raise ValueError("The uploaded file is not a readable PDF.") from exc
        return output, "cropped.pdf", page_count

    if operation == "scale_pages":
        output, output_pages = _scale_pages(documents[0][1], options)
        return output, "scaled-pages.pdf", output_pages

    if operation == "booklet_imposition":
        output, output_pages = _booklet_imposition(documents[0][1], options)
        return output, "booklet-imposition.pdf", output_pages

    if operation == "poster_pdf":
        output, output_pages = _poster_pdf(documents[0][1], options)
        return output, "poster-pages.pdf", output_pages

    if operation == "sanitize":
        output, output_pages = _sanitize_pdf(documents[0][1])
        return output, "sanitized.pdf", output_pages

    if operation == "flatten":
        output, output_pages = _flatten_pdf(
            documents[0][1], str(options.get("flatten_mode", "forms_annotations"))
        )
        return output, "flattened.pdf", output_pages

    if operation == "unlock_forms":
        output, output_pages = _unlock_pdf_forms(documents[0][1])
        return output, "unlocked-forms.pdf", output_pages

    if operation == "remove_pages":
        output, output_pages = _remove_pages(documents[0][1], options.get("pages", ""))
        return output, "pages-removed.pdf", output_pages

    if operation == "auto_form_detection":
        output, output_pages = _detect_form_fields(documents[0][1])
        return output, "auto-form-detection.pdf", output_pages

    if operation == "remove_blank_pages":
        output, output_pages = _remove_blank_pages(documents[0][1])
        return output, "blank-pages-removed.pdf", output_pages

    if operation == "remove_annotations":
        output, output_pages = _remove_annotations(documents[0][1])
        return output, "annotations-removed.pdf", output_pages

    if operation == "remove_images":
        output, output_pages = _remove_images(documents[0][1])
        return output, "images-removed.pdf", output_pages

    if operation == "remove_certificate_sign":
        output, output_pages = _remove_certificate_signatures(documents[0][1])
        return output, "signatures-removed.pdf", output_pages

    if operation == "split_scanned_photos":
        output, output_pages = _split_scanned_photos(documents[0][1])
        return output, "split-photos.pdf", output_pages

    if operation == "auto_rename":
        return _auto_rename(documents[0][1])

    if operation == "add_text":
        output, output_pages = _insert_text(documents[0][1], options)
        return output, "text-added.pdf", output_pages

    if operation == "annotate":
        output, output_pages = _insert_text(documents[0][1], options, annotation=True)
        return output, "annotated.pdf", output_pages

    if operation == "add_image":
        image_data = options.get("tool_image")
        if not isinstance(image_data, bytes):
            raise ValueError("Upload a PNG or JPEG image to add.")
        output, output_pages = _insert_image(
            documents[0][1],
            image_data,
            str(options.get("tool_image_type", "")),
            options,
        )
        return output, "image-added.pdf", output_pages

    if operation == "form_editor":
        output, output_pages = _edit_form_fields(
            documents[0][1], str(options.get("form_values", ""))
        )
        return output, "form-edited.pdf", output_pages

    if operation == "adjust_colors":
        output, output_pages = _adjust_colors(documents[0][1], options)
        return output, "colors-adjusted.pdf", output_pages

    if operation == "color_transform":
        output, output_pages = _replace_or_invert_color(documents[0][1], options)
        return output, "colors-transformed.pdf", output_pages

    if operation == "pdf_to_images":
        document = pymupdf.open(stream=documents[0][1], filetype="pdf")
        try:
            return _pdf_to_images(document)
        finally:
            document.close()

    if operation == "protect":
        password = str(options.get("protect_password", ""))
        owner_password = str(options.get("owner_password", ""))
        _validate_password(password, "Open password")
        _validate_password(owner_password, "Owner password", required=False)
        writer = PdfWriter()
        for page in pages:
            writer.add_page(page)
        writer.encrypt(
            user_password=password,
            owner_password=owner_password or None,
            permissions_flag=_permission_flags(options.get("permission_actions")),
            algorithm="AES-256",
        )
        return _writer_bytes(writer), "protected.pdf", page_count

    if operation == "change_permissions":
        owner_password = str(options.get("owner_password", ""))
        open_password = str(options.get("protect_password", ""))
        _validate_password(owner_password, "Owner password")
        _validate_password(open_password, "Open password", required=False)
        writer = PdfWriter()
        for page in pages:
            writer.add_page(page)
        writer.encrypt(
            user_password=open_password,
            owner_password=owner_password,
            permissions_flag=_permission_flags(options.get("permission_actions")),
            algorithm="AES-256",
        )
        return _writer_bytes(writer), "permissions-changed.pdf", page_count

    if operation in {"unlock", "remove_password"}:
        output_name = "password-removed.pdf" if operation == "remove_password" else "unlocked.pdf"
        return _single_pdf(pages), output_name, page_count

    if operation == "rotate":
        try:
            degrees = int(options.get("degrees", ""))
        except ValueError as exc:
            raise ValueError("Choose a rotation of 90, 180, or 270 degrees.") from exc
        if degrees not in {90, 180, 270}:
            raise ValueError("Choose a rotation of 90, 180, or 270 degrees.")
        for page in pages:
            page.rotate(degrees)
        return _single_pdf(pages), "rotated.pdf", page_count

    if operation == "reorder":
        selected = _parse_pages(options.get("pages", ""), len(pages))
        if len(selected) != len(pages) or set(selected) != set(range(1, len(pages) + 1)):
            raise ValueError("Reorder must list every page exactly once.")
        return _single_pdf(pages, selected), "reordered.pdf", page_count

    if operation == "extract":
        selected = _parse_pages(options.get("pages", ""), len(pages))
        return _single_pdf(pages, selected), "extracted-pages.pdf", len(selected)

    if operation == "compress":
        writer = PdfWriter()
        for page in pages:
            writer.add_page(page)
        for page in writer.pages:
            page.compress_content_streams()
        writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
        return _writer_bytes(writer), "compressed.pdf", page_count

    if operation == "watermark":
        text = options.get("text", "").strip()
        if not text:
            raise ValueError("Enter watermark text.")
        writer = PdfWriter()
        for source_page in pages:
            page = writer.add_page(source_page)
            box = page.mediabox
            watermark = _watermark_page(float(box.width), float(box.height), text)
            page.merge_page(watermark.pages[0])
        return _writer_bytes(writer), "watermarked.pdf", page_count

    if operation == "stamp":
        text = str(options.get("stamp_text", "")).strip()
        image_data = options.get("stamp_image")
        stamp_mode = str(options.get("stamp_mode", "text"))
        if stamp_mode not in {"text", "image", "both"}:
            raise ValueError("Choose a supported stamp type.")
        if stamp_mode in {"text", "both"} and not text:
            raise ValueError("Enter text for the stamp.")
        if stamp_mode in {"image", "both"} and not isinstance(image_data, bytes):
            raise ValueError("Upload a PNG or JPEG stamp image.")
        if stamp_mode == "text":
            image_data = None
        elif stamp_mode == "image":
            text = ""
        if isinstance(image_data, bytes):
            _validate_stamp_image(
                image_data, str(options.get("stamp_image_type", ""))
            )
        writer = PdfWriter()
        for source_page in pages:
            page = writer.add_page(source_page)
            box = page.mediabox
            stamp = _stamp_page(
                float(box.width),
                float(box.height),
                text,
                image_data if isinstance(image_data, bytes) else None,
                str(options.get("stamp_position", "center")),
            )
            page.merge_page(stamp.pages[0])
        return _writer_bytes(writer), "stamped.pdf", page_count

    search_text = options.get("redact_text", "").strip()
    if not search_text:
        raise ValueError("Enter the exact text to redact.")
    try:
        document = pymupdf.open(stream=documents[0][1], filetype="pdf")
    except pymupdf.FileDataError as exc:
        raise ValueError("The uploaded file is not a readable PDF.") from exc
    matches = 0
    for page in document:
        matches_on_page = page.search_for(search_text)
        for rectangle in matches_on_page:
            page.add_redact_annot(rectangle, fill=(0, 0, 0))
        if matches_on_page:
            matches += len(matches_on_page)
            page.apply_redactions()
    if matches == 0:
        document.close()
        raise ValueError("No matching text was found; no redacted file was produced.")
    result = document.tobytes(garbage=4, deflate=True, clean=True)
    document.close()
    return result, "redacted.pdf", page_count
