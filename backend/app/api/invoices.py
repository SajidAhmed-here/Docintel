from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..extraction.normalize import normalize_currency, parse_amount, parse_date
from ..models import Correction, Document, Invoice, LineItem
from ..processor import run_validation
from ..schemas import CorrectionIn, InvoiceDetail
from ..search.service import apply_filters, index_document
from .serializers import invoice_detail, invoice_summary

router = APIRouter(prefix="/invoices", tags=["invoices"])
EDITABLE = {"invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax", "total", "currency"}


def _filters(status, needs_review, supplier, currency, date_from, date_to, min_total, max_total, file_type):
    return dict(status=status, needs_review=needs_review, supplier=supplier, currency=currency, date_from=date_from,
                date_to=date_to, min_total=min_total, max_total=max_total, file_type=file_type)


@router.get("", summary="List / filter invoices (review queue: needs_review=true)")
def list_invoices(
    status: str | None = None, needs_review: bool | None = None, supplier: str | None = None, currency: str | None = None,
    date_from: date | None = None, date_to: date | None = None, min_total: float | None = None, max_total: float | None = None,
    file_type: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = apply_filters(select(Invoice).join(Document), _filters(status, needs_review, supplier, currency, date_from, date_to, min_total, max_total, file_type))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Invoice.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [{**invoice_summary(i).model_dump(mode="json"), "document_filename": i.document.original_filename} for i in rows],
            "total": total, "page": page, "page_size": page_size}


@router.get("/export.csv", summary="Export invoices as CSV (for import into internal systems)")
def export_csv(needs_review: bool | None = None, db: Session = Depends(get_db)):
    stmt = apply_filters(select(Invoice).join(Document), {"needs_review": needs_review})
    rows = db.scalars(stmt.order_by(Invoice.invoice_date)).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["invoice_id", "document", "invoice_number", "invoice_date", "supplier_name", "customer_name",
                "subtotal", "tax", "total", "currency", "status", "needs_review"])
    for i in rows:
        w.writerow([i.id, i.document.original_filename, i.invoice_number, i.invoice_date, i.supplier_name, i.customer_name,
                    i.subtotal, i.tax, i.total, i.currency, i.status, i.needs_review])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="invoices.csv"'})


@router.get("/{invoice_id}", response_model=InvoiceDetail)
def get_invoice(invoice_id: str, db: Session = Depends(get_db)):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found.")
    return invoice_detail(inv)


def _coerce(field: str, value):
    if value in (None, ""):
        return None
    if field in ("subtotal", "tax", "total"):
        v = parse_amount(value)
        if v is None:
            raise HTTPException(422, f"'{value}' is not a valid amount for {field}.")
        return v
    if field == "invoice_date":
        d, _ = parse_date(value)
        if d is None:
            raise HTTPException(422, f"'{value}' is not a valid date. Use YYYY-MM-DD.")
        return d
    if field == "currency":
        c = normalize_currency(value)
        if c is None:
            raise HTTPException(422, f"'{value}' is not a recognised currency code.")
        return c
    return str(value).strip()[:512]


def _s(v):
    return None if v is None else (f"{v:f}" if isinstance(v, Decimal) else str(v))


@router.patch("/{invoice_id}", response_model=InvoiceDetail, summary="Apply manual corrections (audited) and re-validate")
def correct_invoice(invoice_id: str, body: CorrectionIn, db: Session = Depends(get_db)):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found.")
    unknown = set(body.fields) - EDITABLE
    if unknown:
        raise HTTPException(422, f"Not editable: {', '.join(sorted(unknown))}")
    who = body.corrected_by or "reviewer"
    changed = False
    for field, raw in body.fields.items():
        new = _coerce(field, raw)
        old = getattr(inv, field)
        if _s(old) != _s(new):
            db.add(Correction(invoice_id=inv.id, field=field, old_value=_s(old), new_value=_s(new), corrected_by=who))
            setattr(inv, field, new)
            conf = dict(inv.field_confidence or {})
            conf[field] = 1.0  # human-verified
            inv.field_confidence = conf
            changed = True
    if body.line_items is not None:
        before = "; ".join(f"{li.description}|{_s(li.quantity)}|{_s(li.unit_price)}|{_s(li.amount)}" for li in inv.line_items)
        inv.line_items.clear()
        db.flush()
        for pos, li in enumerate(body.line_items):
            inv.line_items.append(LineItem(position=pos, description=li.description, quantity=li.quantity, unit_price=li.unit_price, amount=li.amount))
        after = "; ".join(f"{li.description}|{_s(li.quantity)}|{_s(li.unit_price)}|{_s(li.amount)}" for li in body.line_items)
        if before != after:
            db.add(Correction(invoice_id=inv.id, field="line_items", old_value=before[:4000], new_value=after[:4000], corrected_by=who))
            changed = True
    if changed:
        inv.status = "corrected"
    db.flush()
    run_validation(db, inv)
    if body.approve:
        inv.needs_review = False
        inv.status = "approved"
        db.add(Correction(invoice_id=inv.id, field="status", old_value="needs_review", new_value="approved", corrected_by=who))
    doc = inv.document
    db.flush()
    doc.status = "needs_review" if any(i.needs_review for i in doc.invoices) else "completed"
    index_document(db, doc)
    db.commit()
    db.refresh(inv)
    return invoice_detail(inv)
