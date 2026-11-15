"""Cross-verification: PDF vs PDF, PDF vs Excel/CSV (any two documents).

Typical use: a supplier's PDF invoice batch vs the AP team's Excel ledger.
Invoices are matched on normalised invoice number (supplier as tie-break),
then every field is compared.
"""
from __future__ import annotations

from decimal import Decimal

from .extraction.normalize import norm_key
from .models import Document, Invoice

COMPARE = ["invoice_date", "supplier_name", "customer_name", "subtotal", "tax", "total", "currency"]


def _eq(field: str, a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        return abs(Decimal(str(a)) - Decimal(str(b))) <= Decimal("0.01")
    if field in ("supplier_name", "customer_name"):
        return norm_key(str(a)) == norm_key(str(b))
    return str(a).strip().upper() == str(b).strip().upper()


def _fmt(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        from .schemas import fmt_decimal
        return fmt_decimal(v)
    return str(v)


def _line_sum(inv: Invoice):
    a = [li.amount for li in inv.line_items if li.amount is not None]
    return sum(a, Decimal(0)) if a else None


def reconcile(doc_a: Document, doc_b: Document) -> dict:
    idx_b: dict[str, list[Invoice]] = {}
    for inv in doc_b.invoices:
        idx_b.setdefault(norm_key(inv.invoice_number), []).append(inv)
    matched, only_a = [], []
    used_b: set[str] = set()
    for a in doc_a.invoices:
        cands = [b for b in idx_b.get(norm_key(a.invoice_number), []) if b.id not in used_b] if a.invoice_number else []
        if len(cands) > 1 and a.supplier_name:
            cands = [b for b in cands if norm_key(b.supplier_name) == norm_key(a.supplier_name)] or cands
        if not cands:
            only_a.append(a)
            continue
        b = cands[0]
        used_b.add(b.id)
        diffs = []
        for f in COMPARE:
            va, vb = getattr(a, f), getattr(b, f)
            if not _eq(f, va, vb):
                diffs.append({"field": f, "a": _fmt(va), "b": _fmt(vb), "severity": "error" if f in ("total", "subtotal", "tax", "currency") else "warning"})
        sa, sb = _line_sum(a), _line_sum(b)
        if sa is not None and sb is not None and abs(sa - sb) > Decimal("0.01"):
            diffs.append({"field": "line_items_sum", "a": str(sa), "b": str(sb), "severity": "error"})
        if a.line_items and b.line_items and len(a.line_items) != len(b.line_items):
            diffs.append({"field": "line_item_count", "a": str(len(a.line_items)), "b": str(len(b.line_items)), "severity": "warning"})
        matched.append({"invoice_number": a.invoice_number, "a_invoice_id": a.id, "b_invoice_id": b.id,
                        "status": "match" if not diffs else "mismatch", "differences": diffs})
    only_b = [b for b in doc_b.invoices if b.id not in used_b]

    def brief(i: Invoice):
        return {"invoice_id": i.id, "invoice_number": i.invoice_number, "supplier_name": i.supplier_name,
                "total": _fmt(i.total), "currency": i.currency}

    n_mis = sum(1 for m in matched if m["status"] == "mismatch")
    return {
        "document_a": {"id": doc_a.id, "filename": doc_a.original_filename, "file_type": doc_a.file_type, "invoices": len(doc_a.invoices)},
        "document_b": {"id": doc_b.id, "filename": doc_b.original_filename, "file_type": doc_b.file_type, "invoices": len(doc_b.invoices)},
        "summary": {"matched": len(matched) - n_mis, "mismatched": n_mis, "only_in_a": len(only_a), "only_in_b": len(only_b)},
        "pairs": matched,
        "only_in_a": [brief(i) for i in only_a],
        "only_in_b": [brief(i) for i in only_b],
    }
