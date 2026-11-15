"""Map tabular spreadsheet exports to invoices by header names (no LLM needed).

Handles the two common shapes:
  * one row per invoice        (Invoice No | Date | Supplier | ... | Total)
  * one row per line item      (Invoice No | ... | Description | Qty | Price | Amount)
"""
from __future__ import annotations

import re
from collections import OrderedDict

from ..pipeline import SheetTable
from .normalize import clean_text, normalize_currency, parse_amount, parse_date
from .schema import InvoiceData, LineItemData

SYNONYMS = {
    "invoice_number": ["invoice no", "invoice number", "invoice #", "invoice id", "inv no", "inv number", "invoice", "invoice ref", "bill no"],
    "invoice_date": ["invoice date", "date", "inv date", "issue date", "bill date"],
    "supplier_name": ["supplier", "supplier name", "vendor", "vendor name", "seller", "from", "issuer"],
    "customer_name": ["customer", "customer name", "client", "bill to", "buyer", "sold to"],
    "subtotal": ["subtotal", "sub total", "net", "net amount", "amount before tax", "net total"],
    "tax": ["tax", "vat", "gst", "tax amount", "vat amount", "sales tax"],
    "total": ["total", "grand total", "total amount", "gross", "gross amount", "amount due", "invoice total", "total due"],
    "currency": ["currency", "ccy", "curr"],
    "li_description": ["description", "item", "product", "service", "item description", "details"],
    "li_quantity": ["qty", "quantity", "units"],
    "li_unit_price": ["unit price", "price", "rate", "unit cost", "unit"],
    "li_amount": ["amount", "line total", "line amount", "ext price", "extended price", "line value"],
}


def _norm(h: str) -> str:
    return re.sub(r"[^a-z# ]", " ", h.lower()).strip()


def map_headers(header: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    normed = [re.sub(r"\s+", " ", _norm(h)) for h in header]
    for field, names in SYNONYMS.items():
        for name in names:  # exact match first, in synonym priority order
            if name in normed and normed.index(name) not in found.values():
                found[field] = normed.index(name)
                break
    return found


def is_invoice_table(table: SheetTable) -> bool:
    m = map_headers(table.header)
    header_fields = {k for k in m if not k.startswith("li_")}
    return "invoice_number" in m and len(header_fields) >= 3


def extract_table(table: SheetTable) -> list[tuple[str, InvoiceData]]:
    m = map_headers(table.header)
    groups: "OrderedDict[str, list[tuple[int, list[str]]]]" = OrderedDict()
    for r_i, row in enumerate(table.rows):
        key = row[m["invoice_number"]].strip() if m["invoice_number"] < len(row) else ""
        if not key:
            continue
        groups.setdefault(key, []).append((r_i, row))

    def get(row, f):
        j = m.get(f)
        return row[j] if j is not None and j < len(row) and row[j] != "" else None

    has_items = "li_description" in m
    out = []
    for key, rows in groups.items():
        d = InvoiceData(method="spreadsheet")
        first = lambda f: next((get(r, f) for _, r in rows if get(r, f) is not None), None)  # noqa: E731
        d.invoice_number = key
        d.invoice_date, amb = parse_date(first("invoice_date"))
        d.supplier_name = clean_text(first("supplier_name"))
        d.customer_name = clean_text(first("customer_name"))
        d.currency = normalize_currency(first("currency"))
        d.subtotal = parse_amount(first("subtotal"))
        d.tax = parse_amount(first("tax"))
        totals = [parse_amount(get(r, "total")) for _, r in rows if get(r, "total") is not None]
        if has_items and len(set(totals)) > 1 and "li_amount" not in m:
            d.total = None  # 'Total' column is per line here, not per invoice
        else:
            d.total = totals[0] if totals else None
        if has_items:
            for _, r in rows:
                amt = parse_amount(get(r, "li_amount"))
                if amt is None and "li_amount" not in m and len(set(totals)) > 1:
                    amt = parse_amount(get(r, "total"))
                d.line_items.append(LineItemData(
                    description=clean_text(get(r, "li_description")),
                    quantity=parse_amount(get(r, "li_quantity")),
                    unit_price=parse_amount(get(r, "li_unit_price")),
                    amount=amt,
                ))
        d.confidence = {f: (0.95 if getattr(d, f) is not None else 0.0) for f in
                        ("invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax", "total", "currency")}
        if amb:
            d.confidence["invoice_date"] = 0.6
        lo = table.header_row_index + 2 + rows[0][0]
        hi = table.header_row_index + 2 + rows[-1][0]
        out.append((f"{table.sheet_name} rows {lo}-{hi}", d))
    return out
