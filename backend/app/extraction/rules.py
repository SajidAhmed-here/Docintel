"""Deterministic, label-driven invoice extractor.

Used (a) when no LLM key is configured, (b) as a fallback when the LLM is
rate-limited or down, and (c) to fill gaps the LLM left empty. It only ever
returns values it can point to in the text.
"""
from __future__ import annotations

import re
from decimal import Decimal

from .normalize import clean_text, detect_currency, find_amounts, fix_company_suffix, fix_ocr_id, parse_amount, parse_date
from .schema import InvoiceData, LineItemData

LABEL_SEP = r"\s*[:#.\-]?\s*"
INV_LABEL = re.compile(r"\b(?:invoice|inv|bill|receipt)\s*(?:no\.?|number|num\.?|#|id)" + LABEL_SEP + r"([A-Z0-9$!|][A-Z0-9$!|\-/_.]*[A-Z0-9])?", re.I)
DATE_LABEL = re.compile(r"\b(?:invoice\s*date|date\s*of\s*issue|issue\s*date|issued(?:\s*on)?|tax\s*point|bill\s*date|dated|date)\b" + LABEL_SEP + r"(.*)", re.I)
DUE_LABEL = re.compile(r"\b(?:due|payment\s*date|delivery|order\s*date|ship)\b", re.I)
SUPPLIER_WORDS = r"from|supplier|vendor|seller|issued\s*by|sold\s*by|company|payee"
CUSTOMER_WORDS = r"bill(?:ed)?\s*to|invoice\s*to|sold\s*to|customer(?:\s*name)?|client|buyer"
# Any label that can follow a value on the same line (OCR often merges two columns into one line).
ANY_LABEL_MID = re.compile(
    r"\s(?:" + SUPPLIER_WORDS + "|" + CUSTOMER_WORDS +
    r"|invoice\s*(?:no|number|date|#)|inv\s*no|date(?:\s*of\s*issue)?|due\s*date|currency|tel|phone|e-?mail|vat\s*reg(?:\s*no)?|po\s*(?:no|number))\s*[:#]",
    re.I,
)


def _label_res(words: str, start_only: str = ""):
    start = re.compile(r"^\s*(?:" + words + (("|" + start_only) if start_only else "") + r")\b" + LABEL_SEP + r"(.*)$", re.I)
    mid = re.compile(r"(?:^|\s)(?:" + words + r")\s*:\s*(.*)$", re.I)
    return start, mid


SUPPLIER_LABEL = _label_res(SUPPLIER_WORDS)
CUSTOMER_LABEL = _label_res(CUSTOMER_WORDS, start_only=r"to")

SUBTOTAL_RE = re.compile(r"\b(?:sub\s*-?\s*total|net\s*(?:amount|total)|total\s*(?:before|excl(?:uding)?\.?)\s*(?:tax|vat)|amount\s*before\s*tax|total\s*net)\b", re.I)
TAX_RE = re.compile(r"\b(?:total\s*)?(?:vat|tax|gst|hst|sales\s*tax)\b", re.I)
TAX_EXCLUDE = re.compile(r"\b(?:tax\s*invoice|vat\s*(?:reg|no|number|id)|tax\s*(?:id|no|number|rate\s*:?\s*$)|gstin|incl|before|excl|sub)", re.I)
TOTAL_RE = re.compile(r"\b(?:grand\s*total|total\s*due|amount\s*due|balance\s*due|total\s*payable|invoice\s*total|total\s*amount|amount\s*payable|total)\b", re.I)
ITEM_HEADER = re.compile(r"\b(?:description|item|product|service|details|particulars)\b", re.I)
ITEM_HEADER_NUM = re.compile(r"\b(?:qty|quantity|amount|price|rate|total|unit)\b", re.I)
_NOT_NAME = re.compile(r"^(?:-{2,}|(?:tax\s+)?(?:invoice|receipt|bill|statement|page\s*\d|date|tel|phone|email|www\.|http|vat|reg|thank))", re.I)


def _lines(text: str) -> list[str]:
    return [re.sub(r"[ \t]+", " ", l).strip() for l in text.splitlines()]


def _next_value(lines: list[str], i: int) -> str | None:
    for j in range(i + 1, min(i + 4, len(lines))):
        if lines[j]:
            return lines[j]
    return None


def _amount_on(lines: list[str], i: int) -> Decimal | None:
    amts = find_amounts(lines[i])
    # "VAT 20%: 120.00" -> ignore the rate
    line_wo_pct = re.sub(r"\d+(?:\.\d+)?\s*%", "", lines[i])
    amts = find_amounts(line_wo_pct) or amts
    if amts:
        return amts[-1]
    nxt = _next_value(lines, i)
    if nxt and re.fullmatch(r"[£€$₹৳¥A-Z\s]*-?[\d,.' ]+[A-Z\s]*", nxt):
        return parse_amount(nxt)
    return None


def _invoice_number(lines):
    for i, l in enumerate(lines):
        m = INV_LABEL.search(l)
        if not m:
            continue
        val = m.group(1)
        if val:
            val = fix_ocr_id(val)
        if not val:
            nxt = _next_value(lines, i)
            cand = nxt.split()[0] if nxt else None
            val = cand if cand and re.fullmatch(r"[A-Z0-9][A-Z0-9\-/_.]*", cand, re.I) and any(c.isdigit() for c in cand) else None
        if val and any(c.isdigit() for c in val):
            return val.strip(".-/"), 0.9
    return None, 0.0


def _invoice_date(lines):
    for i, l in enumerate(lines):
        m = DATE_LABEL.search(l)
        if not m or DUE_LABEL.search(l[: m.start() + 12]):
            continue
        d, amb = parse_date(m.group(1))
        if d is None:
            d, amb = parse_date(_next_value(lines, i) or "")
        if d:
            return d, (0.6 if amb else 0.9)
    for l in lines:  # positional fallback: first date not on a 'due' line
        if DUE_LABEL.search(l):
            continue
        d, amb = parse_date(l)
        if d:
            return d, 0.5 if not amb else 0.4
    return None, 0.0


def _cut(val: str | None) -> str | None:
    """'Acme Ltd   Invoice No: 123' -> 'Acme Ltd'"""
    if not val:
        return None
    val = ANY_LABEL_MID.split(" " + val)[0]
    val = re.split(r"\s{3,}|\t", val.strip())[0]
    return clean_text(val)


def _party(lines, label_res):
    start, mid = label_res
    for i, l in enumerate(lines):
        m = start.match(l) or mid.search(l)
        if not m:
            continue
        val = _cut(m.group(1))
        if not val:
            val = _cut(_next_value(lines, i))
        if val and not _NOT_NAME.match(val) and any(c.isalpha() for c in val) and not ITEM_HEADER.search(val):
            return fix_company_suffix(val), 0.85
    return None, 0.0


def _supplier_positional(lines):
    """Letterhead heuristic: the issuer's name is usually the first text line."""
    seen = 0
    for l in lines[:8]:
        if not l:
            continue
        seen += 1
        if _NOT_NAME.match(l) or CUSTOMER_LABEL[0].match(l) or (ITEM_HEADER.search(l) and ITEM_HEADER_NUM.search(l)) or len(l) > 80:
            continue
        letters = sum(c.isalpha() for c in l)
        digits = sum(c.isdigit() for c in l)
        if letters >= 3 and digits <= 2 and ":" not in l:
            # OCR often joins the letterhead with a right-aligned title: "Acme Ltd   INVOICE"
            l = re.sub(r"\s+(?:tax\s+)?(?:invoice|receipt|bill|statement)\s*$", "", l, flags=re.I).strip()
            if l:
                return fix_company_suffix(l), (0.65 if seen <= 2 else 0.5)
    return None, 0.0


def _totals(lines):
    subtotal = tax = total = None
    tax_values: list[Decimal] = []
    for i, l in enumerate(lines):
        if SUBTOTAL_RE.search(l):
            v = _amount_on(lines, i)
            if v is not None:
                subtotal = v
            continue
        if TAX_RE.search(l) and not TAX_EXCLUDE.search(l) and not re.search(r"\btotal\b(?!\s*(?:vat|tax|gst))", l, re.I):
            v = _amount_on(lines, i)
            if v is not None:
                if re.search(r"\btotal\s*(?:vat|tax|gst)\b", l, re.I):
                    tax_values = [v]
                    tax = v
                else:
                    tax_values.append(v)
            continue
        if TOTAL_RE.search(l) and not re.search(r"\b(?:qty|quantity|items?)\b", l, re.I):
            v = _amount_on(lines, i)
            if v is not None:
                total = v  # last 'total' line wins (grand total sits at the bottom)
    if tax is None and tax_values:
        tax = sum(tax_values, Decimal(0))
    return subtotal, tax, total


def _split_cells(line: str) -> list[str]:
    return [c.strip() for c in re.split(r"\t|\s{2,}|\s\|\s", line) if c.strip()]


def _line_items(raw_lines: list[str]):
    items: list[LineItemData] = []
    start = None
    for i, l in enumerate(raw_lines):
        if ITEM_HEADER.search(l) and ITEM_HEADER_NUM.search(l):
            start = i + 1
            break
    if start is None:
        return items
    for l in raw_lines[start:]:
        s = l.strip()
        if not s:
            continue
        if SUBTOTAL_RE.search(s) or TOTAL_RE.search(s) or (TAX_RE.search(s) and not TAX_EXCLUDE.search(s)):
            break
        cells = _split_cells(l)
        nums_tail = []
        for c in reversed(cells):
            a = parse_amount(c) if re.fullmatch(r"[£€$₹৳¥]?\s*-?[\d,.']+\s*%?", c) else None
            if a is None or c.endswith("%"):
                break
            nums_tail.insert(0, a)
        if len(cells) <= 1:  # single-space separated OCR line: peel numbers off the end
            toks = s.split()
            nums_tail = []
            while toks and re.fullmatch(r"[£€$₹৳¥]?-?[\d,.']+", toks[-1]) and len(nums_tail) < 3:
                nums_tail.insert(0, parse_amount(toks.pop()))
            desc = " ".join(toks)
        else:
            desc = " ".join(cells[: len(cells) - len(nums_tail)])
        desc = clean_text(re.sub(r"^\d+[.)]\s+", "", desc or ""))
        if not nums_tail or not desc:
            continue
        q = u = a = None
        if len(nums_tail) >= 3:
            q, u, a = nums_tail[-3:]
        elif len(nums_tail) == 2:
            q, a = nums_tail
            if q is not None and a is not None and q != 0 and abs(q) > abs(a):
                q, a = None, nums_tail[-1]
        else:
            a = nums_tail[0]
        items.append(LineItemData(description=desc, quantity=q, unit_price=u, amount=a))
    return items


def extract_rules(text: str) -> InvoiceData:
    lines = _lines(text)
    conf: dict[str, float] = {}
    out = InvoiceData(method="rules")
    out.invoice_number, conf["invoice_number"] = _invoice_number(lines)
    out.invoice_date, conf["invoice_date"] = _invoice_date(lines)
    out.supplier_name, conf["supplier_name"] = _party(lines, SUPPLIER_LABEL)
    if not out.supplier_name:
        out.supplier_name, conf["supplier_name"] = _supplier_positional(lines)
    out.customer_name, conf["customer_name"] = _party(lines, CUSTOMER_LABEL)
    if out.customer_name and out.supplier_name and out.customer_name == out.supplier_name:
        out.customer_name, conf["customer_name"] = None, 0.0
    out.subtotal, out.tax, out.total = _totals(lines)
    conf["subtotal"] = 0.85 if out.subtotal is not None else 0.0
    conf["tax"] = 0.8 if out.tax is not None else 0.0
    conf["total"] = 0.85 if out.total is not None else 0.0
    total_lines = "\n".join(l for l in lines if TOTAL_RE.search(l) or SUBTOTAL_RE.search(l) or re.search(r"currency", l, re.I))
    out.currency = detect_currency(total_lines) or detect_currency(text)
    conf["currency"] = 0.8 if out.currency else 0.0
    out.line_items = _line_items(text.splitlines())
    out.confidence = {k: round(v, 2) for k, v in conf.items()}
    return out
