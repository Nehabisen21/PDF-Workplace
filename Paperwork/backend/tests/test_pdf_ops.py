import io
import zipfile

import pymupdf
import pytest
from pypdf import PdfReader
from pypdf.constants import UserAccessPermissions
from reportlab.pdfgen import canvas

import app.pdf_ops as pdf_ops
from app.pdf_ops import _single_pdf, _split_by_size, process_pdf


def make_pdf(*texts: str) -> bytes:
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=(300, 300))
    for text in texts:
        pdf.drawString(30, 250, text)
        pdf.showPage()
    pdf.save()
    return output.getvalue()


def read_pdf(data: bytes) -> PdfReader:
    return PdfReader(io.BytesIO(data))


def test_merge_and_extract_selected_pages():
    first = make_pdf("first")
    second = make_pdf("second", "third")
    merged, name, count = process_pdf("merge", [("a.pdf", first), ("b.pdf", second)], {})
    assert name == "merged.pdf"
    assert count == 3
    assert len(read_pdf(merged).pages) == 3

    extracted, name, count = process_pdf("extract", [("all.pdf", merged)], {"pages": "3,1"})
    assert name == "extracted-pages.pdf"
    assert count == 2
    assert [page.extract_text().strip() for page in read_pdf(extracted).pages] == ["third", "first"]


def test_split_creates_one_pdf_per_range():
    source = make_pdf("one", "two", "three")
    result, name, count = process_pdf("split", [("source.pdf", source)], {"groups": "1-2;3"})
    assert name == "split-pages.zip"
    assert count == 3
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert archive.namelist() == ["part-1.pdf", "part-2.pdf"]
        assert len(read_pdf(archive.read("part-1.pdf")).pages) == 2
        assert len(read_pdf(archive.read("part-2.pdf")).pages) == 1


def test_split_by_page_count_and_document_count():
    first = make_pdf("one", "two", "three")
    second = make_pdf("four", "five")
    result, name, count = process_pdf(
        "split", [("first.pdf", first)], {"split_mode": "page_count", "split_value": "2"}
    )
    assert name == "split-page-count.zip"
    assert count == 3
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert [len(read_pdf(archive.read(item)).pages) for item in archive.namelist()] == [2, 1]

    result, name, count = process_pdf(
        "split",
        [("first.pdf", first), ("second.pdf", second)],
        {"split_mode": "document_count", "split_value": "1"},
    )
    assert name == "split-documents.zip"
    assert count == 5
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert [len(read_pdf(archive.read(item)).pages) for item in archive.namelist()] == [3, 2]

    result, _, _ = process_pdf(
        "split",
        [("first.pdf", first), ("second.pdf", second)],
        {"split_mode": "document_count", "split_value": "2"},
    )
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert [len(read_pdf(archive.read(item)).pages) for item in archive.namelist()] == [5]


def test_split_by_chapters_sections_and_divider_pages():
    chapters = make_pdf("Introduction", "Chapter 1", "Chapter 2")
    result, _, _ = process_pdf(
        "split", [("source.pdf", chapters)], {"split_mode": "chapters"}
    )
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert [len(read_pdf(archive.read(item)).pages) for item in archive.namelist()] == [1, 1, 1]

    sections = make_pdf("Introduction", "1.1 First section", "1.2 Second section")
    result, _, _ = process_pdf(
        "split", [("source.pdf", sections)], {"split_mode": "sections"}
    )
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert [len(read_pdf(archive.read(item)).pages) for item in archive.namelist()] == [1, 1, 1]

    bookmarked = pymupdf.open(
        stream=make_pdf("chapter one", "section 1.1", "chapter two"),
        filetype="pdf",
    )
    bookmarked.set_toc(
        [[1, "Chapter one", 1], [1, "Chapter two", 3], [2, "Section 1.1", 2]]
    )
    bookmarked_bytes = bookmarked.tobytes()
    bookmarked.close()
    result, _, _ = process_pdf(
        "split", [("bookmarked.pdf", bookmarked_bytes)], {"split_mode": "chapters"}
    )
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert [len(read_pdf(archive.read(item)).pages) for item in archive.namelist()] == [2, 1]
    result, _, _ = process_pdf(
        "split", [("bookmarked.pdf", bookmarked_bytes)], {"split_mode": "sections"}
    )
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert [len(read_pdf(archive.read(item)).pages) for item in archive.namelist()] == [1, 2]

    divided = make_pdf("first", "DIVIDER", "second", "DIVIDER", "third")
    result, _, _ = process_pdf(
        "split",
        [("source.pdf", divided)],
        {"split_mode": "page_divider", "divider_text": "divider"},
    )
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert [
            [page.extract_text().strip() for page in read_pdf(archive.read(item)).pages]
            for item in archive.namelist()
        ] == [["first"], ["second"], ["third"]]


def test_split_by_file_size_and_printable_chunks():
    source = make_pdf("one", "two", "three")
    pages = read_pdf(source).pages
    max_bytes = max(len(_single_pdf(pages, [index])) for index in range(1, 4))
    chunks = _split_by_size(pages, max_bytes)
    assert len(chunks) == 3
    assert all(len(chunk) <= max_bytes for chunk in chunks)
    assert sum(len(read_pdf(chunk).pages) for chunk in chunks) == 3

    oversized = io.BytesIO()
    pdf = canvas.Canvas(oversized, pagesize=(1000, 1000))
    pdf.drawString(40, 950, "Large format page")
    pdf.save()
    result, name, count = process_pdf(
        "split",
        [("oversized.pdf", oversized.getvalue())],
        {"split_mode": "printable_chunks"},
    )
    assert name == "printable-chunks.pdf"
    assert count == 4
    assert len(read_pdf(result).pages) == 4


def test_crop_applies_selected_area_to_all_pages_and_rejects_invalid_area():
    source = make_pdf("first", "second")
    cropped, name, count = process_pdf(
        "crop",
        [("source.pdf", source)],
        {"crop_x": "0.25", "crop_y": "0.25", "crop_width": "0.5", "crop_height": "0.5"},
    )
    assert name == "cropped.pdf"
    assert count == 2
    document = pymupdf.open(stream=cropped, filetype="pdf")
    assert [page.rect for page in document] == [
        pymupdf.Rect(0, 0, 150, 150),
        pymupdf.Rect(0, 0, 150, 150),
    ]
    document.close()
    with pytest.raises(ValueError, match="outside the page"):
        process_pdf(
            "crop",
            [("source.pdf", source)],
            {"crop_x": "0.8", "crop_y": "0", "crop_width": "0.5", "crop_height": "1"},
        )

    rotated_source = io.BytesIO()
    pdf = canvas.Canvas(rotated_source, pagesize=(400, 200))
    pdf.drawString(30, 150, "rotated")
    pdf.showPage()
    pdf.save()
    rotated_document = pymupdf.open(stream=rotated_source.getvalue(), filetype="pdf")
    rotated_document[0].set_rotation(90)
    rotated_bytes = rotated_document.tobytes()
    rotated_document.close()
    rotated_crop, _, _ = process_pdf(
        "crop",
        [("rotated.pdf", rotated_bytes)],
        {"crop_x": "0.25", "crop_y": "0.25", "crop_width": "0.5", "crop_height": "0.5"},
    )
    rotated_result = pymupdf.open(stream=rotated_crop, filetype="pdf")
    assert rotated_result[0].rect.width == pytest.approx(100)
    assert rotated_result[0].rect.height == pytest.approx(200)
    rotated_result.close()


def test_reorder_requires_each_page_once():
    source = make_pdf("one", "two", "three")
    result, _, _ = process_pdf("reorder", [("source.pdf", source)], {"pages": "3,1,2"})
    assert [page.extract_text().strip() for page in read_pdf(result).pages] == ["three", "one", "two"]
    with pytest.raises(ValueError, match="duplicates"):
        process_pdf("reorder", [("source.pdf", source)], {"pages": "1,1,2"})


def test_watermark_and_redaction():
    source = make_pdf("Confidential document")
    watermarked, _, _ = process_pdf(
        "watermark", [("source.pdf", source)], {"text": "INTERNAL"}
    )
    assert len(read_pdf(watermarked).pages) == 1

    redacted, name, _ = process_pdf(
        "redact", [("source.pdf", source)], {"redact_text": "Confidential"}
    )
    assert name == "redacted.pdf"
    document = pymupdf.open(stream=redacted, filetype="pdf")
    assert "Confidential" not in document[0].get_text()
    assert "document" in document[0].get_text()
    document.close()


def test_rotate_and_compress():
    source = make_pdf("page")
    rotated, _, _ = process_pdf("rotate", [("source.pdf", source)], {"degrees": "90"})
    assert read_pdf(rotated).pages[0].get("/Rotate") == 90
    compressed, name, _ = process_pdf("compress", [("source.pdf", source)], {})
    assert name == "compressed.pdf"
    assert len(read_pdf(compressed).pages) == 1


def test_invalid_page_selection_is_rejected():
    source = make_pdf("one", "two")
    with pytest.raises(ValueError, match="between 1 and 2"):
        process_pdf("extract", [("source.pdf", source)], {"pages": "1-3"})


def test_image_pdf_conversions():
    source = make_pdf("image source")
    document = pymupdf.open(stream=source, filetype="pdf")
    pixmap = document[0].get_pixmap()
    document.close()
    png = pixmap.tobytes("png")
    jpeg = pixmap.tobytes("jpeg")

    converted, name, count = process_pdf(
        "images_to_pdf", [("page.png", png), ("page.jpg", jpeg)], {}
    )
    assert name == "images.pdf"
    assert count == 2
    assert len(read_pdf(converted).pages) == 2

    images, name, count = process_pdf("pdf_to_images", [("source.pdf", source)], {})
    assert name == "page-images.zip"
    assert count == 1
    with zipfile.ZipFile(io.BytesIO(images)) as archive:
        assert archive.namelist() == ["page-001.png"]
        image_document = pymupdf.open(stream=archive.read("page-001.png"), filetype="png")
        assert image_document.page_count == 1
        image_document.close()


def test_password_protect_and_unlock():
    source = make_pdf("protected content")
    protected, name, count = process_pdf(
        "protect", [("source.pdf", source)], {"protect_password": "correct horse"}
    )
    assert name == "protected.pdf"
    assert count == 1
    assert read_pdf(protected).is_encrypted
    default_permissions = read_pdf(protected)
    assert default_permissions.decrypt("correct horse")
    assert default_permissions.user_access_permissions & UserAccessPermissions.PRINT
    assert default_permissions.user_access_permissions & UserAccessPermissions.EXTRACT

    with pytest.raises(ValueError, match="missing or incorrect"):
        process_pdf("unlock", [("protected.pdf", protected)], {"unlock_password": "wrong"})
    unlocked, name, _ = process_pdf(
        "unlock", [("protected.pdf", protected)], {"unlock_password": "correct horse"}
    )
    assert name == "unlocked.pdf"
    assert not read_pdf(unlocked).is_encrypted
    assert read_pdf(unlocked).pages[0].extract_text().strip() == "protected content"
    password_removed, name, _ = process_pdf(
        "remove_password",
        [("protected.pdf", protected)],
        {"unlock_password": "correct horse"},
    )
    assert name == "password-removed.pdf"
    assert not read_pdf(password_removed).is_encrypted

    with pytest.raises(ValueError, match="8 and 127 UTF-8 bytes"):
        process_pdf("protect", [("source.pdf", source)], {"protect_password": "short"})


def test_password_protection_and_permission_changes():
    source = make_pdf("restricted document")
    protected, _, _ = process_pdf(
        "protect",
        [("source.pdf", source)],
        {
            "protect_password": "open password",
            "owner_password": "original owner password",
            "permission_actions": "print",
        },
    )
    reader = read_pdf(protected)
    assert reader.decrypt("open password")
    assert reader.user_access_permissions & UserAccessPermissions.PRINT
    assert not reader.user_access_permissions & UserAccessPermissions.EXTRACT
    with pytest.raises(ValueError, match="owner password"):
        process_pdf(
            "change_permissions",
            [("protected.pdf", protected)],
            {
                "source_password": "open password",
                "owner_password": "new owner password",
                "permission_actions": "print",
            },
        )

    changed, name, _ = process_pdf(
        "change_permissions",
        [("protected.pdf", protected)],
        {
            "source_password": "original owner password",
            "owner_password": "new owner password",
            "permission_actions": "copy,forms",
        },
    )
    assert name == "permissions-changed.pdf"
    reader = read_pdf(changed)
    assert reader.decrypt("")
    assert reader.user_access_permissions & UserAccessPermissions.EXTRACT
    assert not reader.user_access_permissions & UserAccessPermissions.PRINT


@pytest.mark.parametrize("operation", ["auto_form_detection", "split_scanned_photos"])
def test_vision_tools_report_optional_dependency(operation, monkeypatch):
    monkeypatch.setattr(pdf_ops, "cv2", None)
    with pytest.raises(ValueError, match="require the optional OpenCV package"):
        process_pdf(operation, [("source.pdf", make_pdf("source"))], {})


def test_stamp_text_and_image_options():
    source = make_pdf("Original page")
    image_document = pymupdf.open()
    image_page = image_document.new_page(width=40, height=40)
    image_page.draw_rect(pymupdf.Rect(0, 0, 40, 40), color=(1, 0, 0), fill=(1, 0, 0))
    stamp_image = image_page.get_pixmap().tobytes("png")
    image_document.close()

    stamped, name, count = process_pdf(
        "stamp",
        [("source.pdf", source)],
        {
            "stamp_mode": "both",
            "stamp_text": "APPROVED",
            "stamp_image": stamp_image,
            "stamp_image_type": "png",
            "stamp_position": "top_right",
        },
    )
    assert name == "stamped.pdf"
    assert count == 1
    page = read_pdf(stamped).pages[0]
    assert "Original page" in page.extract_text()
    assert "APPROVED" in page.extract_text()
    rendered = pymupdf.open(stream=stamped, filetype="pdf")
    assert rendered[0].get_images()
    rendered.close()

    text_only, _, _ = process_pdf(
        "stamp",
        [("source.pdf", source)],
        {"stamp_mode": "text", "stamp_text": "TEXT ONLY"},
    )
    assert "TEXT ONLY" in read_pdf(text_only).pages[0].extract_text()

    image_only, _, _ = process_pdf(
        "stamp",
        [("source.pdf", source)],
        {
            "stamp_mode": "image",
            "stamp_image": stamp_image,
            "stamp_image_type": "png",
        },
    )
    rendered = pymupdf.open(stream=image_only, filetype="pdf")
    assert rendered[0].get_images()
    rendered.close()


def make_read_only_form_pdf() -> bytes:
    document = pymupdf.open(stream=make_pdf("form background"), filetype="pdf")
    widget = pymupdf.Widget()
    widget.field_name = "LockedField"
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.field_value = "Locked value"
    widget.field_flags = pymupdf.PDF_FIELD_IS_READ_ONLY
    widget.rect = pymupdf.Rect(30, 40, 180, 70)
    document[0].add_widget(widget)
    document[0].add_text_annot((40, 170), "Review note")
    result = document.tobytes()
    document.close()
    return result


def test_unlock_pdf_forms_and_flatten_appearances():
    source = make_read_only_form_pdf()
    unlocked, name, _ = process_pdf(
        "unlock_forms", [("source.pdf", source)], {"form_password": ""}
    )
    assert name == "unlocked-forms.pdf"
    document = pymupdf.open(stream=unlocked, filetype="pdf")
    widgets = list(document[0].widgets() or [])
    assert len(widgets) == 1
    assert not widgets[0].field_flags & pymupdf.PDF_FIELD_IS_READ_ONLY
    document.close()

    flattened, name, _ = process_pdf("flatten", [("source.pdf", source)], {})
    assert name == "flattened.pdf"
    document = pymupdf.open(stream=flattened, filetype="pdf")
    assert list(document[0].widgets() or []) == []
    assert list(document[0].annots() or []) == []
    assert "form background" in document[0].get_text()
    document.close()


def test_sanitize_removes_metadata_and_annotations():
    document = pymupdf.open(stream=make_pdf("sanitized page"), filetype="pdf")
    document.set_metadata({"author": "private author", "title": "private title"})
    document[0].add_text_annot((40, 170), "private comment")
    source = document.tobytes()
    document.close()

    sanitized, name, _ = process_pdf("sanitize", [("source.pdf", source)], {})
    assert name == "sanitized.pdf"
    result = pymupdf.open(stream=sanitized, filetype="pdf")
    assert not result.metadata.get("author")
    assert not result.metadata.get("title")
    assert list(result[0].annots() or []) == []
    result.close()


def test_removal_tools_and_cleanup_automation():
    document = pymupdf.open()
    first = document.new_page(width=300, height=300)
    first.insert_text((30, 40), "keep this text")
    image = pymupdf.open()
    image_page = image.new_page(width=20, height=20)
    image_page.draw_rect(pymupdf.Rect(0, 0, 20, 20), color=(1, 0, 0), fill=(1, 0, 0))
    image_bytes = image_page.get_pixmap().tobytes("png")
    image.close()
    first.insert_image(pymupdf.Rect(30, 60, 90, 120), stream=image_bytes)
    first.add_text_annot((40, 150), "remove this note")
    document.new_page(width=300, height=300)
    third = document.new_page(width=300, height=300)
    third.insert_text((30, 40), "keep this too")
    source = document.tobytes()
    document.close()

    removed, _, count = process_pdf("remove_pages", [("source.pdf", source)], {"pages": "2"})
    assert count == 2
    assert "keep this text" in read_pdf(removed).pages[0].extract_text()

    without_blanks, _, count = process_pdf(
        "remove_blank_pages", [("source.pdf", source)], {}
    )
    assert count == 2
    assert [page.extract_text().strip() for page in read_pdf(without_blanks).pages] == [
        "keep this text",
        "keep this too",
    ]

    without_annotations, _, _ = process_pdf(
        "remove_annotations", [("source.pdf", source)], {}
    )
    annotated_document = pymupdf.open(stream=without_annotations, filetype="pdf")
    assert all(list(page.annots() or []) == [] for page in annotated_document)
    annotated_document.close()

    without_images, _, _ = process_pdf("remove_images", [("source.pdf", source)], {})
    image_document = pymupdf.open(stream=without_images, filetype="pdf")
    remaining_image = image_document[0].get_pixmap(
        matrix=pymupdf.Matrix(1, 1),
        clip=pymupdf.Rect(30, 60, 90, 120),
        colorspace=pymupdf.csRGB,
        alpha=False,
    )
    assert set(remaining_image.samples) == {255}
    assert "keep this text" in image_document[0].get_text()
    image_document.close()

    automated, name, count = process_pdf(
        "automate",
        [("source.pdf", source)],
        {"automation_steps": '["remove_annotations","remove_images","compress"]'},
    )
    assert name == "automated.pdf"
    assert count == 3
    result = pymupdf.open(stream=automated, filetype="pdf")
    assert not list(result[0].annots() or [])
    page_image = result[0].get_pixmap(
        matrix=pymupdf.Matrix(1, 1),
        clip=pymupdf.Rect(30, 60, 90, 120),
        colorspace=pymupdf.csRGB,
        alpha=False,
    )
    assert set(page_image.samples) == {255}
    assert not list(result[1].annots() or [])
    result.close()

    with pytest.raises(ValueError, match="At least one page must remain"):
        process_pdf("remove_pages", [("source.pdf", source)], {"pages": "1-3"})

    signed_document = pymupdf.open(stream=make_pdf("signed content"), filetype="pdf")
    signature = pymupdf.Widget()
    signature.field_name = "CertificateSignature"
    signature.field_type = pymupdf.PDF_WIDGET_TYPE_SIGNATURE
    signature.rect = pymupdf.Rect(30, 60, 180, 90)
    signed_document[0].add_widget(signature)
    signed_source = signed_document.tobytes()
    signed_document.close()
    unsigned, _, _ = process_pdf(
        "remove_certificate_sign", [("signed.pdf", signed_source)], {}
    )
    unsigned_document = pymupdf.open(stream=unsigned, filetype="pdf")
    assert not list(unsigned_document[0].widgets() or [])
    unsigned_document.close()


def test_text_image_annotation_color_and_overlay_operations():
    source = make_pdf("base content")
    image_document = pymupdf.open()
    image_page = image_document.new_page(width=20, height=20)
    image_page.draw_rect(pymupdf.Rect(0, 0, 20, 20), color=(0, 0, 1), fill=(0, 0, 1))
    image_bytes = image_page.get_pixmap().tobytes("png")
    image_document.close()

    added_text, _, _ = process_pdf(
        "add_text",
        [("source.pdf", source)],
        {"text": "inserted text", "page_number": "1", "x": "40", "y": "80"},
    )
    assert "inserted text" in read_pdf(added_text).pages[0].extract_text()

    annotated, _, _ = process_pdf(
        "annotate",
        [("source.pdf", source)],
        {"annotation_text": "review this", "page_number": "1", "x": "40", "y": "80"},
    )
    annotated_document = pymupdf.open(stream=annotated, filetype="pdf")
    assert len(list(annotated_document[0].annots() or [])) == 1
    annotated_document.close()

    image_added, _, _ = process_pdf(
        "add_image",
        [("source.pdf", source)],
        {
            "tool_image": image_bytes,
            "tool_image_type": "png",
            "page_number": "1",
            "x": "40",
            "y": "80",
            "width": "50",
            "height": "50",
        },
    )
    image_document = pymupdf.open(stream=image_added, filetype="pdf")
    assert image_document[0].get_images()
    image_document.close()

    adjusted, _, _ = process_pdf(
        "adjust_colors", [("source.pdf", source)], {"brightness": "1.2", "contrast": "1.1"}
    )
    inverted, _, _ = process_pdf(
        "color_transform", [("source.pdf", source)], {"color_mode": "invert"}
    )
    replaced, _, _ = process_pdf(
        "color_transform",
        [("source.pdf", source)],
        {
            "color_mode": "replace",
            "source_color": "#000000",
            "replacement_color": "#FF0000",
            "color_tolerance": "24",
        },
    )
    for raster_pdf in (adjusted, inverted, replaced):
        rendered = pymupdf.open(stream=raster_pdf, filetype="pdf")
        assert rendered[0].get_images()
        assert rendered[0].get_text().strip() == ""
        rendered.close()

    overlay, _, count = process_pdf(
        "overlay", [("base.pdf", source), ("overlay.pdf", make_pdf("overlay content"))], {}
    )
    assert count == 1
    overlay_document = pymupdf.open(stream=overlay, filetype="pdf")
    assert "base content" in overlay_document[0].get_text()
    assert "overlay content" in overlay_document[0].get_text()
    overlay_document.close()

    with pytest.raises(ValueError, match="exactly two"):
        process_pdf("overlay", [("base.pdf", source)], {})


def test_form_editor_detection_repair_and_auto_rename():
    if pdf_ops.cv2 is None:
        pytest.skip("OpenCV is an optional dependency for automatic form detection.")
    source = make_read_only_form_pdf()
    from app.pdf_ops import _get_form_fields

    assert _get_form_fields(source) == [
        {"name": "LockedField", "type": "Text", "value": "Locked value", "page": 1}
    ]
    edited, _, _ = process_pdf(
        "form_editor", [("form.pdf", source)], {"form_values": '{"LockedField":"Updated"}'}
    )
    assert _get_form_fields(edited)[0]["value"] == "Updated"

    document = pymupdf.open()
    page = document.new_page(width=300, height=300)
    page.draw_rect(pymupdf.Rect(50, 50, 250, 100), color=(0, 0, 0), width=1)
    blank_box_pdf = document.tobytes()
    document.close()
    detected, _, _ = process_pdf("auto_form_detection", [("boxes.pdf", blank_box_pdf)], {})
    detected_document = pymupdf.open(stream=detected, filetype="pdf")
    widgets = list(detected_document[0].widgets() or [])
    assert len(widgets) == 1
    assert widgets[0].field_type == pymupdf.PDF_WIDGET_TYPE_TEXT
    detected_document.close()

    renamed, name, count = process_pdf("auto_rename", [("source.pdf", make_pdf("Annual Report"))], {})
    assert name == "Annual-Report.pdf"
    assert count == 1
    repaired, name, count = process_pdf("repair", [("source.pdf", source)], {})
    assert name == "repaired.pdf"
    assert count == 1


def test_split_scanned_photos_uses_detected_image_regions():
    if pdf_ops.cv2 is None:
        pytest.skip("OpenCV is an optional dependency for scanned-photo splitting.")
    image_document = pymupdf.open()
    image_page = image_document.new_page(width=20, height=20)
    image_page.draw_rect(pymupdf.Rect(0, 0, 20, 20), color=(0, 0, 1), fill=(0, 0, 1))
    image_bytes = image_page.get_pixmap().tobytes("png")
    image_document.close()

    document = pymupdf.open()
    page = document.new_page(width=300, height=300)
    page.insert_image(pymupdf.Rect(20, 20, 120, 120), stream=image_bytes)
    page.insert_image(pymupdf.Rect(160, 160, 260, 260), stream=image_bytes)
    source = document.tobytes()
    document.close()

    result, name, count = process_pdf(
        "split_scanned_photos", [("photos.pdf", source)], {}
    )
    assert name == "split-photos.pdf"
    assert count == 2
    assert len(read_pdf(result).pages) == 2


def test_scale_pages_booklet_and_poster_tools():
    source = make_pdf("a", "b", "c", "d")

    scaled, name, count = process_pdf(
        "scale_pages", [("doc.pdf", source)], {"scale_factor": "1.5", "page_size": "A4"}
    )
    assert name == "scaled-pages.pdf"
    assert count == 4
    with pymupdf.open(stream=scaled) as document:
        assert document.page_count == 4
        assert document[0].rect.width > 0

    booklet, name, count = process_pdf(
        "booklet_imposition", [("doc.pdf", source)], {"add_border": "true", "gutter_size": "4"}
    )
    assert name == "booklet-imposition.pdf"
    with pymupdf.open(stream=source) as source_doc, pymupdf.open(stream=booklet) as document:
        assert document.page_count == 2  # one sheet: front and back
        # Booklet paper is landscape relative to the source page.
        assert document[0].rect.width == pytest.approx(source_doc[0].rect.height)
        assert document[0].rect.height == pytest.approx(source_doc[0].rect.width)

    with pytest.raises(ValueError):
        process_pdf("booklet_imposition", [("doc.pdf", source)], {"spine_location": "MIDDLE"})

    poster, name, count = process_pdf(
        "poster_pdf", [("doc.pdf", source)], {"page_size": "A4", "x_factor": "2", "y_factor": "1"}
    )
    assert name == "poster-pages.pdf"
    assert count == 8
    with pymupdf.open(stream=poster) as document:
        assert document.page_count == 8

    with pytest.raises(ValueError):
        process_pdf("scale_pages", [("doc.pdf", source)], {"scale_factor": "99"})
