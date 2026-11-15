"""Task 1: file detection, PDF text, OCR, images, spreadsheets, corrupted input."""
import io

import pytest
from PIL import Image

from app.pipeline import PipelineError
from app.pipeline.detect import detect_file_type
from app.pipeline.images import extract_image
from app.pipeline.pdf import extract_pdf
from app.pipeline.segment import page_invoice_number, segment_pages
from app.pipeline.spreadsheets import extract_csv, extract_excel
from eval import generate_dataset as gen


# ---------- detection ----------
@pytest.mark.parametrize("name,head,kind", [
    ("a.pdf", b"%PDF-1.7\n...", "pdf"),
    ("a.PNG", b"\x89PNG\r\n\x1a\n....", "image"),
    ("a.jpeg", b"\xff\xd8\xff\xe0....", "image"),
    ("a.tif", b"II*\x00....", "image"),
    ("a.xlsx", b"PK\x03\x04....", "xlsx"),
    ("a.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "xls"),
    ("a.csv", b"a,b,c\n1,2,3", "csv"),
])
def test_detect_valid(name, head, kind):
    assert detect_file_type(name, head) == kind


@pytest.mark.parametrize("name,head,code", [
    ("a.exe", b"MZ....", "unsupported_type"),
    ("a.pdf", b"", "empty_file"),
    ("a.png", b"%PDF-1.4 ...", "type_mismatch"),
    ("a.pdf", b"hello world", "corrupted_file"),
    ("a.csv", b"\x00\x01\x02binary", "type_mismatch"),
])
def test_detect_rejects(name, head, code):
    with pytest.raises(PipelineError) as e:
        detect_file_type(name, head)
    assert e.value.code == code


# ---------- PDF ----------
def test_digital_pdf_text(invoice_pdf, tmp_file):
    data, inv = invoice_pdf()
    pages = extract_pdf(tmp_file("a.pdf", data))
    assert len(pages) == 1 and pages[0].method == "text"
    assert inv.invoice_number in pages[0].text and inv.supplier_name in pages[0].text


def test_multipage_pdf_and_progress(make_invoice, rng, tmp_file):
    invs = [make_invoice() for _ in range(30)]
    path = tmp_file("batch.pdf", gen.pdf_bytes(invs, rng, terms_page_every=10))
    seen = []
    pages = extract_pdf(path, lambda d, t: seen.append((d, t)))
    assert len(pages) == 33 and [p.page_number for p in pages] == list(range(1, 34))
    assert seen[-1] == (33, 33)


def test_scanned_pdf_uses_ocr(scanned_pdf, tmp_file):
    data, inv = scanned_pdf()
    pages = extract_pdf(tmp_file("scan.pdf", data))
    assert pages[0].method == "ocr" and pages[0].ocr_confidence > 60
    assert inv.invoice_number.replace("-", "") in pages[0].text.replace("-", "").replace(" ", "")


def test_corrupted_pdf(invoice_pdf, tmp_file):
    data, _ = invoice_pdf()
    with pytest.raises(PipelineError) as e:
        extract_pdf(tmp_file("bad.pdf", b"%PDF-1.7\n" + b"\x00garbage" * 300))
    assert e.value.code == "corrupted_pdf"


def test_encrypted_pdf(invoice_pdf, tmp_file):
    from pypdf import PdfReader, PdfWriter

    data, _ = invoice_pdf()
    w = PdfWriter()
    for p in PdfReader(io.BytesIO(data)).pages:
        w.add_page(p)
    w.encrypt("secret")
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(PipelineError) as e:
        extract_pdf(tmp_file("locked.pdf", buf.getvalue()))
    assert e.value.code in ("encrypted_pdf", "corrupted_pdf")


# ---------- images / OCR ----------
@pytest.mark.parametrize("fmt,level", [("PNG", "clean"), ("JPEG", "photo")])
def test_image_ocr(invoice_png, tmp_file, fmt, level):
    data, inv = invoice_png(level, fmt)
    pages = extract_image(tmp_file(f"x.{fmt.lower()}", data))
    text = pages[0].text
    assert pages[0].method == "ocr"
    assert inv.supplier_name.split()[0] in text
    assert f"{inv.total:,.2f}" in text


def test_low_resolution_image_still_reads_total(invoice_png, tmp_file):
    data, inv = invoice_png("scan", "JPEG", dpi=110)  # small, noisy, rotated
    text = extract_image(tmp_file("small.jpg", data))[0].text
    assert f"{inv.total:,.2f}" in text


def test_multipage_tiff(make_invoice, rng, tmp_file):
    frames = [gen.render(gen.pdf_bytes([make_invoice()], rng, [0]), 150)[0].convert("L") for _ in range(2)]
    buf = io.BytesIO()
    frames[0].save(buf, format="TIFF", save_all=True, append_images=frames[1:])
    pages = extract_image(tmp_file("m.tiff", buf.getvalue()))
    assert [p.page_number for p in pages] == [1, 2]


def test_truncated_image(invoice_png, tmp_file):
    data, _ = invoice_png(fmt="JPEG")
    with pytest.raises(PipelineError) as e:
        extract_image(tmp_file("t.jpg", data[: len(data) // 5]))
    assert e.value.code == "corrupted_image"


def test_blank_image_returns_little_text(tmp_file):
    buf = io.BytesIO()
    Image.new("RGB", (800, 1000), "white").save(buf, format="PNG")
    assert extract_image(tmp_file("blank.png", buf.getvalue()))[0].text.strip() == ""


# ---------- spreadsheets ----------
def test_xlsx_multiple_sheets_and_header(make_invoice, tmp_path):
    invs = [make_invoice() for _ in range(3)]
    p = tmp_path / "l.xlsx"
    gen.write_xlsx_ledger(p, invs)
    pages, tables = extract_excel(str(p), "xlsx")
    assert [t.sheet_name for t in tables] == ["AP Ledger", "Notes"]
    assert tables[0].header[:3] == ["Invoice No", "Invoice Date", "Supplier"]  # title rows skipped
    assert len(tables[0].rows) == 3
    assert invs[0].invoice_number in pages[0].text


def test_xls_legacy(make_invoice, tmp_path):
    p = tmp_path / "l.xls"
    gen.write_xls_ledger(p, [make_invoice() for _ in range(2)])
    _, tables = extract_excel(str(p), "xls")
    assert len(tables[0].rows) == 2


def test_csv_semicolon_decimal_comma(make_invoice, tmp_path):
    p = tmp_path / "eu.csv"
    gen.write_csv_lines(p, [make_invoice()], sep=";", decimal_comma=True)
    _, tables = extract_csv(str(p))
    assert tables[0].header[0] == "Invoice Number" and "," in tables[0].rows[0][6]


def test_csv_cp1252_encoding(tmp_path):
    p = tmp_path / "w.csv"
    p.write_bytes("Invoice No,Supplier,Total\nA1,Caf\xe9 Ltd,\xa310.00\n".encode("latin-1"))
    pages, _ = extract_csv(str(p))
    assert "Café Ltd" in pages[0].text and "£10.00" in pages[0].text


def test_corrupted_xlsx(tmp_path):
    p = tmp_path / "x.xlsx"
    p.write_bytes(b"PK\x03\x04 not really a zip archive")
    with pytest.raises(PipelineError) as e:
        extract_excel(str(p), "xlsx")
    assert e.value.code == "corrupted_spreadsheet"


# ---------- segmentation ----------
def test_page_invoice_number_ignores_inv_prefix_inside_value():
    assert page_invoice_number("INVOICE\nInvoice No: INV-27661-46") == "INV-27661-46"
    assert page_invoice_number("Please quote the invoice number with your payment") is None


def test_segmentation_keeps_continuation_pages(make_invoice, rng, tmp_file):
    invs = [make_invoice() for _ in range(4)]
    pages = extract_pdf(tmp_file("b.pdf", gen.pdf_bytes(invs, rng, [0, 1, 2, 0], terms_page_every=2)))
    segs = segment_pages(pages)
    assert len(segs) == 4
    assert segs[0].page_end - segs[0].page_start == 1  # invoice + T&C page


def test_segmentation_without_readable_numbers():
    from app.pipeline import PageResult
    pages = [
        PageResult(1, "INVOICE\nInvoice No: ###\nWidget 10.00\nTotal 10.00", "ocr"),
        PageResult(2, "Terms and conditions\nPay within 30 days", "ocr"),
        PageResult(3, "INVOICE\nInvoice No: ???\nGadget 20.00\nTotal 20.00", "ocr"),
    ]
    segs = segment_pages(pages)
    assert [(s.page_start, s.page_end) for s in segs] == [(1, 2), (3, 3)]
