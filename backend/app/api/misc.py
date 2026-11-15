from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Document, Invoice, ValidationIssue
from ..processor import failed_document_ids
from ..reconcile import reconcile
from ..schemas import ReconcileIn, SearchHit
from ..search.service import hybrid_search, keyword_search, semantic_search
from ..worker import enqueue
from .serializers import invoice_summary

router = APIRouter(tags=["search & jobs"])


@router.get("/search", response_model=list[SearchHit], summary="Keyword, semantic or hybrid search with filters")
def search(
    q: str = Query("", max_length=500), mode: str = Query("hybrid", pattern="^(keyword|semantic|hybrid)$"),
    status: str | None = None, needs_review: bool | None = None, supplier: str | None = None, currency: str | None = None,
    date_from: date | None = None, date_to: date | None = None, min_total: float | None = None, max_total: float | None = None,
    file_type: str | None = None, limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db),
):
    f = dict(status=status, needs_review=needs_review, supplier=supplier, currency=currency, date_from=date_from,
             date_to=date_to, min_total=min_total, max_total=max_total, file_type=file_type)
    if not q.strip() or mode == "keyword":
        hits = [(i, s, snip, ["keyword"] if q.strip() else []) for i, s, snip in keyword_search(db, q, f, limit)]
    elif mode == "semantic":
        hits = [(i, s, snip, ["semantic"]) for i, s, snip in semantic_search(db, q, f, limit)]
    else:
        hits = hybrid_search(db, q, f, limit)
    return [SearchHit(score=s, snippet=snip, matched_by=how, invoice=invoice_summary(inv), document_filename=inv.document.original_filename)
            for inv, s, snip, how in hits]


@router.post("/reconcile", summary="Cross-verify two documents (PDF↔PDF, PDF↔Excel/CSV)")
def cross_verify(body: ReconcileIn, db: Session = Depends(get_db)):
    a, b = db.get(Document, body.document_a), db.get(Document, body.document_b)
    if not a or not b:
        raise HTTPException(404, "Both documents must exist.")
    if a.id == b.id:
        raise HTTPException(422, "Pick two different documents.")
    pending = [d.original_filename for d in (a, b) if d.status in ("queued", "processing")]
    if pending:
        raise HTTPException(409, f"Still processing: {', '.join(pending)}")
    return reconcile(a, b)


@router.post("/jobs/reprocess-failed", summary="Re-queue every failed document")
def reprocess_failed(db: Session = Depends(get_db)):
    ids = failed_document_ids(db)
    for i in ids:
        d = db.get(Document, i)
        d.status, d.progress, d.stage, d.error_code, d.error_message = "queued", 0, "queued", None, None
    db.commit()
    for i in ids:
        enqueue(i)
    return {"requeued": len(ids), "document_ids": ids}


@router.get("/stats", summary="Dashboard metrics")
def stats(db: Session = Depends(get_db)):
    by_status = dict(db.execute(select(Document.status, func.count()).group_by(Document.status)).all())
    inv_total = db.scalar(select(func.count()).select_from(Invoice)) or 0
    review = db.scalar(select(func.count()).select_from(Invoice).where(Invoice.needs_review.is_(True))) or 0
    no_invoice = db.scalar(select(func.count()).select_from(Document).where(
        Document.status == "needs_review", ~select(Invoice.id).where(Invoice.document_id == Document.id).exists())) or 0
    avg_ms = db.scalar(select(func.avg(Document.processing_ms)).where(Document.status.in_(["completed", "needs_review"])))
    pages = db.scalar(select(func.sum(Document.page_count))) or 0
    ocr = db.scalar(select(func.sum(Document.ocr_pages))) or 0
    by_currency = [{"currency": c or "—", "count": n, "total": str(t or 0)} for c, n, t in db.execute(
        select(Invoice.currency, func.count(), func.sum(Invoice.total)).group_by(Invoice.currency).order_by(func.count().desc())).all()]
    top_issues = [{"code": c, "count": n} for c, n in db.execute(
        select(ValidationIssue.code, func.count()).group_by(ValidationIssue.code).order_by(func.count().desc()).limit(8)).all()]
    by_method = dict(db.execute(select(Invoice.extraction_method, func.count()).group_by(Invoice.extraction_method)).all())
    docs = sum(by_status.values())
    done = by_status.get("completed", 0) + by_status.get("needs_review", 0)
    return {
        "documents": docs, "documents_by_status": by_status, "invoices": inv_total, "needs_review": review,
        "documents_without_invoices": no_invoice,
        "pages_processed": pages, "ocr_pages": ocr, "avg_processing_ms": int(avg_ms) if avg_ms else None,
        "success_rate": round(done / (done + by_status.get("failed", 0)), 4) if (done + by_status.get("failed", 0)) else None,
        "by_currency": by_currency, "top_issues": top_issues, "by_method": by_method,
        "llm": {"enabled": settings.llm_enabled, "provider": settings.llm_provider if settings.llm_enabled else None,
                "model": settings.llm_endpoint[1] if settings.llm_enabled else None},
    }
