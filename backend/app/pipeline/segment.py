"""Split a multi-page document into invoices.

A 1000-page PDF from accounts payable is usually a batch of many invoices.
A new segment starts on a page that shows an invoice number different from
the current segment's; pages with no number (continuations, T&Cs) stay with
the invoice before them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..extraction.normalize import fix_ocr_id
from . import PageResult

# Label + value, e.g. "Invoice No: INV-123", "Invoice # 2026/88", "INVOICE NUMBER A12345".
# The label must be followed by no/number/#/id so the "INV" inside "INV-123" is never taken as a label.
_LABEL = re.compile(
    r"\b(?:invoice|inv|bill|receipt)\s*(?:no\.?|number|num\.?|#|id)\s*[:#.\-]?\s*([A-Z0-9$!|][A-Z0-9$!|\-/_.]*[A-Z0-9])?",
    re.I,
)
_HEADER = re.compile(r"^\s*(?:tax\s+)?(?:invoice|receipt|bill)\b|\binvoice\s*(?:no|number|#|date)\b", re.I | re.M)
_TOTAL = re.compile(r"\b(?:grand\s*total|total\s*due|amount\s*due|balance\s*due|total)\b[^\n]*\d", re.I)


def looks_like_invoice_start(text: str) -> bool:
    head = "\n".join(text.splitlines()[:15])
    return bool(_HEADER.search(head)) and bool(_TOTAL.search(text))


def page_invoice_number(text: str) -> str | None:
    lines = [l.strip() for l in text.splitlines()[:60]]
    for i, line in enumerate(lines):
        for m in _LABEL.finditer(line):
            val = m.group(1)
            if not val and i + 1 < len(lines) and lines[i + 1]:
                val = lines[i + 1].split()[0]
            if val:
                val = fix_ocr_id(val)
            if val and any(c.isdigit() for c in val) and re.fullmatch(r"[A-Z0-9][A-Z0-9\-/_.]*", val, re.I):
                return val.strip(".-/").upper()
    return None


def _key(no: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (no or "").upper())


@dataclass
class Segment:
    index: int
    pages: list[PageResult]

    @property
    def page_start(self) -> int:
        return self.pages[0].page_number

    @property
    def page_end(self) -> int:
        return self.pages[-1].page_number

    @property
    def text(self) -> str:
        if len(self.pages) == 1:
            return self.pages[0].text
        return "\n\n".join(f"--- page {p.page_number} ---\n{p.text}" for p in self.pages)


def segment_pages(pages: list[PageResult]) -> list[Segment]:
    pages = [p for p in pages if p.text.strip()]
    if not pages:
        return []
    segments: list[Segment] = []
    current: list[PageResult] = []
    current_no: str | None = None
    for p in pages:
        no = page_invoice_number(p.text)
        new_number = bool(current and no and current_no and _key(no) != _key(current_no))
        # backup rule when OCR garbles the number: a fresh invoice header + totals, and the
        # current invoice already has its totals (so this isn't page 2 of the same invoice)
        unnumbered_start = bool(current and not no and looks_like_invoice_start(p.text)
                                and any(_TOTAL.search(c.text) for c in current))
        if new_number or unnumbered_start:
            segments.append(Segment(len(segments), current))
            current, current_no = [], None
        current.append(p)
        current_no = current_no or no
    if current:
        segments.append(Segment(len(segments), current))
    return segments
