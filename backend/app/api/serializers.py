from __future__ import annotations

from ..models import Document, Invoice
from ..schemas import DocumentDetail, DocumentOut, EventOut, InvoiceDetail, InvoiceSummary, PageMeta


def invoice_summary(inv: Invoice) -> InvoiceSummary:
    s = InvoiceSummary.model_validate(inv)
    s.error_count = sum(1 for i in inv.issues if i.severity == "error")
    s.warning_count = sum(1 for i in inv.issues if i.severity == "warning")
    return s


def invoice_detail(inv: Invoice) -> InvoiceDetail:
    base = invoice_summary(inv).model_dump()
    return InvoiceDetail(
        **base,
        model=inv.model,
        field_confidence=inv.field_confidence or {},
        line_items=inv.line_items,
        issues=sorted(inv.issues, key=lambda i: (i.severity != "error", i.code)),
        corrections=inv.corrections,
        notes=(inv.raw_extraction or {}).get("notes", []),
        document_filename=inv.document.original_filename if inv.document else None,
    )


def document_out(doc: Document, invoice_count: int | None = None, review_count: int | None = None) -> DocumentOut:
    d = DocumentOut.model_validate(doc)
    d.invoice_count = invoice_count if invoice_count is not None else len(doc.invoices)
    d.review_count = review_count if review_count is not None else sum(1 for i in doc.invoices if i.needs_review)
    return d


def document_detail(doc: Document) -> DocumentDetail:
    base = document_out(doc).model_dump()
    return DocumentDetail(
        **base,
        invoices=[invoice_summary(i) for i in doc.invoices],
        events=[EventOut.model_validate(e) for e in doc.events],
        pages=[PageMeta(page_number=p.page_number, sheet_name=p.sheet_name, method=p.method,
                        ocr_confidence=p.ocr_confidence, chars=len(p.text or "")) for p in doc.pages],
    )
