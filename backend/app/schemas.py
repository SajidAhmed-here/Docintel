from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def _dec(self, v: Any):
        if isinstance(v, Decimal):
            return fmt_decimal(v)
        if isinstance(v, datetime) and v.tzinfo is None:  # SQLite drops tzinfo; values are stored in UTC
            return v.replace(tzinfo=timezone.utc).isoformat()
        return v


def fmt_decimal(v: Decimal) -> str:
    """165.0000 -> '165.00', 2.5 -> '2.50', 0.125 -> '0.125' (never loses precision)."""
    s = format(v.normalize(), "f")
    whole, _, frac = s.partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"


class LineItemOut(_Out):
    position: int
    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None


class IssueOut(_Out):
    code: str
    severity: str
    field: str | None
    message: str
    details: dict | None = None


class CorrectionOut(_Out):
    field: str
    old_value: str | None
    new_value: str | None
    corrected_by: str | None
    created_at: datetime


class InvoiceSummary(_Out):
    id: str
    document_id: str
    segment_index: int
    page_start: int | None
    page_end: int | None
    source_ref: str | None
    invoice_number: str | None
    invoice_date: date | None
    supplier_name: str | None
    customer_name: str | None
    subtotal: Decimal | None
    tax: Decimal | None
    total: Decimal | None
    currency: str | None
    status: str
    needs_review: bool
    extraction_method: str
    error_count: int = 0
    warning_count: int = 0


class InvoiceDetail(InvoiceSummary):
    model: str | None
    field_confidence: dict
    line_items: list[LineItemOut]
    issues: list[IssueOut]
    corrections: list[CorrectionOut]
    notes: list[str] = []
    document_filename: str | None = None


class EventOut(_Out):
    attempt: int
    stage: str
    status: str
    message: str | None
    duration_ms: int | None
    created_at: datetime


class PageMeta(_Out):
    page_number: int
    sheet_name: str | None
    method: str
    ocr_confidence: float | None
    chars: int = 0


class DocumentOut(_Out):
    id: str
    original_filename: str
    file_type: str
    size_bytes: int
    status: str
    stage: str | None
    progress: int
    page_count: int | None
    ocr_pages: int
    error_code: str | None
    error_message: str | None
    attempts: int
    duplicate_of: str | None
    processing_ms: int | None
    created_at: datetime
    finished_at: datetime | None
    invoice_count: int = 0
    review_count: int = 0


class DocumentDetail(DocumentOut):
    invoices: list[InvoiceSummary]
    events: list[EventOut]
    pages: list[PageMeta]


class UploadResult(BaseModel):
    filename: str
    accepted: bool
    document_id: str | None = None
    duplicate_of: str | None = None
    error: str | None = None


class Page_(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int


class LineItemIn(BaseModel):
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class CorrectionIn(BaseModel):
    """Only the keys present are changed. Send null to clear a value."""

    fields: dict[str, Any] = Field(default_factory=dict)
    line_items: list[LineItemIn] | None = None
    corrected_by: str | None = Field(default=None, max_length=128)
    approve: bool = False  # reviewer accepts the invoice despite remaining warnings


class ReconcileIn(BaseModel):
    document_a: str
    document_b: str


class SearchHit(BaseModel):
    score: float
    snippet: str | None
    matched_by: list[str] = []
    invoice: InvoiceSummary
    document_filename: str
