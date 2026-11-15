"""Task 3: validation rules."""
from datetime import date, timedelta
from decimal import Decimal as D

from app.db import SessionLocal
from app.models import Document, Invoice, LineItem
from app.validation.rules import needs_review, validate_invoice


def make(**kw):
    base = dict(invoice_number="INV-1", invoice_date=date(2026, 3, 1), supplier_name="Acme Ltd", customer_name="Bob Ltd",
                subtotal=D("100.00"), tax=D("20.00"), total=D("120.00"), currency="GBP", extraction_method="rules",
                field_confidence={})
    items = kw.pop("items", [("Widget", D("2"), D("25.00"), D("50.00")), ("Gadget", D("1"), D("50.00"), D("50.00"))])
    base.update(kw)
    inv = Invoice(**base)
    inv.line_items = [LineItem(position=i, description=d, quantity=q, unit_price=u, amount=a) for i, (d, q, u, a) in enumerate(items)]
    return inv


def codes(inv, db=None):
    return {i.code for i in validate_invoice(inv, db, today=date(2026, 6, 1))}


def test_clean_invoice_passes():
    issues = validate_invoice(make(), today=date(2026, 6, 1))
    assert issues == [] and not needs_review(issues)


def test_missing_required_fields_are_errors():
    issues = validate_invoice(make(invoice_number=None, total=None, customer_name=None), today=date(2026, 6, 1))
    sev = {(i.code, i.field): i.severity for i in issues}
    assert sev[("MISSING_FIELD", "invoice_number")] == "error"
    assert sev[("MISSING_FIELD", "total")] == "error"
    assert sev[("MISSING_FIELD", "customer_name")] == "warning"
    assert needs_review(issues)


def test_total_mismatch():
    assert "TOTAL_MISMATCH" in codes(make(total=D("120.90")))


def test_rounding_tolerance_not_flagged():
    assert "TOTAL_MISMATCH" not in codes(make(total=D("120.01")))


def test_line_item_math():
    inv = make(items=[("Widget", D("2"), D("25.00"), D("55.00")), ("Gadget", D("1"), D("45.00"), D("45.00"))])
    assert "LINE_ITEM_MATH" in codes(inv)


def test_line_items_sum_vs_subtotal():
    inv = make(items=[("Widget", D("1"), D("40.00"), D("40.00")), ("Gadget", D("1"), D("50.00"), D("50.00"))])
    assert "LINE_ITEMS_SUM_MISMATCH" in codes(inv)


def test_tax_inclusive_line_items_accepted():
    inv = make(items=[("All in", D("1"), D("120.00"), D("120.00"))])
    assert "LINE_ITEMS_SUM_MISMATCH" not in codes(inv)


def test_negative_and_subtotal_exceeds_total():
    c = codes(make(subtotal=D("200.00"), tax=D("-80.00"), total=D("120.00"), items=[]))
    assert {"NEGATIVE_AMOUNT", "SUBTOTAL_EXCEEDS_TOTAL"} <= c


def test_dates():
    assert "DATE_IN_FUTURE" in codes(make(invoice_date=date(2026, 6, 1) + timedelta(days=90)))
    assert "DATE_IMPLAUSIBLE" in codes(make(invoice_date=date(1995, 1, 1)))


def test_unusual_tax_and_currency():
    c = codes(make(tax=D("60.00"), total=D("160.00"), currency="XYZ"))
    assert {"TAX_RATE_UNUSUAL", "INVALID_CURRENCY"} <= c


def test_low_confidence_flags_review():
    issues = validate_invoice(make(field_confidence={"total": 0.3}), today=date(2026, 6, 1))
    assert any(i.code == "LOW_CONFIDENCE" for i in issues) and needs_review(issues)


def test_duplicate_invoice_detection():
    with SessionLocal() as db:
        doc = Document(original_filename="a.pdf", storage_path="/x", file_type="pdf", size_bytes=1, sha256="0")
        db.add(doc)
        db.flush()
        first = make(document_id=doc.id, supplier_name="ACME LIMITED")
        db.add(first)
        db.flush()
        second = make(document_id=doc.id, invoice_number="inv-1")  # same number, case differs, 'Ltd' vs 'LIMITED'
        db.add(second)
        db.flush()
        assert "DUPLICATE_INVOICE" in codes(second, db)
        other_supplier = make(document_id=doc.id, supplier_name="Different Co")
        db.add(other_supplier)
        db.flush()
        assert "DUPLICATE_INVOICE" not in codes(other_supplier, db)
        ledger_row = make(document_id=doc.id, extraction_method="spreadsheet")  # reconciliation, not duplicate
        db.add(ledger_row)
        db.flush()
        assert "DUPLICATE_INVOICE" not in codes(ledger_row, db)
