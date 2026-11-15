from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Document, Invoice, Page, ProcessingEvent
from ..pipeline import PipelineError
from ..pipeline.detect import ALLOWED_EXTENSIONS, detect_file_type
from ..pipeline.images import render_image_png
from ..pipeline.pdf import render_page_png
from ..schemas import DocumentDetail, DocumentOut, UploadResult
from ..security import safe_filename, upload_limiter
from ..worker import enqueue
from .serializers import document_detail, document_out

router = APIRouter(prefix="/documents", tags=["documents"])
CHUNK = 1024 * 1024


def _get(db: Session, doc_id: str) -> Document:
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found.")
    return doc


@router.post("", response_model=list[UploadResult], status_code=202, dependencies=[Depends(upload_limiter)],
             summary="Upload one or more documents for processing")
async def upload(files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    if len(files) > settings.max_files_per_request:
        raise HTTPException(413, f"At most {settings.max_files_per_request} files per request.")
    limit = settings.max_upload_mb * 1024 * 1024
    results: list[UploadResult] = []
    queued: list[str] = []
    for uf in files:
        name = safe_filename(uf.filename or "upload")
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            results.append(UploadResult(filename=name, accepted=False, error=f"Unsupported file type '{ext or 'none'}'."))
            continue
        dest = settings.storage_dir / f"{uuid.uuid4().hex}{ext}"
        sha, size, head = hashlib.sha256(), 0, b""
        try:
            with open(dest, "wb") as out:
                while chunk := await uf.read(CHUNK):
                    size += len(chunk)
                    if size > limit:
                        raise PipelineError("too_large", f"File exceeds {settings.max_upload_mb} MB.")
                    if len(head) < 2048:
                        head += chunk[: 2048 - len(head)]
                    sha.update(chunk)
                    out.write(chunk)
            kind = detect_file_type(name, head)  # reject bad files before queueing work
        except PipelineError as e:
            dest.unlink(missing_ok=True)
            results.append(UploadResult(filename=name, accepted=False, error=e.message))
            continue
        digest = sha.hexdigest()
        dup = db.scalar(select(Document.id).where(Document.sha256 == digest).limit(1))
        doc = Document(original_filename=name, storage_path=str(dest), file_type=kind, mime_type=uf.content_type,
                       size_bytes=size, sha256=digest, duplicate_of=dup, status="queued", stage="queued")
        db.add(doc)
        db.commit()
        queued.append(doc.id)
        results.append(UploadResult(filename=name, accepted=True, document_id=doc.id, duplicate_of=dup))
    for doc_id in queued:
        enqueue(doc_id)
    return results


@router.get("", summary="List documents")
def list_documents(
    status: str | None = None, q: str | None = None, file_type: str | None = None,
    has_invoices: bool | None = Query(None, description="false = documents where no invoice was found"),
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200), db: Session = Depends(get_db),
):
    stmt = select(Document)
    if status:
        stmt = stmt.where(Document.status == status)
    if file_type:
        stmt = stmt.where(Document.file_type == file_type)
    if q:
        stmt = stmt.where(Document.original_filename.ilike(f"%{q}%"))
    if has_invoices is not None:
        any_inv = select(Invoice.id).where(Invoice.document_id == Document.id).exists()
        stmt = stmt.where(any_inv if has_invoices else ~any_inv)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    docs = db.scalars(stmt.order_by(Document.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    ids = [d.id for d in docs]
    counts = dict(db.execute(select(Invoice.document_id, func.count()).where(Invoice.document_id.in_(ids)).group_by(Invoice.document_id)).all()) if ids else {}
    reviews = dict(db.execute(select(Invoice.document_id, func.count()).where(Invoice.document_id.in_(ids), Invoice.needs_review.is_(True)).group_by(Invoice.document_id)).all()) if ids else {}
    return {"items": [document_out(d, counts.get(d.id, 0), reviews.get(d.id, 0)) for d in docs],
            "total": total, "page": page, "page_size": page_size}


@router.get("/status", summary="Processing status for many documents (for polling)")
def batch_status(ids: str = Query(..., description="Comma-separated document ids"), db: Session = Depends(get_db)):
    id_list = [i for i in ids.split(",") if i][:200]
    docs = db.scalars(select(Document).where(Document.id.in_(id_list))).all()
    return [{"id": d.id, "status": d.status, "progress": d.progress, "stage": d.stage, "error_code": d.error_code,
             "error_message": d.error_message} for d in docs]


@router.get("/{doc_id}", response_model=DocumentDetail)
def get_document(doc_id: str, db: Session = Depends(get_db)):
    return document_detail(_get(db, doc_id))


@router.get("/{doc_id}/status", summary="Processing status")
def get_status(doc_id: str, db: Session = Depends(get_db)):
    d = _get(db, doc_id)
    return {"id": d.id, "status": d.status, "progress": d.progress, "stage": d.stage, "attempts": d.attempts,
            "error_code": d.error_code, "error_message": d.error_message, "processing_ms": d.processing_ms}


@router.get("/{doc_id}/pages/{page_number}", summary="Extracted text of a page / sheet")
def get_page(doc_id: str, page_number: int, db: Session = Depends(get_db)):
    p = db.scalar(select(Page).where(Page.document_id == doc_id, Page.page_number == page_number))
    if not p:
        raise HTTPException(404, "Page not found.")
    return {"page_number": p.page_number, "sheet_name": p.sheet_name, "method": p.method,
            "ocr_confidence": p.ocr_confidence, "text": p.text}


@router.get("/{doc_id}/pages/{page_number}/image", summary="Rendered page preview (PDF/image only)")
def page_image(doc_id: str, page_number: int, db: Session = Depends(get_db)):
    d = _get(db, doc_id)
    try:
        if d.file_type == "pdf":
            png = render_page_png(d.storage_path, page_number)
        elif d.file_type == "image":
            png = render_image_png(d.storage_path, page_number)
        else:
            raise HTTPException(400, "Spreadsheets have no page image.")
    except PipelineError as e:
        raise HTTPException(422, e.message)
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{doc_id}/file", summary="Download the original file")
def download(doc_id: str, db: Session = Depends(get_db)):
    d = _get(db, doc_id)
    if not os.path.exists(d.storage_path):
        raise HTTPException(410, "Stored file is no longer available.")
    return FileResponse(d.storage_path, filename=d.original_filename, media_type="application/octet-stream")


@router.post("/{doc_id}/reprocess", response_model=DocumentOut, status_code=202, summary="Re-run processing")
def reprocess(doc_id: str, db: Session = Depends(get_db)):
    d = _get(db, doc_id)
    if d.status in ("queued", "processing"):
        raise HTTPException(409, "Document is already being processed.")
    d.status, d.progress, d.stage, d.error_code, d.error_message = "queued", 0, "queued", None, None
    db.commit()
    enqueue(d.id)
    db.refresh(d)
    return document_out(d)


@router.post("/{doc_id}/confirm-no-invoice", response_model=DocumentOut,
             summary="Reviewer confirms a document with no extracted invoice really contains none")
def confirm_no_invoice(doc_id: str, db: Session = Depends(get_db)):
    d = _get(db, doc_id)
    if d.status in ("queued", "processing"):
        raise HTTPException(409, "Document is still being processed.")
    if d.invoices:
        raise HTTPException(409, "This document has extracted invoices; review those instead.")
    d.status = "completed"
    db.add(ProcessingEvent(document_id=d.id, attempt=d.attempts, stage="review", status="ok",
                           message="Reviewer confirmed the document contains no invoice."))
    db.commit()
    db.refresh(d)
    return document_out(d)


@router.delete("/{doc_id}", status_code=204)
def delete(doc_id: str, db: Session = Depends(get_db)):
    d = _get(db, doc_id)
    path = d.storage_path
    db.delete(d)
    db.commit()
    if not db.scalar(select(Document.id).where(Document.storage_path == path).limit(1)):
        Path(path).unlink(missing_ok=True)
