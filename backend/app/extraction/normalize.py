"""Parse messy amounts, dates and currencies into canonical values."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from ..config import settings

CURRENCY_SYMBOLS = {"£": "GBP", "€": "EUR", "$": "USD", "₹": "INR", "৳": "BDT", "¥": "JPY", "₩": "KRW", "₦": "NGN"}
ISO_CODES = {
    "GBP", "EUR", "USD", "INR", "BDT", "JPY", "CNY", "AUD", "CAD", "CHF", "SEK", "NOK", "DKK", "PLN",
    "AED", "SAR", "SGD", "HKD", "NZD", "ZAR", "PKR", "KRW", "NGN", "MYR", "THB", "TRY", "BRL", "MXN",
}
_ALIASES = {"US$": "USD", "USD$": "USD", "RS": "INR", "RS.": "INR", "TK": "BDT", "TAKA": "BDT", "STERLING": "GBP", "EURO": "EUR"}

_AMOUNT_RE = re.compile(r"-?\(?[£€$₹৳¥]?\s*-?\d[\d,.\s']*\d|-?\(?[£€$₹৳¥]?\s*-?\d")


def parse_amount(value) -> Decimal | None:
    """'£1,234.50' -> 1234.50 ; '1.234,50' -> 1234.50 ; '(12.00)' -> -12.00"""
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        try:
            d = Decimal(str(value))
            return d if d.is_finite() else None
        except InvalidOperation:
            return None
    s = str(value).strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-") or s.endswith("-")
    s = re.sub(r"[^\d.,']", "", s).replace("'", "")
    if not s or not any(c.isdigit() for c in s):
        return None
    if "," in s and "." in s:
        # whichever separator comes last is the decimal point
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = f"{head.replace(',', '')}.{tail}" if len(tail) in (1, 2) else s.replace(",", "")
    elif s.count(".") > 1:
        head, _, tail = s.rpartition(".")
        s = f"{head.replace('.', '')}.{tail}" if len(tail) in (1, 2) else s.replace(".", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return -d if neg else d


def find_amounts(line: str) -> list[Decimal]:
    out = []
    for m in _AMOUNT_RE.finditer(line):
        tok = m.group(0)
        if re.fullmatch(r"\s*\d{1,2}\s*", tok):  # lone small integers are usually qty/%, keep anyway
            pass
        a = parse_amount(tok)
        if a is not None:
            out.append(a)
    return out


def detect_currency(text: str) -> str | None:
    if not text:
        return None
    up = text.upper()
    for alias, code in _ALIASES.items():
        if re.search(rf"(?<![A-Z]){re.escape(alias)}(?![A-Z])", up):
            return code
    codes = re.findall(r"(?<![A-Z])([A-Z]{3})(?![A-Z])", up)
    for c in codes:
        if c in ISO_CODES:
            return c
    for sym, code in CURRENCY_SYMBOLS.items():
        if sym in text:
            return code
    return None


def normalize_currency(value) -> str | None:
    if not value:
        return None
    s = str(value).strip().upper()
    if s in ISO_CODES:
        return s
    if s in _ALIASES:
        return _ALIASES[s]
    return CURRENCY_SYMBOLS.get(str(value).strip()) or detect_currency(str(value))


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DATE_PATTERNS = [
    re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b"),                       # 2026-03-14
    re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})\b"),                 # 14/03/2026
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-]+([A-Za-z]{3,9})\.?,?[\s\-]+(\d{4})\b"),  # 14 March 2026
    re.compile(r"\b([A-Za-z]{3,9})\.?[\s\-]+(\d{1,2})(?:st|nd|rd|th)?,?[\s\-]+(\d{4})\b"),  # March 14, 2026
]


def _mk(y: int, m: int, d: int) -> date | None:
    if y < 100:
        y += 2000
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date(value) -> tuple[date | None, bool]:
    """Returns (date, ambiguous). Ambiguous = day/month order was a guess."""
    if value is None:
        return None, False
    if isinstance(value, date):
        return value, False
    s = str(value).strip()
    if not s:
        return None, False
    m = _DATE_PATTERNS[0].search(s)
    if m:
        return _mk(int(m[1]), int(m[2]), int(m[3])), False
    m = _DATE_PATTERNS[1].search(s)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        if a > 12 and b <= 12:
            return _mk(y, b, a), False
        if b > 12 and a <= 12:
            return _mk(y, a, b), False
        ambiguous = a != b
        return (_mk(y, b, a) if settings.date_order == "DMY" else _mk(y, a, b)), ambiguous
    m = _DATE_PATTERNS[2].search(s)
    if m and m[2][:3].lower() in _MONTHS:
        return _mk(int(m[3]), _MONTHS[m[2][:3].lower()], int(m[1])), False
    m = _DATE_PATTERNS[3].search(s)
    if m and m[1][:3].lower() in _MONTHS:
        return _mk(int(m[3]), _MONTHS[m[1][:3].lower()], int(m[2])), False
    return None, False


def clean_text(value) -> str | None:
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip(" :,-\t")
    return s or None


def norm_key(value: str | None) -> str:
    """Comparison key for names/invoice numbers: case/punctuation-insensitive."""
    if not value:
        return ""
    s = re.sub(r"[^a-z0-9]", "", value.lower())
    for suffix in ("limited", "ltd", "llc", "inc", "plc", "gmbh", "co"):
        if s.endswith(suffix) and len(s) > len(suffix) + 2:
            s = s[: -len(suffix)]
    return s


# ---- common OCR confusions (l/i/1, I/1, O/0, S/$) in places where the right answer is knowable
_SUFFIX_FIXES = [
    (re.compile(r"\b[pP][il1|][cC]\b\.?$"), "plc"),
    (re.compile(r"\b[lL1I|][tT][dD]\b\.?$"), "Ltd"),
    (re.compile(r"\b[lL1I|][lL1I|][cC]\b\.?$"), "LLC"),
]


def fix_company_suffix(name: str | None) -> str | None:
    """'Contoso Retail pic' -> 'Contoso Retail plc', 'Acme 1td' -> 'Acme Ltd'."""
    if not name:
        return name
    for rx, rep in _SUFFIX_FIXES:
        name = rx.sub(rep, name)
    return name


_ID_CHARS = str.maketrans({"$": "S", "!": "I", "|": "I"})


def fix_ocr_id(value: str | None) -> str | None:
    """'$1-78077' -> 'SI-78077'. A short alphabetic prefix before the first dash
    ('SI-', 'INV-') can't legitimately contain 1/0, so those are read as I/O."""
    if not value:
        return value
    value = value.translate(_ID_CHARS)
    head, sep, tail = value.partition("-")
    if sep and 2 <= len(head) <= 4 and head[0].isalpha() and all(c.isalpha() or c in "10" for c in head):
        head = head.replace("1", "I").replace("0", "O")
    return head + sep + tail
