"""Generate a labelled evaluation dataset with known ground truth.

    python -m eval.generate_dataset --out eval/dataset --seed 7

Produces digital PDFs (3 layouts), degraded scans, phone-style photos, a
multi-page TIFF, a multi-invoice batch PDF, Excel/XLS/CSV in several shapes,
invoices with deliberate errors, and corrupted files. labels.json records the
correct answer for every file.

Synthetic data is used because real supplier invoices contain personal and
commercial data; the generator varies layout, fonts, date formats, currencies,
separators and image quality so the extractor cannot overfit to one template.
"""
from __future__ import annotations

import argparse
import io
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

SUPPLIERS = ["Northwind Supplies Ltd", "Brightline Office Co", "Kestrel Logistics Ltd", "Harbour & Pike Stationers",
             "Meridian IT Services Ltd", "Oakfield Catering Ltd", "Bluewater Cleaning Services", "Granite Print Works",
             "Summit Electrical Ltd", "Willow Lane Furniture", "Atlas Freight GmbH", "Cobalt Software Inc"]
CUSTOMERS = ["Contoso Retail plc", "Fabrikam Holdings Ltd", "Scholared UK Ltd", "Tailspin Toys Ltd", "Litware Academy",
             "Adventure Works Cycles", "Proseware Clinics", "Wide World Importers"]
ITEMS = [("Printer paper A4 (box)", 18, 32), ("Toner cartridge black", 45, 95), ("Desk chair ergonomic", 120, 260),
         ("Network switch 24-port", 180, 420), ("Monthly IT support", 300, 900), ("Delivery charge", 8, 35),
         ("Cleaning service (weekly)", 60, 140), ("Catering - lunch platter", 25, 70), ("Business cards (500)", 20, 55),
         ("LED panel light", 30, 75), ("Cable management kit", 12, 28), ("Software licence (annual)", 150, 600),
         ("Whiteboard markers (pack)", 4, 11), ("Filing cabinet 4-drawer", 140, 320), ("Laptop stand", 18, 45)]
CURRENCIES = [("GBP", "£", 0.2), ("USD", "$", 0.0), ("EUR", "€", 0.19), ("AED", "AED ", 0.05)]
DATE_FORMATS = [lambda d: d.strftime("%d/%m/%Y"), lambda d: d.strftime("%B %d, %Y").replace(" 0", " "),
                lambda d: d.isoformat(), lambda d: d.strftime("%d %b %Y")]

TWO = Decimal("0.01")
REVIEW_CODES = {"TOTAL_MISMATCH", "LINE_ITEM_MATH", "DUPLICATE_INVOICE", "LINE_ITEMS_SUM_MISMATCH"}


def money(v: Decimal) -> str:
    return f"{v:,.2f}"


@dataclass
class Line:
    description: str
    quantity: int
    unit_price: Decimal
    amount: Decimal


@dataclass
class Inv:
    invoice_number: str
    invoice_date: date
    supplier_name: str
    customer_name: str | None
    currency: str
    symbol: str
    lines: list[Line]
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    tax_rate: float
    printed_total: Decimal | None = None  # differs from total when an error is injected
    errors: list[str] = field(default_factory=list)

    def label(self) -> dict:
        return {
            "invoice_number": self.invoice_number, "invoice_date": self.invoice_date.isoformat(),
            "supplier_name": self.supplier_name, "customer_name": self.customer_name,
            "subtotal": str(self.subtotal), "tax": str(self.tax),
            "total": str(self.printed_total if self.printed_total is not None else self.total),
            "currency": self.currency,
            "line_items": [{"description": l.description, "quantity": str(l.quantity), "unit_price": str(l.unit_price),
                            "amount": str(l.amount)} for l in self.lines],
        }


def make_invoice(rng: random.Random, n: int, error: str | None = None, cur=None) -> Inv:
    code, sym, rate = cur or rng.choice(CURRENCIES)
    lines = []
    for desc, lo, hi in rng.sample(ITEMS, rng.randint(2, 6)):
        q = rng.randint(1, 12)
        up = (Decimal(rng.uniform(lo, hi))).quantize(TWO)
        lines.append(Line(desc, q, up, (up * q).quantize(TWO)))
    sub = sum((l.amount for l in lines), Decimal(0))
    tax = (sub * Decimal(str(rate))).quantize(TWO, ROUND_HALF_UP)
    inv = Inv(
        invoice_number=rng.choice(["INV-", "INV", "SI-", "A", "2026/"]) + str(rng.randint(1000, 99999)) + (f"-{n}" if rng.random() < 0.3 else ""),
        invoice_date=date(2026, 1, 1) + timedelta(days=rng.randint(0, 290)),
        supplier_name=rng.choice(SUPPLIERS), customer_name=rng.choice(CUSTOMERS), currency=code, symbol=sym,
        lines=lines, subtotal=sub, tax=tax, total=sub + tax, tax_rate=rate,
    )
    if error == "total_mismatch":
        inv.printed_total = inv.total + Decimal(rng.choice(["10.00", "100.00", "0.90"]))
        inv.errors.append("TOTAL_MISMATCH")
    elif error == "line_math":
        l = rng.choice(inv.lines)
        l.amount = l.amount + Decimal("5.00")
        inv.subtotal = sum((x.amount for x in inv.lines), Decimal(0))
        inv.tax = (inv.subtotal * Decimal(str(rate))).quantize(TWO, ROUND_HALF_UP)
        inv.total = inv.subtotal + inv.tax
        inv.errors.append("LINE_ITEM_MATH")
    elif error == "missing_customer":
        inv.customer_name = None
    return inv


# ---------------------------------------------------------------- PDF layouts
def draw_layout(c: canvas.Canvas, inv: Inv, layout: int, rng: random.Random, fmt_date) -> None:
    W, H = A4
    total = inv.printed_total if inv.printed_total is not None else inv.total
    s = inv.symbol
    tax_label = {"GBP": "VAT", "EUR": "VAT", "USD": "Sales Tax", "AED": "VAT"}[inv.currency]
    pct = f"{int(inv.tax_rate * 100)}%"
    if layout == 0:  # classic: supplier top-left, meta right
        c.setFont("Helvetica-Bold", 16); c.drawString(50, H - 60, inv.supplier_name)
        c.setFont("Helvetica", 9); c.drawString(50, H - 76, f"{rng.randint(1, 200)} {rng.choice(['High Street', 'Market Road', 'Station Way'])}, {rng.choice(['Manchester', 'Leeds', 'London', 'Bristol'])}")
        c.drawString(50, H - 88, "VAT Reg No: GB" + str(rng.randint(100000000, 999999999)))
        c.setFont("Helvetica-Bold", 22); c.drawRightString(W - 50, H - 60, "INVOICE")
        c.setFont("Helvetica", 10)
        c.drawRightString(W - 50, H - 84, f"Invoice No: {inv.invoice_number}")
        c.drawRightString(W - 50, H - 98, f"Invoice Date: {fmt_date(inv.invoice_date)}")
        c.drawRightString(W - 50, H - 112, f"Due Date: {fmt_date(inv.invoice_date + timedelta(days=30))}")
        if inv.customer_name:
            c.setFont("Helvetica-Bold", 10); c.drawString(50, H - 140, "Bill To:")
            c.setFont("Helvetica", 10); c.drawString(50, H - 154, inv.customer_name)
        y = H - 200
        cols = [50, 330, 400, 490]
        c.setFont("Helvetica-Bold", 10)
        for x, t in zip(cols, ["Description", "Qty", "Unit Price", "Amount"]):
            c.drawString(x, y, t)
        c.setFont("Helvetica", 10)
        for l in inv.lines:
            y -= 18
            c.drawString(cols[0], y, l.description); c.drawString(cols[1], y, str(l.quantity))
            c.drawString(cols[2], y, money(l.unit_price)); c.drawString(cols[3], y, money(l.amount))
        y -= 34
        for lab, v in [("Subtotal", inv.subtotal), (f"{tax_label} ({pct})", inv.tax), ("Total Due", total)]:
            c.setFont("Helvetica-Bold" if lab == "Total Due" else "Helvetica", 10)
            c.drawString(360, y, f"{lab}:"); c.drawRightString(W - 50, y, f"{s}{money(v)}"); y -= 16
    elif layout == 1:  # 'Tax Invoice' header, customer first, currency code column
        c.setFont("Helvetica-Bold", 20); c.drawString(50, H - 60, "Tax Invoice")
        c.setFont("Helvetica", 10)
        c.drawString(50, H - 90, f"Invoice # {inv.invoice_number}")
        c.drawString(50, H - 104, f"Date of issue: {fmt_date(inv.invoice_date)}")
        c.drawString(300, H - 90, f"Supplier: {inv.supplier_name}")
        if inv.customer_name:
            c.drawString(300, H - 104, f"Customer: {inv.customer_name}")
        c.drawString(300, H - 118, f"Currency: {inv.currency}")
        y = H - 160
        c.setFont("Helvetica-Bold", 9)
        for x, t in [(50, "Item"), (300, "Quantity"), (380, "Rate"), (470, "Line Total")]:
            c.drawString(x, y, t)
        c.setFont("Helvetica", 9)
        for l in inv.lines:
            y -= 16
            c.drawString(50, y, l.description); c.drawString(300, y, str(l.quantity))
            c.drawString(380, y, money(l.unit_price)); c.drawString(470, y, money(l.amount))
        y -= 30
        c.drawString(330, y, "Net Amount"); c.drawRightString(W - 50, y, f"{inv.currency} {money(inv.subtotal)}")
        c.drawString(330, y - 15, f"{tax_label} @ {pct}"); c.drawRightString(W - 50, y - 15, f"{inv.currency} {money(inv.tax)}")
        c.setFont("Helvetica-Bold", 11)
        c.drawString(330, y - 34, "Amount Due"); c.drawRightString(W - 50, y - 34, f"{inv.currency} {money(total)}")
    else:  # compact receipt-like, labels on their own lines
        c.setFont("Helvetica-Bold", 14); c.drawCentredString(W / 2, H - 50, inv.supplier_name.upper())
        c.setFont("Helvetica", 9); c.drawCentredString(W / 2, H - 64, "Thank you for your business")
        c.setFont("Helvetica", 10)
        c.drawString(60, H - 100, "INVOICE NUMBER"); c.drawString(220, H - 100, inv.invoice_number)
        c.drawString(60, H - 115, "DATE"); c.drawString(220, H - 115, fmt_date(inv.invoice_date))
        if inv.customer_name:
            c.drawString(60, H - 130, "BILLED TO"); c.drawString(220, H - 130, inv.customer_name)
        y = H - 170
        c.setFont("Helvetica-Bold", 10)
        for x, t in [(60, "Description"), (320, "Qty"), (380, "Price"), (470, "Total")]:
            c.drawString(x, y, t)
        c.setFont("Helvetica", 10)
        for l in inv.lines:
            y -= 17
            c.drawString(60, y, l.description); c.drawString(320, y, str(l.quantity))
            c.drawString(380, y, f"{s}{money(l.unit_price)}"); c.drawString(470, y, f"{s}{money(l.amount)}")
        y -= 30
        c.drawString(320, y, "SUBTOTAL"); c.drawRightString(W - 60, y, f"{s}{money(inv.subtotal)}")
        c.drawString(320, y - 16, f"TAX {pct}"); c.drawRightString(W - 60, y - 16, f"{s}{money(inv.tax)}")
        c.setFont("Helvetica-Bold", 11)
        c.drawString(320, y - 36, "GRAND TOTAL"); c.drawRightString(W - 60, y - 36, f"{s}{money(total)}")
    c.setFont("Helvetica", 7)
    c.drawString(50, 40, "Payment terms: 30 days. Please quote the invoice number with your payment.")


def pdf_bytes(invs: list[Inv], rng: random.Random, layouts=None, terms_page_every: int = 0) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for i, inv in enumerate(invs):
        draw_layout(c, inv, (layouts[i] if layouts else rng.randint(0, 2)), rng, rng.choice(DATE_FORMATS))
        c.showPage()
        if terms_page_every and i % terms_page_every == 0:  # continuation page with no invoice number
            c.setFont("Helvetica", 9)
            for k, t in enumerate(["Terms and conditions", "1. Goods remain our property until paid in full.",
                                   "2. Late payments may incur statutory interest.", "3. Report discrepancies within 7 days."]):
                c.drawString(50, 780 - 16 * k, t)
            c.showPage()
    c.save()
    return buf.getvalue()


def render(pdf: bytes, dpi: int) -> list[Image.Image]:
    doc = pdfium.PdfDocument(pdf)
    imgs = [doc[i].render(scale=dpi / 72).to_pil().convert("RGB") for i in range(len(doc))]
    doc.close()
    return imgs


def degrade(img: Image.Image, rng: random.Random, level: str) -> Image.Image:
    angle = rng.uniform(-2.0, 2.0) if level != "clean" else 0
    img = img.rotate(angle, expand=True, fillcolor=(255, 255, 255), resample=Image.BICUBIC)
    arr = np.asarray(img).astype(np.float32)
    if level in ("scan", "photo"):
        sigma = 10 if level == "scan" else 16
        arr += np.random.default_rng(rng.randint(0, 9999)).normal(0, sigma, arr.shape)
    if level == "photo":  # uneven lighting
        h, w = arr.shape[:2]
        grad = np.linspace(0.75, 1.0, w)[None, :, None]
        arr = arr * grad
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    if level in ("scan", "photo"):
        img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 0.9)))
    return img


def images_to_pdf(imgs: list[Image.Image]) -> bytes:
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:], resolution=200)
    return buf.getvalue()


# ---------------------------------------------------------------- spreadsheets
def ledger_rows(invs: list[Inv]):
    return [[i.invoice_number, i.invoice_date.isoformat(), i.supplier_name, i.customer_name or "", float(i.subtotal),
             float(i.tax), float(i.printed_total if i.printed_total is not None else i.total), i.currency] for i in invs]


LEDGER_HEADER = ["Invoice No", "Invoice Date", "Supplier", "Customer", "Subtotal", "VAT", "Total", "Currency"]


def write_xlsx_ledger(path: Path, invs: list[Inv]):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "AP Ledger"
    ws.append(["Accounts payable ledger - exported from finance system"])
    ws.append([])
    ws.append(LEDGER_HEADER)
    for c in ws[3]:
        c.font = Font(bold=True)
    for r in ledger_rows(invs):
        ws.append(r)
    for row in ws.iter_rows(min_row=4, min_col=2, max_col=2):
        for c in row:
            c.value = date.fromisoformat(c.value)
            c.number_format = "dd/mm/yyyy"
    notes = wb.create_sheet("Notes")
    notes.append(["Prepared by AP team. Figures in original currency."])
    wb.save(path)


def write_xlsx_form(path: Path, inv: Inv):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Invoice"
    rows = [[inv.supplier_name], ["INVOICE"], [], ["Invoice Number", inv.invoice_number],
            ["Invoice Date", inv.invoice_date.strftime("%d/%m/%Y")], ["Bill To", inv.customer_name or ""], [],
            ["Description", "Qty", "Unit Price", "Amount"]]
    rows += [[l.description, l.quantity, float(l.unit_price), float(l.amount)] for l in inv.lines]
    total = inv.printed_total if inv.printed_total is not None else inv.total
    rows += [[], ["Subtotal", "", "", float(inv.subtotal)], [f"VAT {int(inv.tax_rate * 100)}%", "", "", float(inv.tax)],
             ["Total", "", "", float(total)], ["Currency", inv.currency]]
    for r in rows:
        ws.append(r)
    wb.save(path)


def write_xls_ledger(path: Path, invs: list[Inv]):
    import xlwt

    wb = xlwt.Workbook()
    ws = wb.add_sheet("Invoices")
    for j, h in enumerate(LEDGER_HEADER):
        ws.write(0, j, h)
    for i, r in enumerate(ledger_rows(invs), start=1):
        for j, v in enumerate(r):
            ws.write(i, j, v)
    wb.save(str(path))


def write_csv_lines(path: Path, invs: list[Inv], sep=",", decimal_comma=False):
    hdr = ["Invoice Number", "Date", "Vendor", "Customer", "Description", "Qty", "Unit Price", "Line Total", "Net Total", "Tax", "Grand Total", "Currency"]
    out = [sep.join(hdr)]

    def f(v: Decimal) -> str:
        s = f"{v:.2f}"
        return s.replace(".", ",") if decimal_comma else s

    for i in invs:
        tot = i.printed_total if i.printed_total is not None else i.total
        for l in i.lines:
            cells = [i.invoice_number, i.invoice_date.strftime("%d/%m/%Y"), i.supplier_name, i.customer_name or "", l.description,
                     str(l.quantity), f(l.unit_price), f(l.amount), f(i.subtotal), f(i.tax), f(tot), i.currency]
            out.append(sep.join(f'"{c}"' if sep in c else c for c in cells))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- main
def main(out: Path, seed: int, big_pages: int):
    rng = random.Random(seed)
    np.random.seed(seed)
    out.mkdir(parents=True, exist_ok=True)
    labels: list[dict] = []
    counter = iter(range(1, 100000))

    def add(name, data: bytes | None, category, invs, expect="success", expected_error=None, expect_issues=None, note=None, has_lines=True):
        if data is not None:
            (out / name).write_bytes(data)
        inv_labels = [i.label() for i in invs]
        if not has_lines:  # ledger exports carry header totals only
            for il in inv_labels:
                il["line_items"] = []
        labels.append({"file": name, "category": category, "expect": expect, "expected_error": expected_error,
                       "invoices": inv_labels,
                       # review is required for error-severity problems; warnings alone (missing customer, no line items) are not
                       "expect_review": any(e in REVIEW_CODES for e in (expect_issues or []) + [x for i in invs for x in i.errors]),
                       "expect_issues": sorted(set((expect_issues or []) + [e for i in invs for e in i.errors])),
                       "note": note})

    # 1) digital PDFs, all three layouts
    for k in range(18):
        inv = make_invoice(rng, next(counter))
        add(f"digital_{k + 1:02d}.pdf", pdf_bytes([inv], rng, [k % 3]), "digital_pdf", [inv])
    # 2) digital PDFs with injected errors
    for k, err in enumerate(["total_mismatch", "total_mismatch", "line_math", "line_math", "missing_customer"]):
        inv = make_invoice(rng, next(counter), err)
        add(f"error_{err}_{k + 1}.pdf", pdf_bytes([inv], rng, [k % 3]), "digital_pdf_error", [inv],
            expect_issues=["MISSING_FIELD"] if err == "missing_customer" else None)
    # 3) duplicate: same invoice as digital_01 sent again as a separate (re-scanned) file
    dup_src = labels[0]
    # 4) scanned PDFs (image-only)
    scanned_invs = []
    for k in range(10):
        inv = make_invoice(rng, next(counter))
        img = degrade(render(pdf_bytes([inv], rng, [k % 3]), rng.choice([150, 200]))[0], rng, "scan")
        add(f"scanned_{k + 1:02d}.pdf", images_to_pdf([img]), "scanned_pdf", [inv])
        scanned_invs.append(inv)
    # 5) phone photos / images
    for k in range(6):
        inv = make_invoice(rng, next(counter))
        img = degrade(render(pdf_bytes([inv], rng, [k % 3]), rng.choice([130, 160]))[0], rng, "photo")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=rng.choice([55, 70]))
        add(f"photo_{k + 1:02d}.jpg", buf.getvalue(), "image_jpg", [inv])
    for k in range(4):
        inv = make_invoice(rng, next(counter))
        img = degrade(render(pdf_bytes([inv], rng, [k % 3]), 170)[0], rng, "clean")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        add(f"image_{k + 1:02d}.png", buf.getvalue(), "image_png", [inv])
    # multi-page TIFF with two invoices
    t_invs = [make_invoice(rng, next(counter)) for _ in range(2)]
    frames = [degrade(render(pdf_bytes([i], rng, [j]), 170)[0], rng, "scan").convert("L") for j, i in enumerate(t_invs)]
    buf = io.BytesIO()
    frames[0].save(buf, format="TIFF", save_all=True, append_images=frames[1:], compression="tiff_lzw")
    add("multipage_scan.tiff", buf.getvalue(), "image_tiff", t_invs)
    # 6) batch PDF: 25 invoices + T&C continuation pages, one duplicate of digital_01
    batch = [make_invoice(rng, next(counter)) for _ in range(25)]
    add("batch_25_invoices.pdf", pdf_bytes(batch, rng, terms_page_every=4), "batch_pdf", batch)
    # 7) spreadsheets: ledger mirrors the batch PDF (for cross-verification) with 2 deliberate differences
    ledger = [Inv(**{**asdict(i), "lines": [Line(**l) for l in asdict(i)["lines"]]}) for i in batch[:12]]
    ledger[3].printed_total = ledger[3].total + Decimal("50.00")
    ledger[3].errors.append("TOTAL_MISMATCH")
    ledger[7].customer_name = None
    write_xlsx_ledger(out / "ap_ledger.xlsx", ledger)
    add("ap_ledger.xlsx", None, "xlsx_table", ledger, expect_issues=["NO_LINE_ITEMS"], has_lines=False,
        note="Mirrors batch_25_invoices.pdf invoices 1-12 with 2 deliberate differences (rows 4 and 8) for reconciliation.")
    form_inv = make_invoice(rng, next(counter), cur=CURRENCIES[0])
    write_xlsx_form(out / "single_invoice_form.xlsx", form_inv)
    add("single_invoice_form.xlsx", None, "xlsx_form", [form_inv])
    xls_invs = [make_invoice(rng, next(counter)) for _ in range(6)]
    write_xls_ledger(out / "legacy_ledger.xls", xls_invs)
    add("legacy_ledger.xls", None, "xls_table", xls_invs, expect_issues=["NO_LINE_ITEMS"], has_lines=False)
    csv_invs = [make_invoice(rng, next(counter)) for _ in range(5)]
    write_csv_lines(out / "line_items_export.csv", csv_invs)
    add("line_items_export.csv", None, "csv_lines", csv_invs)
    eu_invs = [make_invoice(rng, next(counter), cur=CURRENCIES[2]) for _ in range(3)]
    write_csv_lines(out / "eu_semicolon_export.csv", eu_invs, sep=";", decimal_comma=True)
    add("eu_semicolon_export.csv", None, "csv_lines", eu_invs, note="Semicolon-separated, decimal commas.")
    # 8) duplicate (exact re-upload of digital_01 under another name)
    (out / "digital_01_resent.pdf").write_bytes((out / dup_src["file"]).read_bytes())
    labels.append({**dup_src, "file": "digital_01_resent.pdf", "category": "duplicate", "expect_review": True,
                   "expect_issues": ["DUPLICATE_INVOICE"], "note": "Exact duplicate of digital_01.pdf"})
    # 9) corrupted / invalid
    good = (out / "digital_02.pdf").read_bytes()
    add("truncated.pdf", good[: len(good) // 3], "corrupted", [], expect="failure", expected_error="corrupted_pdf")
    add("garbage.pdf", b"%PDF-1.7\n" + bytes(rng.getrandbits(8) for _ in range(4000)), "corrupted", [], expect="failure", expected_error="corrupted_pdf")
    add("not_really.xlsx", b"this is just text pretending to be a spreadsheet", "corrupted", [], expect="failure", expected_error="corrupted_file")
    add("pdf_named_as.png", good, "corrupted", [], expect="failure", expected_error="type_mismatch")
    add("empty.csv", b"", "corrupted", [], expect="failure", expected_error="empty_file")
    jpg = (out / "photo_01.jpg").read_bytes()
    add("truncated_photo.jpg", jpg[: len(jpg) // 4], "corrupted", [], expect="failure", expected_error="corrupted_image")
    add("blank_page.pdf", pdf_bytes([], rng) if False else _blank_pdf(), "corrupted", [], expect="failure", expected_error="no_text")

    (out / "labels.json").write_text(json.dumps({"seed": seed, "files": labels}, indent=2))

    # performance fixtures (not part of accuracy labels)
    perf = out.parent / "perf"
    perf.mkdir(exist_ok=True)
    big = [make_invoice(rng, next(counter)) for _ in range(big_pages)]
    (perf / f"digital_{big_pages}_pages.pdf").write_bytes(pdf_bytes(big, rng))
    sc = [make_invoice(rng, next(counter)) for _ in range(40)]
    imgs = [degrade(im, rng, "scan") for im in render(pdf_bytes(sc, rng), 150)]
    (perf / "scanned_40_pages.pdf").write_bytes(images_to_pdf(imgs))
    (perf / "perf_labels.json").write_text(json.dumps({
        f"digital_{big_pages}_pages.pdf": [i.label() for i in big], "scanned_40_pages.pdf": [i.label() for i in sc]}))
    print(f"wrote {len(labels)} labelled files to {out} and perf fixtures to {perf}")


def _blank_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.showPage()
    c.save()
    return buf.getvalue()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="eval/dataset")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--big-pages", type=int, default=1000)
    a = ap.parse_args()
    main(Path(a.out), a.seed, a.big_pages)
