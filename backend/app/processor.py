"""End-to-end processing of one document. Called by the background worker.

Stages (each recorded in processing_events):
  detect -> text (PDF text / OCR / spreadsheet) -> segment -> extract -> validate -> index
"""
from __future__ import annotations

import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .extraction.schema import InvoiceData
from .extraction.service import extract_text
from .extraction.tables import extract_table, is_invoice_table
from .models import Document, Invoice, LineItem, Page, ProcessingEvent, ValidationIssue
from .pipeline import PageResult, PipelineError, TextResult
from .pipeline.detect import detect_file_type
from .pipeline.images import extract_image
from .pipeline.pdf import extract_pdf
from .pipeline.segment import segment_pages
from .pipeline.spreadsheets import extract_csv, extract_excel
from .search.service import index_document
from .validation.rules import needs_review, validate_invoice

log = logging.getLogger(__name__)


class Progress:
    """Writes stage/progress to the DB at most every 0.5s (cheap on huge files)."""

    def __init__(self, doc_id: str):
        self.doc_id = doc_id
        self._last = 0.0

    def set(self, pct: int, stage: str, force: bool = False):
        now = time.monotonic()
        if not force and now - self._last < 0.5:
            return
        self._last = now
        with SessionLocal() as db:
            d = db.get(Document, self.doc_id)
            if d:
                d.progress, d.stage = max(0, min(100, pct)), stage
                db.commit()


def _event(db, doc: Document, stage: str, status: str, message: str | None = None, t0: float | None = None):
    db.add(ProcessingEvent(document_id=doc.id, attempt=doc.attempts, stage=stage, status=status, message=message,
                           duration_ms=int((time.monotonic() - t0) * 1000) if t0 else None))
    db.commit()


def extract_document_text(path: str, kind: str, progress=None) -> TextResult:
    res = TextResult()
    if kind == "pdf":
        res.pages = extract_pdf(path, progress)
    elif kind == "image":
        res.pages = extract_image(path, progress)
    elif kind in ("xlsx", "xls"):
        res.pages, res.tables = extract_excel(path, kind)
    elif kind == "csv":
        res.pages, res.tables = extract_csv(path)
    else:
        raise PipelineError("unsupported_type", f"Unsupported type {kind}")
    res.warnings = [p.warning for p in res.pages if p.warning]
    return res


def build_invoices(res: TextResult) -> list[tuple[dict, InvoiceData]]:
    """Returns [(location info, extraction)] for every invoice found."""
    found: list[tuple[dict, InvoiceData]] = []
    table_sheets = set()
    for t in res.tables:
        if is_invoice_table(t):
            table_sheets.add(t.sheet_name)
            for ref, data in extract_table(t):
                found.append(({"source_ref": ref}, data))
    pages: list[PageResult] = [p for p in res.pages if p.sheet_name not in table_sheets]
    if res.tables:  # form-style sheets: one invoice per sheet
        segs = [(p, p.text) for p in pages if p.text.strip()]
        jobs = [({"page_start": p.page_number, "page_end": p.page_number, "source_ref": f"Sheet '{p.sheet_name}'"}, text) for p, text in segs]
    else:
        jobs = [({"page_start": s.page_start, "page_end": s.page_end}, s.text) for s in segment_pages(pages)]
    if jobs:
        workers = max(1, settings.llm_concurrency) if settings.llm_enabled else 1
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for loc, data in zip([j[0] for j in jobs], pool.map(lambda j: extract_text(j[1]), jobs)):
                if looks_like_invoice(data):
                    found.append((loc, data))
    return found


def looks_like_invoice(d: InvoiceData) -> bool:
    """Notes sheets, cover letters and T&C pages should not become empty 'invoices'."""
    core = [d.invoice_number, d.invoice_date, d.subtotal, d.tax, d.total]
    has_amount_items = any(li.amount is not None for li in d.line_items)
    return (sum(v is not None for v in core) >= 2 and (d.invoice_number or d.total is not None or d.subtotal is not None)) \
        or (has_amount_items and (d.invoice_number or d.total is not None))


def _save_invoice(db, doc: Document, idx: int, loc: dict, d: InvoiceData) -> Invoice:
    inv = Invoice(
        document_id=doc.id, segment_index=idx, page_start=loc.get("page_start"), page_end=loc.get("page_end"),
        source_ref=loc.get("source_ref"), invoice_number=d.invoice_number, invoice_date=d.invoice_date,
        supplier_name=d.supplier_name, customer_name=d.customer_name, subtotal=d.subtotal, tax=d.tax,
        total=d.total, currency=d.currency, field_confidence=d.confidence, extraction_method=d.method,
        model=d.model, raw_extraction={"notes": d.notes, "llm": d.raw},
    )
    for pos, li in enumerate(d.line_items):
        inv.line_items.append(LineItem(position=pos, description=li.description, quantity=li.quantity,
                                       unit_price=li.unit_price, amount=li.amount))
    db.add(inv)
    return inv


def run_validation(db, inv: Invoice) -> None:
    db.flush()
    inv.issues.clear()
    issues = validate_invoice(inv, db)
    for note in (inv.raw_extraction or {}).get("notes", []):
        if note.startswith("Removed"):
            inv.issues.append(ValidationIssue(code="UNGROUNDED_VALUE", severity="warning", field=None, message=note))
        elif note.startswith("LLM unavailable"):
            inv.issues.append(ValidationIssue(code="LLM_FALLBACK", severity="warning", field=None, message=note))
    for i in issues:
        inv.issues.append(ValidationIssue(code=i.code, severity=i.severity, field=i.field, message=i.message, details=i.details))
    review = needs_review(issues) or any(x.code == "UNGROUNDED_VALUE" for x in inv.issues)
    edited = inv.status in ("corrected", "approved") or bool(inv.corrections)
    inv.needs_review = review
    inv.status = "needs_review" if review else ("corrected" if edited else "ok")


def process_document(doc_id: str) -> None:
    t_start = time.monotonic()
    prog = Progress(doc_id)
    db = SessionLocal()
    doc = db.get(Document, doc_id)
    if doc is None:
        db.close()
        return
    try:
        doc.attempts += 1
        doc.status, doc.stage, doc.progress = "processing", "detecting", 2
        doc.error_code = doc.error_message = None
        doc.started_at, doc.finished_at = datetime.now(timezone.utc), None
        # reprocessing: clear previous derived data (corrections are kept on the old rows' audit only)
        for inv in list(doc.invoices):
            db.delete(inv)
        for p in list(doc.pages):
            db.delete(p)
        db.commit()

        t0 = time.monotonic()
        with open(doc.storage_path, "rb") as f:
            head = f.read(2048)
        kind = detect_file_type(doc.original_filename, head)
        _event(db, doc, "detect", "ok", f"Detected {kind}", t0)

        t0 = time.monotonic()
        prog.set(5, "reading text", force=True)
        res = extract_document_text(doc.storage_path, kind, lambda done, total: prog.set(5 + int(60 * done / max(total, 1)), f"reading page {done}/{total}"))
        db.refresh(doc)
        for p in res.pages:
            db.add(Page(document_id=doc.id, page_number=p.page_number, sheet_name=p.sheet_name, method=p.method,
                        ocr_confidence=p.ocr_confidence, text=p.text))
        doc.page_count = len(res.pages)
        doc.ocr_pages = res.ocr_pages
        db.commit()
        msg = f"{len(res.pages)} page(s)/sheet(s), {res.ocr_pages} via OCR"
        if res.warnings:
            msg += f"; {len(res.warnings)} warning(s): " + "; ".join(res.warnings[:3])
        _event(db, doc, "text", "warning" if res.warnings else "ok", msg, t0)
        if not any(p.text.strip() for p in res.pages):
            raise PipelineError("no_text", "No readable text was found (blank pages or unreadable scan).")

        t0 = time.monotonic()
        prog.set(68, "extracting fields", force=True)
        found = build_invoices(res)
        for idx, (loc, data) in enumerate(found):
            _save_invoice(db, doc, idx, loc, data)
        db.commit()
        methods = sorted({d.method for _, d in found})
        _event(db, doc, "extract", "ok" if found else "warning",
               f"{len(found)} invoice(s) extracted via {', '.join(methods) or 'n/a'}", t0)

        t0 = time.monotonic()
        prog.set(88, "validating", force=True)
        db.refresh(doc)
        for inv in doc.invoices:
            run_validation(db, inv)
        db.commit()
        flagged = sum(1 for i in doc.invoices if i.needs_review)
        _event(db, doc, "validate", "warning" if flagged else "ok", f"{flagged} of {len(doc.invoices)} invoice(s) need review", t0)

        t0 = time.monotonic()
        prog.set(95, "indexing", force=True)
        n = index_document(db, doc)
        db.commit()
        _event(db, doc, "index", "ok", f"{n} search vectors", t0)

        doc.status = "needs_review" if (flagged or not doc.invoices) else "completed"
        if not doc.invoices:
            _event(db, doc, "extract", "warning", "No invoice data found in this document.")
        doc.progress, doc.stage = 100, "done"
    except PipelineError as e:
        db.rollback()
        doc = db.get(Document, doc_id)
        doc.status, doc.error_code, doc.error_message, doc.stage = "failed", e.code, e.message, "failed"
        _event(db, doc, "failed", "failed", f"{e.code}: {e.message}")
    except Exception as e:  # unexpected: keep the trace in history for debugging
        log.exception("processing %s crashed", doc_id)
        db.rollback()
        doc = db.get(Document, doc_id)
        doc.status, doc.error_code, doc.error_message, doc.stage = "failed", "internal_error", str(e)[:500], "failed"
        _event(db, doc, "failed", "failed", traceback.format_exc()[-1500:])
    finally:
        doc = db.get(Document, doc_id)
        if doc:
            doc.finished_at = datetime.now(timezone.utc)
            doc.processing_ms = int((time.monotonic() - t_start) * 1000)
            db.commit()
        db.close()


def failed_document_ids(db) -> list[str]:
    return list(db.scalars(select(Document.id).where(Document.status == "failed")).all())
