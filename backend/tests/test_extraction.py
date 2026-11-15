"""Task 2: normalisation, rule extraction, LLM extraction (mocked), grounding."""
import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.config import settings
from app.extraction import llm as llm_mod
from app.extraction.grounding import ground
from app.extraction.normalize import detect_currency, parse_amount, parse_date
from app.extraction.rules import extract_rules
from app.extraction.service import extract_text, from_llm_json
from app.extraction.tables import extract_table, is_invoice_table
from app.pipeline import SheetTable

SAMPLE = """Northwind Supplies Ltd
12 High Street, Manchester
INVOICE
Invoice No: INV-2026-0042
Invoice Date: 14/03/2026
Due Date: 13/04/2026
Bill To: Contoso Retail plc
Description   Qty   Unit Price   Amount
Printer paper A4   10   4.50   45.00
Toner cartridge   2   60.00   120.00
Subtotal: £165.00
VAT (20%): £33.00
Total Due: £198.00
"""


# ---------- normalisation ----------
@pytest.mark.parametrize("raw,val", [
    ("£1,234.50", "1234.50"), ("1.234,50", "1234.50"), ("(12.00)", "-12.00"), ("EUR 3 400,10", "3400.10"),
    ("1,234", "1234"), ("12,5", "12.5"), (99, "99"), ("abc", None), ("", None),
])
def test_parse_amount(raw, val):
    assert parse_amount(raw) == (Decimal(val) if val else None)


@pytest.mark.parametrize("raw,d,amb", [
    ("2026-03-14", date(2026, 3, 14), False), ("14/03/2026", date(2026, 3, 14), False),
    ("03/14/2026", date(2026, 3, 14), False), ("March 14, 2026", date(2026, 3, 14), False),
    ("14 Mar 2026", date(2026, 3, 14), False), ("03/04/2026", date(2026, 4, 3), True), ("not a date", None, False),
])
def test_parse_date(raw, d, amb):
    assert parse_date(raw) == (d, amb)


@pytest.mark.parametrize("text,code", [("Total £10", "GBP"), ("Amount due USD 5", "USD"), ("€ 4,00", "EUR"), ("Total 10", None)])
def test_currency(text, code):
    assert detect_currency(text) == code


# ---------- rules ----------
def test_rules_extract_all_fields():
    d = extract_rules(SAMPLE)
    assert d.invoice_number == "INV-2026-0042"
    assert d.invoice_date == date(2026, 3, 14)  # not the due date
    assert d.supplier_name == "Northwind Supplies Ltd"
    assert d.customer_name == "Contoso Retail plc"
    assert (d.subtotal, d.tax, d.total) == (Decimal("165.00"), Decimal("33.00"), Decimal("198.00"))
    assert d.currency == "GBP"
    assert [(li.description, li.amount) for li in d.line_items] == [("Printer paper A4", Decimal("45.00")), ("Toner cartridge", Decimal("120.00"))]


def test_rules_never_invent_missing_values():
    text = "Some Company\nInvoice No: X-991\nThanks for shopping"
    d = extract_rules(text)
    assert d.invoice_number == "X-991"
    assert d.total is None and d.subtotal is None and d.tax is None and d.invoice_date is None and d.currency is None
    assert d.customer_name is None


def test_rules_ocr_merged_columns():
    text = "Tax Invoice\nInvoice # 2026/99374-25 Supplier: Oakfield Catering Ltd\nDate of issue: July 19, 2026 Customer: Wide World Importers\nAmount Due GBP 932.04"
    d = extract_rules(text)
    assert d.supplier_name == "Oakfield Catering Ltd" and d.customer_name == "Wide World Importers"
    assert d.invoice_number == "2026/99374-25" and d.total == Decimal("932.04")


# ---------- spreadsheet tables ----------
def test_table_one_row_per_line_item():
    t = SheetTable("CSV", ["Invoice Number", "Date", "Vendor", "Customer", "Description", "Qty", "Unit Price", "Line Total", "Net Total", "Tax", "Grand Total", "Currency"], [
        ["A1", "01/02/2026", "Acme", "Bob Ltd", "Widget", "2", "5.00", "10.00", "30.00", "6.00", "36.00", "GBP"],
        ["A1", "01/02/2026", "Acme", "Bob Ltd", "Gadget", "1", "20.00", "20.00", "30.00", "6.00", "36.00", "GBP"],
        ["B2", "03/02/2026", "Acme", "Bob Ltd", "Widget", "1", "5.00", "5.00", "5.00", "1.00", "6.00", "GBP"],
    ])
    assert is_invoice_table(t)
    out = extract_table(t)
    assert [d.invoice_number for _, d in out] == ["A1", "B2"]
    a1 = out[0][1]
    assert a1.total == Decimal("36.00") and len(a1.line_items) == 2 and a1.line_items[1].amount == Decimal("20.00")


def test_not_an_invoice_table():
    assert not is_invoice_table(SheetTable("S", ["Name", "Email"], [["a", "b"]]))


# ---------- LLM (mocked HTTP) ----------
def _transport(responses):
    calls = []

    def handler(request: httpx.Request):
        calls.append(json.loads(request.content))
        status, body = responses.pop(0)
        return httpx.Response(status, json=body, headers={"retry-after": "0"} if status == 429 else {})
    return httpx.MockTransport(handler), calls


def _ok(content: str):
    return 200, {"choices": [{"message": {"content": content}}]}


@pytest.fixture
def llm_on(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)


GOOD = {"invoice_number": "INV-2026-0042", "invoice_date": "14/03/2026", "supplier_name": "Northwind Supplies Ltd",
        "customer_name": "Contoso Retail plc", "subtotal": 165, "tax": 33, "total": 198, "currency": "GBP",
        "line_items": [{"description": "Toner cartridge", "quantity": 2, "unit_price": 60, "amount": 120}],
        "confidence": {"total": 0.99}}


def test_llm_request_and_parse(llm_on):
    transport, calls = _transport([_ok(json.dumps(GOOD))])
    obj, model = llm_mod.chat_extract(SAMPLE, httpx.Client(transport=transport))
    assert obj["total"] == 198 and model == "llama-3.3-70b-versatile"
    assert calls[0]["temperature"] == 0 and calls[0]["response_format"] == {"type": "json_object"}
    assert "NEVER invent" in calls[0]["messages"][0]["content"]


def test_llm_markdown_fenced_json(llm_on):
    transport, _ = _transport([_ok("Here you go:\n```json\n" + json.dumps(GOOD) + "\n```")])
    obj, _ = llm_mod.chat_extract(SAMPLE, httpx.Client(transport=transport))
    assert obj["invoice_number"] == "INV-2026-0042"


def test_llm_retries_rate_limit(llm_on):
    transport, calls = _transport([(429, {"error": "rate"}), (503, {}), _ok(json.dumps(GOOD))])
    obj, _ = llm_mod.chat_extract(SAMPLE, httpx.Client(transport=transport))
    assert len(calls) == 3 and obj["total"] == 198


def test_llm_gives_up(llm_on, monkeypatch):
    monkeypatch.setattr(settings, "llm_max_retries", 1)
    transport, _ = _transport([(500, {}), (500, {})])
    with pytest.raises(llm_mod.LLMError):
        llm_mod.chat_extract(SAMPLE, httpx.Client(transport=transport))


def test_grounding_removes_hallucinated_values():
    obj = dict(GOOD, subtotal=999.99, customer_name="Imaginary Corp", currency="USD")
    d = ground(from_llm_json(obj, "m"), SAMPLE)
    assert d.subtotal is None and d.customer_name is None and d.currency is None
    assert d.total == Decimal("198") and d.supplier_name == "Northwind Supplies Ltd"
    assert sum("Removed" in n for n in d.notes) == 3


def test_service_falls_back_to_rules_when_llm_down(llm_on, monkeypatch):
    def boom(text, client=None):
        raise llm_mod.LLMError("HTTP 503")
    monkeypatch.setattr("app.extraction.service.chat_extract", boom)
    d = extract_text(SAMPLE)
    assert d.method == "rules_fallback" and d.total == Decimal("198.00")


def test_service_fills_llm_gaps_from_rules(llm_on, monkeypatch):
    monkeypatch.setattr("app.extraction.service.chat_extract", lambda t: (dict(GOOD, tax=None, customer_name=None), "m"))
    d = extract_text(SAMPLE)
    assert d.method == "llm" and d.tax == Decimal("33.00") and d.customer_name == "Contoso Retail plc"
    assert d.confidence["tax"] <= 0.7


# ---------- OCR confusion handling ----------
@pytest.mark.parametrize("raw,fixed", [("$1-78077", "SI-78077"), ("S!-5291", "SI-5291"), ("S1-39195", "SI-39195"),
                                       ("INV-12345", "INV-12345"), ("A12345", "A12345"), ("2026/111", "2026/111")])
def test_fix_ocr_invoice_ids(raw, fixed):
    from app.extraction.normalize import fix_ocr_id
    assert fix_ocr_id(raw) == fixed


def test_company_suffix_and_grounding_tolerate_ocr():
    from app.extraction.normalize import fix_company_suffix
    assert fix_company_suffix("Contoso Retail pic") == "Contoso Retail plc"
    assert fix_company_suffix("Pic n Save") == "Pic n Save"
    text = "INVOICE NUMBER $1-78077\nBILLED TO Contoso Retail pic\nTOTAL £10.00"
    d = ground(from_llm_json({"invoice_number": "SI-78077", "customer_name": "Contoso Retail plc", "total": 10}, "m"), text)
    assert d.invoice_number == "SI-78077" and d.customer_name == "Contoso Retail plc"
