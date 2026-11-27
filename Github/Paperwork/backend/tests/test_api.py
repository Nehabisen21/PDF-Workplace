import base64
import hashlib
import io
import os
import struct
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
import pymupdf
from PIL import Image
from cryptography.fernet import Fernet
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from fastapi import HTTPException
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.validation import validate_pdf_signature
from pypdf import PdfReader
from reportlab.pdfgen import canvas
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SESSION_SECRET", "test-only-session-secret-not-for-deployment")

from app import main
from app.db import Base, get_db
from app.models import Folder, Invitation, SavedSignature, StoredDocument, Team, User


@pytest.fixture
def authenticated_client(monkeypatch):
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(main, "engine", test_engine)
    Base.metadata.create_all(test_engine)

    def override_get_db():
        with Session(test_engine) as db:
            yield db

    with Session(test_engine) as db:
        user = User(issuer="test-issuer", subject="test-subject", display_name="Test User")
        db.add(user)
        db.commit()
        db.refresh(user)

    main.app.dependency_overrides[get_db] = override_get_db
    main.app.dependency_overrides[main._current_user] = lambda: user
    try:
        with TestClient(main.app) as client:
            yield client
    finally:
        main.app.dependency_overrides.clear()
        test_engine.dispose()


def make_pdf() -> bytes:
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=(300, 300))
    pdf.drawString(30, 250, "api smoke")
    pdf.showPage()
    pdf.save()
    return output.getvalue()


def make_signature_png() -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=400, height=120)
    page.insert_text((25, 80), "Test signature", fontsize=36)
    image = page.get_pixmap(alpha=True).tobytes("png")
    document.close()
    return image


def make_pkcs12_certificate() -> tuple[bytes, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PDF Workplace Test Signer")])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )
    password = "test certificate password"
    certificate_bytes = pkcs12.serialize_key_and_certificates(
        b"test-signer",
        private_key,
        certificate,
        None,
        serialization.BestAvailableEncryption(password.encode()),
    )
    return certificate_bytes, password


def test_authenticated_processing_reads_multipart_options_and_saves_job(authenticated_client):
    response = authenticated_client.post(
        "/api/process/rotate",
        data={"degrees": "90"},
        files={"files": ("smoke.pdf", make_pdf(), "application/pdf")},
    )

    assert response.status_code == 200
    assert PdfReader(io.BytesIO(response.content)).pages[0].get("/Rotate") == 90

    history = authenticated_client.get("/api/jobs")
    assert history.status_code == 200
    assert len(history.json()) == 1
    assert history.json()[0]["operation"] == "rotate"


def test_format_converter_images_and_rejects_unsupported_pairs(authenticated_client):
    source = io.BytesIO()
    Image.new("RGB", (36, 24), "navy").save(source, format="PNG")

    converted_pdf = authenticated_client.post(
        "/api/process/convert",
        data={"source_format": "png", "target_format": "pdf"},
        files={"files": ("sample.png", source.getvalue(), "image/png")},
    )
    assert converted_pdf.status_code == 200
    assert converted_pdf.headers["content-type"] == "application/pdf"
    assert len(PdfReader(io.BytesIO(converted_pdf.content)).pages) == 1

    converted_image = authenticated_client.post(
        "/api/process/convert",
        data={"source_format": "pdf", "target_format": "png"},
        files={"files": ("sample.pdf", converted_pdf.content, "application/pdf")},
    )
    assert converted_image.status_code == 200
    assert converted_image.headers["content-type"] == "image/png"
    assert converted_image.content.startswith(b"\x89PNG")

    unsupported = authenticated_client.post(
        "/api/process/convert",
        data={"source_format": "pdf", "target_format": "docx"},
        files={"files": ("sample.pdf", converted_pdf.content, "application/pdf")},
    )
    assert unsupported.status_code == 400
    assert "combination is not supported" in unsupported.json()["detail"]

    svg_data = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24">'
        b'<rect width="24" height="24" fill="#3657a5"/></svg>'
    )
    converted_svg = authenticated_client.post(
        "/api/process/convert",
        data={"source_format": "svg", "target_format": "png"},
        files={"files": ("vector.svg", svg_data, "image/svg+xml")},
    )
    assert converted_svg.status_code == 200
    assert converted_svg.content.startswith(b"\x89PNG")

    external_svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.invalid/x.png"/>'
        b"</svg>"
    )
    rejected_svg = authenticated_client.post(
        "/api/process/convert",
        data={"source_format": "svg", "target_format": "png"},
        files={"files": ("external.svg", external_svg, "image/svg+xml")},
    )
    assert rejected_svg.status_code == 400
    assert "external resources" in rejected_svg.json()["detail"]


def test_format_converter_converts_comic_and_email_to_pdf(authenticated_client):
    image = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(image, format="PNG")
    comic = io.BytesIO()
    with zipfile.ZipFile(comic, "w") as archive:
        archive.writestr("pages/page-1.png", image.getvalue())

    comic_pdf = authenticated_client.post(
        "/api/process/convert",
        data={"source_format": "cbz", "target_format": "pdf"},
        files={"files": ("comic.cbz", comic.getvalue(), "application/zip")},
    )
    assert comic_pdf.status_code == 200
    assert len(PdfReader(io.BytesIO(comic_pdf.content)).pages) == 1

    email_pdf = authenticated_client.post(
        "/api/process/convert",
        data={"source_format": "eml", "target_format": "pdf"},
        files={
            "files": (
                "message.eml",
                b"From: reader@example.com\r\nSubject: Conversion test\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n\r\nMessage body.",
                "message/rfc822",
            )
        },
    )
    assert email_pdf.status_code == 200
    converted = pymupdf.open(stream=email_pdf.content, filetype="pdf")
    assert "Conversion test" in converted[0].get_text()
    assert "Message body." in converted[0].get_text()
    converted.close()

    invalid_msg = authenticated_client.post(
        "/api/process/convert",
        data={"source_format": "msg", "target_format": "pdf"},
        files={
            "files": (
                "broken.msg",
                bytes.fromhex("D0CF11E0A1B11AE1") + bytes(2048),
                "application/vnd.ms-outlook",
            )
        },
    )
    assert invalid_msg.status_code == 400
    assert "MSG email could not be read" in invalid_msg.json()["detail"]


def test_format_converter_uses_libreoffice_for_office_documents(authenticated_client):
    source = io.BytesIO()
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Office conversion smoke test</w:t></w:r></w:p>"
            "<w:sectPr/></w:body></w:document>",
        )

    response = authenticated_client.post(
        "/api/process/convert",
        data={"source_format": "docx", "target_format": "pdf"},
        files={
            "files": (
                "office.docx",
                source.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    converted = pymupdf.open(stream=response.content, filetype="pdf")
    assert "Office conversion smoke test" in converted[0].get_text()
    converted.close()


def test_format_converter_uses_calibre_for_ebooks(authenticated_client):
    source = io.BytesIO()
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="book">urn:test:book</dc:identifier>'
            '<dc:title>Calibre Conversion Test</dc:title><dc:language>en</dc:language>'
            "</metadata><manifest><item id=\"chapter\" href=\"chapter.xhtml\" "
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="chapter"/></spine></package>',
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Test</title></head>'
            "<body><h1>Calibre conversion smoke test</h1></body></html>",
        )

    response = authenticated_client.post(
        "/api/process/convert",
        data={"source_format": "epub", "target_format": "pdf"},
        files={"files": ("ebook.epub", source.getvalue(), "application/epub+zip")},
    )
    assert response.status_code == 200
    converted = pymupdf.open(stream=response.content, filetype="pdf")
    assert "Calibre conversion smoke test" in converted[0].get_text()
    converted.close()


def test_crop_preview_and_processing_endpoints(authenticated_client):
    source = make_pdf()
    preview = authenticated_client.post(
        "/api/pdf/preview",
        files={"files": ("source.pdf", source, "application/pdf")},
    )
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.content.startswith(b"\x89PNG")

    cropped = authenticated_client.post(
        "/api/process/crop",
        data={
            "crop_x": "0.1",
            "crop_y": "0.1",
            "crop_width": "0.5",
            "crop_height": "0.5",
        },
        files={"files": ("source.pdf", source, "application/pdf")},
    )
    assert cropped.status_code == 200
    document = pymupdf.open(stream=cropped.content, filetype="pdf")
    assert document[0].rect.width == pytest.approx(150)
    assert document[0].rect.height == pytest.approx(150)
    document.close()


def test_new_pdf_tools_and_form_field_api(authenticated_client):
    source_document = pymupdf.open()
    first_page = source_document.new_page(width=300, height=300)
    first_page.insert_text((30, 40), "first page")
    first_page.add_text_annot((40, 100), "remove this comment")
    second_page = source_document.new_page(width=300, height=300)
    second_page.insert_text((30, 40), "second page")
    source = source_document.tobytes()
    source_document.close()

    removed = authenticated_client.post(
        "/api/process/remove_pages",
        data={"pages": "2"},
        files={"files": ("source.pdf", source, "application/pdf")},
    )
    assert removed.status_code == 200
    assert len(PdfReader(io.BytesIO(removed.content)).pages) == 1

    text_added = authenticated_client.post(
        "/api/process/add_text",
        data={"text": "API inserted", "page_number": "1", "x": "40", "y": "80"},
        files={"files": ("source.pdf", source, "application/pdf")},
    )
    assert text_added.status_code == 200
    assert "API inserted" in PdfReader(io.BytesIO(text_added.content)).pages[0].extract_text()

    base = make_pdf()
    overlay = authenticated_client.post(
        "/api/process/overlay",
        files=[
            ("files", ("base.pdf", base, "application/pdf")),
            ("files", ("overlay.pdf", make_pdf(), "application/pdf")),
        ],
    )
    assert overlay.status_code == 200
    assert overlay.headers["content-type"] == "application/pdf"

    form_document = pymupdf.open(stream=make_pdf(), filetype="pdf")
    widget = pymupdf.Widget()
    widget.field_name = "Applicant"
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.field_value = "Original"
    widget.rect = pymupdf.Rect(30, 60, 180, 90)
    form_document[0].add_widget(widget)
    form_bytes = form_document.tobytes()
    form_document.close()

    fields = authenticated_client.post(
        "/api/pdf/form-fields",
        files={"files": ("form.pdf", form_bytes, "application/pdf")},
    )
    assert fields.status_code == 200
    assert fields.json()["fields"] == [
        {"name": "Applicant", "type": "Text", "value": "Original", "page": 1}
    ]
    edited = authenticated_client.post(
        "/api/process/form_editor",
        data={"form_values": '{"Applicant":"Updated"}'},
        files={"files": ("form.pdf", form_bytes, "application/pdf")},
    )
    assert edited.status_code == 200
    result = pymupdf.open(stream=edited.content, filetype="pdf")
    assert list(result[0].widgets())[0].field_value == "Updated"
    result.close()


def test_stamp_and_change_permissions_api_endpoints(authenticated_client):
    source = make_pdf()
    stamped = authenticated_client.post(
        "/api/process/stamp",
        data={
            "stamp_mode": "both",
            "stamp_text": "APPROVED",
            "stamp_position": "center",
        },
        files=[
            ("files", ("source.pdf", source, "application/pdf")),
            ("stamp_image", ("stamp.png", make_signature_png(), "image/png")),
        ],
    )
    assert stamped.status_code == 200
    assert "APPROVED" in PdfReader(io.BytesIO(stamped.content)).pages[0].extract_text()

    changed = authenticated_client.post(
        "/api/process/change_permissions",
        data={
            "owner_password": "new owner password",
            "permission_actions": "print",
        },
        files={"files": ("source.pdf", source, "application/pdf")},
    )
    assert changed.status_code == 200
    reader = PdfReader(io.BytesIO(changed.content))
    assert reader.decrypt("")


def test_image_conversions_through_api(authenticated_client):
    source_document = pymupdf.open(stream=make_pdf(), filetype="pdf")
    image = source_document[0].get_pixmap().tobytes("png")
    source_document.close()

    converted = authenticated_client.post(
        "/api/process/images_to_pdf",
        files={"files": ("page.png", image, "image/png")},
    )
    assert converted.status_code == 200
    assert len(PdfReader(io.BytesIO(converted.content)).pages) == 1

    rendered = authenticated_client.post(
        "/api/process/pdf_to_images",
        files={"files": ("source.pdf", converted.content, "application/pdf")},
    )
    assert rendered.status_code == 200
    assert rendered.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(rendered.content)) as archive:
        assert archive.namelist() == ["page-001.png"]


def test_password_protection_and_unlock_through_api(authenticated_client):
    protected = authenticated_client.post(
        "/api/process/protect",
        data={"protect_password": "api test password"},
        files={"files": ("source.pdf", make_pdf(), "application/pdf")},
    )
    assert protected.status_code == 200
    assert PdfReader(io.BytesIO(protected.content)).is_encrypted

    unlocked = authenticated_client.post(
        "/api/process/unlock",
        data={"unlock_password": "api test password"},
        files={"files": ("protected.pdf", protected.content, "application/pdf")},
    )
    assert unlocked.status_code == 200
    assert not PdfReader(io.BytesIO(unlocked.content)).is_encrypted


def test_personal_signature_is_visible_on_selected_page(authenticated_client):
    signature_image = base64.b64encode(make_signature_png()).decode("ascii")
    response = authenticated_client.post(
        "/api/sign/personal",
        data={
            "signature_image": f"data:image/png;base64,{signature_image}",
            "page_number": "1",
            "position": "bottom-left",
        },
        files={"files": ("source.pdf", make_pdf(), "application/pdf")},
    )

    assert response.status_code == 200, response.text
    signed_document = pymupdf.open(stream=response.content, filetype="pdf")
    assert len(signed_document[0].get_images()) == 1
    image_rect = signed_document[0].get_image_rects(signed_document[0].get_images()[0][0])[0]
    assert image_rect.x0 < signed_document[0].rect.width / 2
    assert image_rect.y0 > signed_document[0].rect.height / 2
    signed_document.close()
    assert authenticated_client.get("/api/jobs").json()[0]["operation"] == "personal_signature"


def test_personal_signature_rejects_page_outside_document(authenticated_client):
    signature_image = base64.b64encode(make_signature_png()).decode("ascii")
    response = authenticated_client.post(
        "/api/sign/personal",
        data={
            "signature_image": f"data:image/png;base64,{signature_image}",
            "page_number": "2",
            "position": "bottom-right",
        },
        files={"files": ("source.pdf", make_pdf(), "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Page number must be between 1 and 1."


def test_personal_signature_rejects_malformed_png(authenticated_client):
    malformed_png = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + struct.pack(">II", 10, 10)
        + b"\x08\x06\x00\x00\x00"
    )
    signature_image = base64.b64encode(malformed_png).decode("ascii")
    response = authenticated_client.post(
        "/api/sign/personal",
        data={
            "signature_image": f"data:image/png;base64,{signature_image}",
            "page_number": "1",
            "position": "bottom-right",
        },
        files={"files": ("source.pdf", make_pdf(), "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "The signature image is not a readable PNG."


def test_saved_personal_signature_is_encrypted_reusable_and_private(authenticated_client, monkeypatch):
    encryption_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(main, "STORAGE_ENCRYPTION_KEY", encryption_key)
    signature_image_bytes = make_signature_png()
    signature_image = base64.b64encode(signature_image_bytes).decode("ascii")
    created = authenticated_client.post(
        "/api/sign/personal",
        data={
            "signature_image": f"data:image/png;base64,{signature_image}",
            "page_number": "1",
            "position": "bottom-right",
            "save_to_signatures": "true",
            "signature_name": "Work signature",
            "make_default": "true",
        },
        files={"files": ("source.pdf", make_pdf(), "application/pdf")},
    )

    assert created.status_code == 200
    signature_id = int(created.headers["x-saved-signature-id"])
    with Session(main.engine) as db:
        saved = db.get(SavedSignature, signature_id)
        assert saved is not None
        assert saved.is_default
        assert saved.encrypted_image != signature_image_bytes
        assert signature_image_bytes not in saved.encrypted_image

    listed = authenticated_client.get("/api/signatures")
    assert listed.status_code == 200
    assert listed.json() == [
        {"id": signature_id, "name": "Work signature", "is_default": True}
    ]
    detail = authenticated_client.get(f"/api/signatures/{signature_id}")
    assert detail.status_code == 200
    assert detail.headers["cache-control"] == "no-store"
    assert base64.b64decode(detail.json()["signature_image"].split(",", 1)[1]) == signature_image_bytes

    reused = authenticated_client.post(
        "/api/sign/personal",
        data={
            "saved_signature_id": str(signature_id),
            "make_default": "false",
            "page_number": "1",
            "position": "bottom-left",
        },
        files={"files": ("source.pdf", make_pdf(), "application/pdf")},
    )
    assert reused.status_code == 200
    assert authenticated_client.get("/api/signatures").json()[0]["is_default"] is False

    made_default = authenticated_client.post(
        "/api/sign/personal",
        data={
            "saved_signature_id": str(signature_id),
            "make_default": "true",
            "page_number": "1",
            "position": "bottom-left",
        },
        files={"files": ("source.pdf", make_pdf(), "application/pdf")},
    )
    assert made_default.status_code == 200
    assert authenticated_client.get("/api/signatures").json()[0]["is_default"] is True

    with Session(main.engine) as db:
        other_user = User(
            issuer="other-issuer", subject="other-subject", display_name="Other User"
        )
        db.add(other_user)
        db.commit()
        db.refresh(other_user)
    main.app.dependency_overrides[main._current_user] = lambda: other_user
    assert authenticated_client.get("/api/signatures").json() == []
    assert authenticated_client.get(f"/api/signatures/{signature_id}").status_code == 404


def test_saved_personal_signature_rejects_duplicate_name(authenticated_client, monkeypatch):
    monkeypatch.setattr(main, "STORAGE_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    signature_image = base64.b64encode(make_signature_png()).decode("ascii")
    request_data = {
        "signature_image": f"data:image/png;base64,{signature_image}",
        "page_number": "1",
        "position": "bottom-right",
        "save_to_signatures": "true",
        "signature_name": "Primary",
    }
    files = {"files": ("source.pdf", make_pdf(), "application/pdf")}

    assert authenticated_client.post(
        "/api/sign/personal", data=request_data, files=files
    ).status_code == 200
    duplicate = authenticated_client.post(
        "/api/sign/personal", data=request_data, files=files
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "A saved signature with this name already exists."


def test_certificate_signature_is_cryptographically_valid_and_password_is_checked(authenticated_client):
    certificate, password = make_pkcs12_certificate()
    response = authenticated_client.post(
        "/api/sign/digital",
        data={"certificate_password": password},
        files=[
            ("files", ("source.pdf", make_pdf(), "application/pdf")),
            ("certificate_file", ("signer.p12", certificate, "application/x-pkcs12")),
        ],
    )

    assert response.status_code == 200, response.text
    signed_pdf = PdfFileReader(io.BytesIO(response.content))
    assert len(signed_pdf.embedded_signatures) == 1
    signature_status = validate_pdf_signature(signed_pdf.embedded_signatures[0])
    assert signature_status.intact
    assert signature_status.valid

    rejected = authenticated_client.post(
        "/api/sign/digital",
        data={"certificate_password": "wrong password"},
        files=[
            ("files", ("source.pdf", make_pdf(), "application/pdf")),
            ("certificate_file", ("signer.p12", certificate, "application/x-pkcs12")),
        ],
    )
    assert rejected.status_code == 400
    assert "Check the file and password" in rejected.json()["detail"]


def test_encrypted_server_folder_and_batch_merge(authenticated_client, monkeypatch):
    monkeypatch.setattr(main, "STORAGE_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    created = authenticated_client.post("/api/folders", json={"name": "Invoices"})
    assert created.status_code == 201
    folder_id = created.json()["id"]
    first_pdf = make_pdf()
    second_pdf = make_pdf()

    uploaded = authenticated_client.post(
        f"/api/folders/{folder_id}/documents",
        files=[
            ("files", ("first.pdf", first_pdf, "application/pdf")),
            ("files", ("second.pdf", second_pdf, "application/pdf")),
        ],
    )
    assert uploaded.status_code == 201
    assert [document["name"] for document in uploaded.json()] == ["first.pdf", "second.pdf"]

    with Session(main.engine) as db:
        folder = db.get(Folder, folder_id)
        stored = db.query(StoredDocument).filter_by(folder_id=folder_id).all()
        assert folder is not None
        assert len(stored) == 2
        assert all(b"api smoke" not in document.encrypted_content for document in stored)

    download = authenticated_client.get(
        f"/api/folders/{folder_id}/documents/{uploaded.json()[0]['id']}"
    )
    assert download.status_code == 200
    assert download.content == first_pdf

    merged = authenticated_client.post(f"/api/folders/{folder_id}/process/merge")
    assert merged.status_code == 200
    assert len(PdfReader(io.BytesIO(merged.content)).pages) == 2

    split = authenticated_client.post(
        f"/api/folders/{folder_id}/process/split",
        data={"split_mode": "document_count", "split_value": "1"},
    )
    assert split.status_code == 200
    assert split.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(split.content)) as archive:
        assert archive.namelist() == ["part-1.pdf", "part-2.pdf"]

    listed = authenticated_client.get("/api/folders")
    assert listed.json() == [{"id": folder_id, "name": "Invoices", "document_count": 2}]

    with Session(main.engine) as db:
        other_user = User(
            issuer="other-issuer", subject="other-subject", display_name="Other User"
        )
        db.add(other_user)
        db.commit()
        db.refresh(other_user)
    main.app.dependency_overrides[main._current_user] = lambda: other_user
    assert authenticated_client.get(f"/api/folders/{folder_id}/documents").status_code == 404


def test_folder_storage_requires_an_encryption_key(authenticated_client, monkeypatch):
    monkeypatch.setattr(main, "STORAGE_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    created = authenticated_client.post("/api/folders", json={"name": "Private"})
    assert created.status_code == 201
    monkeypatch.setattr(main, "STORAGE_ENCRYPTION_KEY", "")
    response = authenticated_client.post(
        f"/api/folders/{created.json()['id']}/documents",
        files={"files": ("private.pdf", make_pdf(), "application/pdf")},
    )
    assert response.status_code == 503
    assert "STORAGE_ENCRYPTION_KEY" in response.json()["detail"]


def test_folder_rejects_non_pdf_content(authenticated_client, monkeypatch):
    monkeypatch.setattr(main, "STORAGE_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    created = authenticated_client.post("/api/folders", json={"name": "Documents"})
    response = authenticated_client.post(
        f"/api/folders/{created.json()['id']}/documents",
        files={"files": ("invalid.pdf", b"not a PDF", "application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "An uploaded file is not a readable PDF."


def test_admin_user_directory_is_paginated_without_a_total_user_cap(authenticated_client):
    admin = main.app.dependency_overrides[main._current_user]()
    admin.role = "org_owner"
    with Session(main.engine) as db:
        db.add_all(
            [
                User(
                    issuer="tenant",
                    subject=f"user-{index}",
                    email="target@example.com" if index == 0 else f"user{index}@example.com",
                    display_name=f"Directory User {index}",
                )
                for index in range(600)
            ]
        )
        db.commit()

    first_page = authenticated_client.get("/api/admin/users?offset=0&limit=50")
    assert first_page.status_code == 200
    assert first_page.json()["total"] == 601
    assert len(first_page.json()["users"]) == 50

    last_page = authenticated_client.get("/api/admin/users?offset=600&limit=50")
    assert last_page.status_code == 200
    assert len(last_page.json()["users"]) == 1

    search = authenticated_client.get("/api/admin/users?search=target%40example.com")
    assert search.status_code == 200
    target = search.json()["users"][0]
    changed_role = authenticated_client.patch(
        f"/api/admin/users/{target['id']}", json={"role": "admin"}
    )
    assert changed_role.status_code == 200
    assert changed_role.json()["role"] == "admin"

    disabled = authenticated_client.patch(
        f"/api/admin/users/{target['id']}", json={"is_active": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False
    enabled = authenticated_client.patch(
        f"/api/admin/users/{target['id']}", json={"is_active": True}
    )
    assert enabled.status_code == 200
    assert enabled.json()["is_active"] is True


def test_member_cannot_access_admin_api(authenticated_client):
    member = main.app.dependency_overrides[main._current_user]()
    member.role = "member"
    assert authenticated_client.get("/api/admin/users").status_code == 403
    assert authenticated_client.patch(
        f"/api/admin/users/{member.id}", json={"role": "admin"}
    ).status_code == 403
    assert authenticated_client.get("/api/admin/teams").status_code == 403
    assert authenticated_client.get("/api/admin/invitations").status_code == 403


def test_admin_cannot_promote_members_or_manage_other_admins(authenticated_client):
    admin = main.app.dependency_overrides[main._current_user]()
    admin.role = "admin"
    with Session(main.engine) as db:
        db.add_all(
            [
                User(
                    issuer="tenant",
                    subject="admin-target",
                    email="other-admin@example.com",
                    display_name="Other Admin",
                    role="admin",
                ),
                User(
                    issuer="tenant",
                    subject="member-target",
                    email="member@example.com",
                    display_name="Member",
                ),
            ]
        )
        db.commit()
        other_admin = db.query(User).filter_by(subject="admin-target").one()
        member = db.query(User).filter_by(subject="member-target").one()

    assert authenticated_client.patch(
        f"/api/admin/users/{other_admin.id}", json={"is_active": False}
    ).status_code == 403
    assert authenticated_client.patch(
        f"/api/admin/users/{member.id}", json={"role": "admin"}
    ).status_code == 403


def test_teams_invitations_and_capability_assignments(authenticated_client):
    admin = main.app.dependency_overrides[main._current_user]()
    admin.role = "org_owner"
    teams = authenticated_client.get("/api/admin/teams")
    assert teams.status_code == 200
    default_team = next(team for team in teams.json() if team["name"] == "Default")

    created_team = authenticated_client.post("/api/admin/teams", json={"name": "Legal"})
    assert created_team.status_code == 201
    team_id = created_team.json()["id"]
    invitation_response = authenticated_client.post(
        "/api/admin/invitations",
        json={
            "emails": ["first@example.com", "second@example.com"],
            "team_id": team_id,
            "role": "team_lead",
            "can_edit": False,
            "can_process": True,
        },
    )
    assert invitation_response.status_code == 201
    assert invitation_response.json()["created"] == 2
    invitations = authenticated_client.get("/api/admin/invitations").json()
    assert {invite["email"] for invite in invitations} == {
        "first@example.com",
        "second@example.com",
    }
    assert all(invite["team_id"] == team_id for invite in invitations)
    assert all(not invite["can_edit"] and invite["can_process"] for invite in invitations)

    invitation_id = invitations[0]["id"]
    assert authenticated_client.delete(
        f"/api/admin/invitations/{invitation_id}"
    ).status_code == 204
    assert len(authenticated_client.get("/api/admin/invitations").json()) == 1

    with Session(main.engine) as db:
        user = User(
            issuer="tenant",
            subject="capability-user",
            email="capability@example.com",
            display_name="Capability User",
            team_id=default_team["id"],
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        user_id = user.id
    updated = authenticated_client.patch(
        f"/api/admin/users/{user_id}",
        json={"team_id": team_id, "can_edit": False, "can_process": True},
    )
    assert updated.status_code == 200
    assert updated.json()["team"] == "Legal"
    assert updated.json()["can_edit"] is False
    assert updated.json()["can_process"] is True


def test_pending_invitation_is_applied_on_sign_in(authenticated_client, monkeypatch):
    monkeypatch.setattr(main, "INITIAL_ADMIN_EMAILS", frozenset())
    monkeypatch.setattr(main, "_configured_local_admin", lambda: False)
    with Session(main.engine) as db:
        team = Team(name="Research")
        db.add(team)
        db.flush()
        user = User(
            issuer="tenant",
            subject="invitee",
            email="invitee@example.com",
            display_name="Invitee",
            team_id=admin_team_id(db),
        )
        invitation = Invitation(
            email="invitee@example.com",
            team_id=team.id,
            role="team_lead",
            can_edit=False,
            can_process=True,
        )
        db.add_all([user, invitation])
        db.commit()
        main._apply_pending_invitation(db, user, "INVITEE@example.com")
        db.commit()
        db.refresh(user)
        db.refresh(invitation)
        assert user.role == "team_lead"
        assert user.team_id == team.id
        assert user.can_edit is False
        assert user.can_process is True
        assert invitation.accepted_at is not None


def test_usage_metrics_and_read_only_server_settings(authenticated_client):
    admin = main.app.dependency_overrides[main._current_user]()
    admin.role = "org_owner"
    usage = authenticated_client.get("/api/usage")
    assert usage.status_code == 200
    assert usage.json()["scope"] == "organization"
    assert "completed_operations" in usage.json()
    assert "active_users" in usage.json()

    settings = authenticated_client.get("/api/admin/server-settings")
    assert settings.status_code == 200
    assert settings.json()["user_seat_limit"] == "Unlimited"
    assert "ENTRA_CLIENT_SECRET" not in settings.text
    admin.role = "member"
    personal_usage = authenticated_client.get("/api/usage")
    assert personal_usage.status_code == 200
    assert personal_usage.json()["scope"] == "personal"
    assert personal_usage.json()["active_users"] is None


def admin_team_id(db):
    return db.query(Team).filter_by(name="Default").one().id


def test_editor_and_processor_capabilities_are_enforced_by_api(authenticated_client):
    user = main.app.dependency_overrides[main._current_user]()
    source = make_pdf()
    user.can_edit = False
    user.can_process = True
    editor_denied = authenticated_client.post(
        "/api/process/rotate",
        data={"degrees": "90"},
        files={"files": ("source.pdf", source, "application/pdf")},
    )
    assert editor_denied.status_code == 403
    processor_allowed = authenticated_client.post(
        "/api/process/compress",
        files={"files": ("source.pdf", source, "application/pdf")},
    )
    assert processor_allowed.status_code == 200

    user.can_edit = True
    user.can_process = False
    processor_denied = authenticated_client.post(
        "/api/process/compress",
        files={"files": ("source.pdf", source, "application/pdf")},
    )
    assert processor_denied.status_code == 403
    editor_allowed = authenticated_client.post(
        "/api/process/rotate",
        data={"degrees": "90"},
        files={"files": ("source.pdf", source, "application/pdf")},
    )
    assert editor_allowed.status_code == 200


def test_last_active_owner_cannot_be_demoted(authenticated_client):
    admin = main.app.dependency_overrides[main._current_user]()
    admin.role = "org_owner"
    with Session(main.engine) as db:
        owner = User(
            issuer="tenant",
            subject="only-owner",
            email="only-owner@example.com",
            display_name="Only Owner",
            role="org_owner",
        )
        db.add(owner)
        db.commit()
        db.refresh(owner)
        owner_id = owner.id

    response = authenticated_client.patch(
        f"/api/admin/users/{owner_id}", json={"role": "member"}
    )
    assert response.status_code == 409
    assert "At least one active organization owner" in response.json()["detail"]


def test_configured_owner_bootstraps_existing_sso_account(authenticated_client, monkeypatch):
    user = main.app.dependency_overrides[main._current_user]()
    with Session(main.engine) as db:
        db_user = db.get(User, user.id)
        db_user.email = "OWNER@example.com"
        db.commit()
    monkeypatch.setattr(main, "INITIAL_ADMIN_EMAILS", frozenset({"owner@example.com"}))
    main._bootstrap_configured_owners(main.engine)
    with Session(main.engine) as db:
        assert db.get(User, user.id).role == "org_owner"


def configure_local_admin(monkeypatch, email="local-owner@example.com"):
    salt = b"test-local-admin-salt"
    password = "test-only-strong-password"
    monkeypatch.setattr(main, "LOCAL_ADMIN_USERNAME", "local-owner")
    monkeypatch.setattr(main, "LOCAL_ADMIN_EMAIL", email)
    monkeypatch.setattr(main, "LOCAL_ADMIN_PASSWORD_SALT", salt)
    monkeypatch.setattr(
        main,
        "LOCAL_ADMIN_PASSWORD_DIGEST",
        hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=32,
            maxmem=64 * 1024 * 1024,
        ),
    )
    return password


def test_local_admin_login_uses_scrypt_and_existing_owner(authenticated_client, monkeypatch):
    user = main.app.dependency_overrides[main._current_user]()
    with Session(main.engine) as db:
        db_user = db.get(User, user.id)
        db_user.email = "local-owner@example.com"
        db_user.role = "member"
        db.commit()
    password = configure_local_admin(monkeypatch)
    main._local_admin_attempts.clear()

    response = authenticated_client.post(
        "/auth/local-login",
        data={"username": "local-owner", "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with Session(main.engine) as db:
        signed_in_user = db.get(User, user.id)
        assert signed_in_user.role == "org_owner"
        assert signed_in_user.last_login_at is not None


def test_local_admin_login_is_disabled_without_server_credentials(authenticated_client, monkeypatch):
    monkeypatch.setattr(main, "LOCAL_ADMIN_USERNAME", "")
    monkeypatch.setattr(main, "LOCAL_ADMIN_EMAIL", "")
    monkeypatch.setattr(main, "LOCAL_ADMIN_PASSWORD_DIGEST", b"")
    response = authenticated_client.post(
        "/auth/local-login",
        data={"username": "admin", "password": "not-a-real-password"},
        follow_redirects=False,
    )
    assert response.status_code == 503
    assert "LOCAL_ADMIN_PASSWORD" in response.json()["detail"]
    assert authenticated_client.get("/api/auth/options").json()["local_admin_enabled"] is False


def test_local_admin_login_rate_limits_invalid_credentials(authenticated_client, monkeypatch):
    password = configure_local_admin(monkeypatch)
    main._local_admin_attempts.clear()
    for _ in range(main.LOCAL_ADMIN_MAX_ATTEMPTS):
        response = authenticated_client.post(
            "/auth/local-login",
            data={"username": "local-owner", "password": f"{password}wrong"},
            follow_redirects=False,
        )
        assert response.status_code == 401
    blocked = authenticated_client.post(
        "/auth/local-login",
        data={"username": "local-owner", "password": password},
        follow_redirects=False,
    )
    assert blocked.status_code == 429
    main._local_admin_attempts.clear()


def test_local_admin_account_cannot_use_an_old_sso_session(authenticated_client, monkeypatch):
    user = main.app.dependency_overrides[main._current_user]()
    configure_local_admin(monkeypatch)
    with Session(main.engine) as db:
        db_user = db.get(User, user.id)
        db_user.email = "local-owner@example.com"
        db.commit()
    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "headers": [],
            "session": {"user_id": user.id, "auth_method": "entra"},
        }
    )
    with Session(main.engine) as db, pytest.raises(HTTPException) as error:
        main._current_user(request, db)
    assert error.value.status_code == 403
    assert request.session == {}


def test_deactivated_account_session_is_rejected(authenticated_client):
    user = main.app.dependency_overrides[main._current_user]()
    from starlette.requests import Request

    with Session(main.engine) as db:
        db_user = db.get(User, user.id)
        db_user.is_active = False
        db.commit()
    request = Request(
        {"type": "http", "headers": [], "session": {"user_id": user.id}}
    )
    with Session(main.engine) as db, pytest.raises(HTTPException) as error:
        main._current_user(request, db)
    assert error.value.status_code == 403
    assert request.session == {}


def test_existing_user_schema_is_migrated_for_admin_fields():
    legacy_engine = create_engine("sqlite://")
    with legacy_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, issuer VARCHAR(512) NOT NULL, "
                "subject VARCHAR(255) NOT NULL, email VARCHAR(320), "
                "display_name VARCHAR(255) NOT NULL, created_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO users (id, issuer, subject, display_name, created_at) "
                "VALUES (1, 'issuer', 'subject', 'Legacy User', CURRENT_TIMESTAMP)"
            )
        )
    try:
        main._migrate_user_admin_columns(legacy_engine)
        columns = {column["name"] for column in inspect(legacy_engine).get_columns("users")}
        assert {
            "role",
            "is_active",
            "last_login_at",
            "team_id",
            "can_edit",
            "can_process",
        } <= columns
        indexes = {index["name"] for index in inspect(legacy_engine).get_indexes("users")}
        assert {"ix_users_email", "ix_users_role_active", "ix_users_team_id"} <= indexes
        with legacy_engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT role, is_active, last_login_at, can_edit, can_process "
                    "FROM users WHERE id = 1"
                )
            ).one()
        assert row.role == "member"
        assert row.is_active in (True, 1)
        assert row.last_login_at is None
        assert row.can_edit in (True, 1)
        assert row.can_process in (True, 1)
    finally:
        legacy_engine.dispose()
