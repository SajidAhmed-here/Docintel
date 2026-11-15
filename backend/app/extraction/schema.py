from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

FIELDS = ["invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax", "total", "currency"]


class LineItemData(BaseModel):
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class InvoiceData(BaseModel):
    """Canonical extraction result. None always means 'not found', never a guess."""

    invoice_number: str | None = None
    invoice_date: date | None = None
    supplier_name: str | None = None
    customer_name: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    currency: str | None = None
    line_items: list[LineItemData] = Field(default_factory=list)
    confidence: dict[str, float] = Field(default_factory=dict)
    method: str = "rules"
    model: str | None = None
    notes: list[str] = Field(default_factory=list)  # e.g. ungrounded values removed
    raw: dict | None = None
