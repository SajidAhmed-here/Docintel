"""Extraction strategy for one invoice-sized chunk of text.

LLM (if configured) -> normalise -> ground against source text -> fill any
remaining gaps from the rule extractor. On LLM failure (rate limit, outage)
fall back to rules so the document still completes, flagged for review.
"""
from __future__ import annotations

import logging

from .grounding import ground
from .llm import LLMError, chat_extract
from .normalize import clean_text, detect_currency, fix_company_suffix, normalize_currency, parse_amount, parse_date
from .rules import extract_rules
from .schema import FIELDS, InvoiceData, LineItemData
from ..config import settings

log = logging.getLogger(__name__)


def from_llm_json(obj: dict, model: str) -> InvoiceData:
    d = InvoiceData(method="llm", model=model, raw=obj)
    d.invoice_number = clean_text(obj.get("invoice_number"))
    d.invoice_date, amb = parse_date(obj.get("invoice_date"))
    d.supplier_name = fix_company_suffix(clean_text(obj.get("supplier_name")))
    d.customer_name = fix_company_suffix(clean_text(obj.get("customer_name")))
    d.subtotal = parse_amount(obj.get("subtotal"))
    d.tax = parse_amount(obj.get("tax"))
    d.total = parse_amount(obj.get("total"))
    d.currency = normalize_currency(obj.get("currency"))
    for li in obj.get("line_items") or []:
        if not isinstance(li, dict):
            continue
        d.line_items.append(LineItemData(
            description=clean_text(li.get("description")),
            quantity=parse_amount(li.get("quantity")),
            unit_price=parse_amount(li.get("unit_price")),
            amount=parse_amount(li.get("amount")),
        ))
    conf = obj.get("confidence") or {}
    for f in FIELDS:
        try:
            c = float(conf.get(f, 0.8))
        except (TypeError, ValueError):
            c = 0.8
        d.confidence[f] = round(max(0.0, min(1.0, c)), 2) if getattr(d, f) is not None else 0.0
    if amb and d.invoice_date:
        d.confidence["invoice_date"] = min(d.confidence["invoice_date"], 0.6)
    return d


def _fill_gaps(primary: InvoiceData, backup: InvoiceData) -> InvoiceData:
    for f in FIELDS:
        if getattr(primary, f) is None and getattr(backup, f) is not None:
            setattr(primary, f, getattr(backup, f))
            primary.confidence[f] = min(backup.confidence.get(f, 0.5), 0.7)
            primary.notes.append(f"'{f}' filled by rule-based extractor.")
    if not primary.line_items and backup.line_items:
        primary.line_items = backup.line_items
    return primary


def extract_text(text: str) -> InvoiceData:
    rules = extract_rules(text)
    if not settings.llm_enabled:
        return rules
    try:
        obj, model = chat_extract(text)
    except LLMError as e:
        log.warning("LLM unavailable, using rules: %s", e)
        rules.method = "rules_fallback"
        rules.notes.append(f"LLM unavailable ({str(e)[:120]}); used rule-based extraction.")
        return rules
    data = ground(from_llm_json(obj, model), text)
    data = _fill_gaps(data, rules)
    if not data.currency:
        data.currency = detect_currency(text)
        if data.currency:
            data.confidence["currency"] = 0.7
    return data
