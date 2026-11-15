"""Anti-hallucination check: every LLM value must be traceable to the source text.

LLMs sometimes "helpfully" compute a missing subtotal or guess a currency.
The brief says never invent values, so anything we cannot find in the
document is removed (set to null) and recorded in `notes` for the reviewer.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from .normalize import CURRENCY_SYMBOLS, find_amounts, fix_company_suffix, fix_ocr_id, parse_date
from .schema import InvoiceData

_DATE_SCAN = re.compile(
    r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b|\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b|"
    r"\b\d{1,2}(?:st|nd|rd|th)?[\s\-]+[A-Za-z]{3,9}\.?,?[\s\-]+\d{4}\b|\b[A-Za-z]{3,9}\.?[\s\-]+\d{1,2}(?:st|nd|rd|th)?,?[\s\-]+\d{4}\b"
)


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


class SourceIndex:
    def __init__(self, text: str):
        # apply the same OCR corrections the extractor applies, so a correct LLM value
        # ('Acme plc') still matches OCR text that reads 'Acme pic'
        text = text + "\n" + "\n".join(fix_company_suffix(l) or "" for l in text.splitlines()) \
            + "\n" + " ".join(fix_ocr_id(t) or "" for t in text.split() if "-" in t)
        self.text = text
        self.flat = _alnum(text)
        self.tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
        self.amounts: set[Decimal] = set()
        for line in text.splitlines():
            for a in find_amounts(line):
                self.amounts.add(abs(a).quantize(Decimal("0.01")))
        self.dates: set[date] = set()
        for m in _DATE_SCAN.finditer(text):
            d, amb = parse_date(m.group(0))
            if d:
                self.dates.add(d)
                if amb and d.day <= 12:  # accept either day/month reading for ambiguous dates
                    self.dates.add(date(d.year, d.day, d.month))

    def has_string(self, value: str) -> bool:
        v = _alnum(value)
        if not v:
            return False
        if v in self.flat:
            return True
        toks = re.findall(r"[a-z0-9]+", value.lower())
        return bool(toks) and sum(t in self.tokens for t in toks) / len(toks) >= 0.75  # tolerate OCR noise

    def has_amount(self, value: Decimal) -> bool:
        return abs(value).quantize(Decimal("0.01")) in self.amounts

    def has_date(self, value: date) -> bool:
        return value in self.dates

    def has_currency(self, code: str) -> bool:
        if code.upper() in self.text.upper():
            return True
        return any(sym in self.text for sym, c in CURRENCY_SYMBOLS.items() if c == code.upper())


def ground(data: InvoiceData, text: str) -> InvoiceData:
    idx = SourceIndex(text)

    def drop(field: str, value):
        setattr(data, field, None)
        data.confidence[field] = 0.0
        data.notes.append(f"Removed '{field}' = {value!r}: not found in the document text.")

    for f in ("invoice_number", "supplier_name", "customer_name"):
        v = getattr(data, f)
        if v and not idx.has_string(v):
            drop(f, v)
    for f in ("subtotal", "tax", "total"):
        v = getattr(data, f)
        if v is not None and not idx.has_amount(v):
            drop(f, str(v))
    if data.invoice_date and not idx.has_date(data.invoice_date):
        drop("invoice_date", data.invoice_date.isoformat())
    if data.currency and not idx.has_currency(data.currency):
        drop("currency", data.currency)

    kept = []
    for li in data.line_items:
        for attr in ("quantity", "unit_price", "amount"):
            v = getattr(li, attr)
            if v is not None and not (idx.has_amount(v) or (attr == "quantity" and v == v.to_integral_value())):
                setattr(li, attr, None)
        if li.description and not idx.has_string(li.description):
            data.notes.append(f"Removed line item '{li.description}': description not found in text.")
            continue
        if li.description or li.amount is not None:
            kept.append(li)
    data.line_items = kept
    return data
