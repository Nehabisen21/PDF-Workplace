import base64
import io
import uuid

import pymupdf
from cryptography.exceptions import UnsupportedAlgorithm
from pymupdf.mupdf import FzErrorBase
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.misc import PdfReadError as HankoPdfReadError
from pyhanko.sign import signers
from pyhanko.sign.general import SigningError
from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError

from .pdf_ops import MAX_PDF_PAGES


MAX_SIGNATURE_IMAGE_BYTES = 2 * 1024 * 1024
MAX_SIGNATURE_IMAGE_PIXELS = 4_000_000
MAX_CERTIFICATE_BYTES = 5 * 1024 * 1024
MAX_CERTIFICATE_PASSWORD_BYTES = 1024


def _validate_pdf(pdf_bytes: bytes) -> None:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=True)
        if reader.is_encrypted:
            raise ValueError("Unlock the PDF before signing it.")
        page_count = len(reader.pages)
    except (PdfReadError, PdfStreamError) as exc:
        raise ValueError("The uploaded file is not a readable PDF.") from exc
    if page_count == 0:
        raise ValueError("The PDF must contain at least one page.")
    if page_count > MAX_PDF_PAGES:
        raise ValueError(f"A PDF cannot exceed {MAX_PDF_PAGES} pages.")


def decode_signature_image(signature_data: str) -> bytes:
    if len(signature_data) > MAX_SIGNATURE_IMAGE_BYTES * 4 // 3 + 128:
        raise ValueError("The signature image must be no larger than 2 MB.")
    header, separator, encoded = signature_data.partition(",")
    if header != "data:image/png;base64" or not separator:
        raise ValueError("Signature image must be a PNG created or selected in the signature editor.")
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("The signature image is invalid.") from exc
    if len(image_bytes) > MAX_SIGNATURE_IMAGE_BYTES:
        raise ValueError("The signature image must be no larger than 2 MB.")
    if len(image_bytes) < 24 or image_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("The signature image is not a valid PNG.")
    width = int.from_bytes(image_bytes[16:20], "big")
    height = int.from_bytes(image_bytes[20:24], "big")
    if width == 0 or height == 0 or width * height > MAX_SIGNATURE_IMAGE_PIXELS:
        raise ValueError("The signature image dimensions are invalid or too large.")
    return image_bytes


def apply_personal_signature(
    pdf_bytes: bytes, signature_data: str, page_number: int, position: str
) -> bytes:
    _validate_pdf(pdf_bytes)
    if page_number < 1:
        raise ValueError("Page number must be at least 1.")
    if position not in {"bottom-left", "bottom-center", "bottom-right"}:
        raise ValueError("Choose a supported signature position.")
    image_bytes = decode_signature_image(signature_data)

    try:
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except pymupdf.FileDataError as exc:
        raise ValueError("The uploaded file is not a readable PDF.") from exc
    try:
        if document.is_encrypted:
            raise ValueError("Unlock the PDF before signing it.")
        if page_number > document.page_count:
            raise ValueError(f"Page number must be between 1 and {document.page_count}.")
        page = document[page_number - 1]
        page_width = min(180.0, page.rect.width * 0.4)
        page_height = min(60.0, page.rect.height * 0.12)
        margin = min(36.0, max(0.0, (page.rect.width - page_width) / 2))
        if position == "bottom-left":
            x0 = margin
        elif position == "bottom-center":
            x0 = (page.rect.width - page_width) / 2
        else:
            x0 = page.rect.width - page_width - margin
        y1 = page.rect.height - min(36.0, max(0.0, page.rect.height * 0.08))
        rect = pymupdf.Rect(x0, y1 - page_height, x0 + page_width, y1)
        try:
            page.insert_image(rect, stream=image_bytes, keep_proportion=True, overlay=True)
            return document.tobytes(garbage=4, deflate=True)
        except (FzErrorBase, pymupdf.FileDataError) as exc:
            raise ValueError("The signature image is not a readable PNG.") from exc
    finally:
        document.close()


def apply_certificate_signature(
    pdf_bytes: bytes, certificate_bytes: bytes, certificate_password: str
) -> bytes:
    _validate_pdf(pdf_bytes)
    if len(certificate_bytes) > MAX_CERTIFICATE_BYTES:
        raise ValueError("The PKCS#12 certificate must be no larger than 5 MB.")
    if len(certificate_password.encode("utf-8")) > MAX_CERTIFICATE_PASSWORD_BYTES:
        raise ValueError("The certificate password must be no longer than 1024 UTF-8 bytes.")
    try:
        signer = signers.SimpleSigner.load_pkcs12_data(
            pkcs12_bytes=certificate_bytes,
            other_certs=None,
            passphrase=certificate_password.encode("utf-8") if certificate_password else None,
        )
    except (
        OSError,
        TypeError,
        ValueError,
        SigningError,
        HankoPdfReadError,
        UnsupportedAlgorithm,
    ) as exc:
        raise ValueError("Could not read the PKCS#12 certificate. Check the file and password.") from exc
    if signer is None:
        raise ValueError("The PKCS#12 file does not contain a usable private key and certificate.")

    try:
        writer = IncrementalPdfFileWriter(io.BytesIO(pdf_bytes), strict=True)
        metadata = signers.PdfSignatureMetadata(
            field_name=f"PDFWorkplace-{uuid.uuid4().hex[:12]}",
            name="PDF Workplace",
            reason="Document signed with a PKCS#12 certificate",
        )
        signed_output = signers.PdfSigner(metadata, signer).sign_pdf(writer)
        return signed_output.getvalue()
    except (
        OSError,
        TypeError,
        ValueError,
        SigningError,
        HankoPdfReadError,
        UnsupportedAlgorithm,
    ) as exc:
        raise ValueError("The PDF could not be signed with this certificate.") from exc
