"""Business-rule validation. Each rule yields zero or more issues.

Severity:
  error   -> data is wrong or unusable; invoice MUST be reviewed
  warning -> suspicious; reviewer should look but may accept
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..extraction.normalize import ISO_CODES, norm_key
from ..models import Invoice

REQUIRED_ERROR = ["invoice_number", "invoice_date", "supplier_name", "total"]
REQUIRED_WARN = ["customer_name", "subtotal", "tax", "currency"]
LOW_CONFIDENCE = 0.6


@dataclass
class Issue:
    code: str
    severity: str
    field: str | None
    message: str
    details: dict | None = None


def _tol(expected: Decimal, n_rounded: int = 2) -> Decimal:
    """Rounding drift allowance: each value rounded to 2 dp can be off by half a cent."""
    return Decimal("0.005") * max(2, n_rounded)


def _money(v: Decimal | None) -> str:
    return "—" if v is None else f"{v:,.2f}"


def validate_invoice(inv: Invoice, db: Session | None = None, today: date | None = None) -> list[Issue]:
    today = today or date.today()
    issues: list[Issue] = []

    for f in REQUIRED_ERROR:
        if getattr(inv, f) in (None, ""):
            issues.append(Issue("MISSING_FIELD", "error", f, f"{f.replace('_', ' ').capitalize()} is missing."))
    for f in REQUIRED_WARN:
        if getattr(inv, f) in (None, ""):
            issues.append(Issue("MISSING_FIELD", "warning", f, f"{f.replace('_', ' ').capitalize()} is missing."))

    # numeric sanity
    for f in ("subtotal", "tax", "total"):
        v = getattr(inv, f)
        if v is not None and v < 0:
            issues.append(Issue("NEGATIVE_AMOUNT", "warning", f, f"{f.capitalize()} is negative ({_money(v)}). Credit note?"))
    if inv.total is not None and inv.total == 0:
        issues.append(Issue("ZERO_TOTAL", "warning", "total", "Total is zero."))
    if inv.subtotal is not None and inv.total is not None and inv.subtotal > 0 and inv.total > 0 and inv.subtotal > inv.total + _tol(inv.total):
        issues.append(Issue("SUBTOTAL_EXCEEDS_TOTAL", "error", "subtotal", f"Subtotal {_money(inv.subtotal)} is larger than total {_money(inv.total)}."))
    if inv.tax is not None and inv.subtotal not in (None, Decimal(0)) and inv.subtotal > 0:
        rate = inv.tax / inv.subtotal
        if rate > Decimal("0.35"):
            issues.append(Issue("TAX_RATE_UNUSUAL", "warning", "tax", f"Tax is {rate:.0%} of subtotal, which is unusually high.", {"rate": float(rate)}))

    if inv.currency and inv.currency.upper() not in ISO_CODES:
        issues.append(Issue("INVALID_CURRENCY", "warning", "currency", f"'{inv.currency}' is not a recognised ISO 4217 code."))

    if inv.invoice_date:
        if inv.invoice_date > today + timedelta(days=30):
            issues.append(Issue("DATE_IN_FUTURE", "error", "invoice_date", f"Invoice date {inv.invoice_date} is in the future."))
        elif inv.invoice_date < date(2000, 1, 1):
            issues.append(Issue("DATE_IMPLAUSIBLE", "error", "invoice_date", f"Invoice date {inv.invoice_date} is implausibly old."))

    # arithmetic consistency
    if inv.subtotal is not None and inv.tax is not None and inv.total is not None:
        expected = inv.subtotal + inv.tax
        if abs(expected - inv.total) > _tol(inv.total):
            issues.append(Issue("TOTAL_MISMATCH", "error", "total",
                                f"Subtotal {_money(inv.subtotal)} + tax {_money(inv.tax)} = {_money(expected)}, but total is {_money(inv.total)}.",
                                {"expected": str(expected), "actual": str(inv.total), "difference": str(inv.total - expected)}))
    elif inv.subtotal is not None and inv.tax is None and inv.total is not None and abs(inv.subtotal - inv.total) > _tol(inv.total):
        issues.append(Issue("TOTAL_UNEXPLAINED", "warning", "total",
                            f"Total {_money(inv.total)} differs from subtotal {_money(inv.subtotal)} but no tax amount was found."))

    items = list(inv.line_items)
    for li in items:
        if li.quantity is not None and li.unit_price is not None and li.amount is not None:
            exp = li.quantity * li.unit_price
            # unit prices are shown rounded, so the error scales with quantity
            if abs(exp - li.amount) > max(Decimal("0.01"), abs(li.quantity) * Decimal("0.005")):
                issues.append(Issue("LINE_ITEM_MATH", "error", f"line_items[{li.position}]",
                                    f"Line {li.position + 1} '{(li.description or '')[:40]}': {li.quantity:g} × {_money(li.unit_price)} = {_money(exp)}, not {_money(li.amount)}.",
                                    {"position": li.position, "expected": str(exp), "actual": str(li.amount)}))
    amounts = [li.amount for li in items if li.amount is not None]
    if amounts and len(amounts) == len(items):
        s = sum(amounts, Decimal(0))
        target_field, target = ("subtotal", inv.subtotal) if inv.subtotal is not None else ("total", inv.total)
        if target is not None:
            # line items might be tax-inclusive: accept a match against total too
            tol = _tol(target, len(amounts) + 1)
            matches = abs(s - target) <= tol or (inv.total is not None and abs(s - inv.total) <= tol)
            if not matches:
                issues.append(Issue("LINE_ITEMS_SUM_MISMATCH", "error", target_field,
                                    f"Line items add up to {_money(s)}, but {target_field} is {_money(target)}.",
                                    {"line_items_sum": str(s), target_field: str(target)}))
    elif not items:
        issues.append(Issue("NO_LINE_ITEMS", "warning", "line_items", "No line items were found."))

    # duplicates across the whole database
    if db is not None and inv.invoice_number:
        candidates = db.scalars(select(Invoice).where(Invoice.id != inv.id, Invoice.invoice_number.ilike(inv.invoice_number))).all()
        key_no, key_sup = norm_key(inv.invoice_number), norm_key(inv.supplier_name)
        # A ledger row (spreadsheet) that mirrors a PDF invoice is a reconciliation pair,
        # not a duplicate: compare source documents with source documents, ledgers with ledgers.
        is_sheet = inv.extraction_method == "spreadsheet"
        dups = [c for c in candidates
                if norm_key(c.invoice_number) == key_no
                and (not key_sup or not c.supplier_name or norm_key(c.supplier_name) == key_sup)
                and (c.extraction_method == "spreadsheet") == is_sheet]
        if dups:
            same_total = [c for c in dups if c.total == inv.total]
            issues.append(Issue("DUPLICATE_INVOICE", "error", "invoice_number",
                                f"Invoice {inv.invoice_number} from {inv.supplier_name or 'this supplier'} already exists"
                                + (" with the same total." if same_total else " with a different total."),
                                {"duplicate_ids": [c.id for c in dups], "document_ids": list({c.document_id for c in dups})}))

    conf = inv.field_confidence or {}
    low = [f for f, c in conf.items() if getattr(inv, f, None) is not None and c < LOW_CONFIDENCE]
    if low:
        issues.append(Issue("LOW_CONFIDENCE", "warning", None, f"Low extraction confidence for: {', '.join(low)}.", {"fields": low}))
    return issues


def needs_review(issues: list[Issue]) -> bool:
    return any(i.severity == "error" or i.code in {"LOW_CONFIDENCE", "UNGROUNDED_VALUE"} for i in issues)
