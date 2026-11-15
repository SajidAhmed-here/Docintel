from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Document, Embedding, Invoice
from .embeddings import chunk, cosine, embed

MAX_CHUNKS_PER_DOC = 400  # cap index size for 1000-page files; invoice summaries are always indexed


def invoice_summary(inv: Invoice) -> str:
    items = "; ".join((li.description or "") for li in inv.line_items[:30])
    parts = [
        f"Invoice {inv.invoice_number or ''}", f"from {inv.supplier_name or ''}", f"to {inv.customer_name or ''}",
        f"dated {inv.invoice_date or ''}", f"total {inv.total or ''} {inv.currency or ''}", f"items: {items}",
    ]
    return " ".join(parts)


def index_document(db: Session, doc: Document) -> int:
    db.query(Embedding).filter(Embedding.document_id == doc.id).delete()
    rows: list[tuple[str | None, str]] = []
    for inv in doc.invoices:
        inv.search_text = " ".join(filter(None, [
            inv.invoice_number, inv.supplier_name, inv.customer_name, inv.currency,
            str(inv.total) if inv.total is not None else None, doc.original_filename,
            " ".join(li.description or "" for li in inv.line_items),
        ])).lower()
        rows.append((inv.id, invoice_summary(inv)))
    text_chunks: list[str] = []
    for p in doc.pages:
        text_chunks.extend(chunk(p.text))
    for c in text_chunks[:MAX_CHUNKS_PER_DOC]:
        rows.append((None, c))
    if not rows:
        return 0
    vectors = embed([t for _, t in rows])
    for i, ((inv_id, t), v) in enumerate(zip(rows, vectors)):
        db.add(Embedding(document_id=doc.id, invoice_id=inv_id, chunk_index=i, text=t[:2000], vector=v))
    return len(rows)


def apply_filters(stmt, f: dict):
    if f.get("status"):
        stmt = stmt.where(Invoice.status == f["status"])
    if f.get("needs_review") is not None:
        stmt = stmt.where(Invoice.needs_review == f["needs_review"])
    if f.get("supplier"):
        stmt = stmt.where(Invoice.supplier_name.ilike(f"%{f['supplier']}%"))
    if f.get("currency"):
        stmt = stmt.where(Invoice.currency == f["currency"].upper())
    if f.get("date_from"):
        stmt = stmt.where(Invoice.invoice_date >= f["date_from"])
    if f.get("date_to"):
        stmt = stmt.where(Invoice.invoice_date <= f["date_to"])
    if f.get("min_total") is not None:
        stmt = stmt.where(Invoice.total >= Decimal(str(f["min_total"])))
    if f.get("max_total") is not None:
        stmt = stmt.where(Invoice.total <= Decimal(str(f["max_total"])))
    if f.get("file_type"):
        stmt = stmt.where(Document.file_type == f["file_type"])
    return stmt


def keyword_search(db: Session, q: str, filters: dict, limit: int = 50) -> list[tuple[Invoice, float, str | None]]:
    stmt = select(Invoice).join(Document)
    stmt = apply_filters(stmt, filters)
    terms = [t for t in q.lower().split() if t]
    for t in terms:
        like = f"%{t}%"
        stmt = stmt.where(or_(Invoice.search_text.ilike(like), Invoice.invoice_number.ilike(like),
                              Invoice.supplier_name.ilike(like), Invoice.customer_name.ilike(like)))
    stmt = stmt.order_by(Invoice.invoice_date.desc().nullslast(), Invoice.created_at.desc()).limit(limit)
    return [(inv, 1.0, None) for inv in db.scalars(stmt).all()]


def semantic_search(db: Session, q: str, filters: dict, limit: int = 20) -> list[tuple[Invoice, float, str | None]]:
    qv = embed([q])[0]
    if settings.is_postgres:
        dist = Embedding.vector.cosine_distance(qv)
        rows = db.execute(select(Embedding, dist.label("d")).order_by(dist).limit(limit * 8)).all()
        scored = [(e, 1 - float(d)) for e, d in rows]
    else:
        scored = [(e, cosine(qv, e.vector)) for e in db.scalars(select(Embedding)).all()]
        scored.sort(key=lambda x: x[1], reverse=True)
        scored = scored[: limit * 8]

    best: dict[str, tuple[float, str | None]] = {}
    for e, score in scored:
        inv_ids = [e.invoice_id] if e.invoice_id else [
            i for i in db.scalars(select(Invoice.id).where(Invoice.document_id == e.document_id)).all()
        ]
        snippet = None if e.invoice_id else e.text[:240]
        for iid in inv_ids:
            if iid not in best or score > best[iid][0]:
                best[iid] = (score, snippet or best.get(iid, (0, None))[1])
    floor = settings.semantic_min_score
    best = {k: v for k, v in best.items() if v[0] >= floor}
    if not best:
        return []
    stmt = apply_filters(select(Invoice).join(Document).where(Invoice.id.in_(best)), filters)
    invs = db.scalars(stmt).all()
    out = [(inv, round(best[inv.id][0], 4), best[inv.id][1]) for inv in invs]
    out.sort(key=lambda x: x[1], reverse=True)
    return out[:limit]


def hybrid_search(db: Session, q: str, filters: dict, limit: int = 20):
    """Keyword hits rank first (exact terms are what AP staff usually type), then
    semantic neighbours above the similarity floor. Score = semantic similarity
    + 1.0 for a keyword hit, so it stays interpretable."""
    kw = keyword_search(db, q, filters, limit * 2)
    sem = semantic_search(db, q, filters, limit * 2)
    sem_score = {inv.id: (s, snip) for inv, s, snip in sem}
    out: dict[str, tuple[Invoice, float, str | None, list[str]]] = {}
    for inv, _, _ in kw:
        s, snip = sem_score.get(inv.id, (0.0, None))
        out[inv.id] = (inv, 1.0 + s, snip, ["keyword"] + (["semantic"] if inv.id in sem_score else []))
    for inv, s, snip in sem:
        out.setdefault(inv.id, (inv, s, snip, ["semantic"]))
    ranked = sorted(out.values(), key=lambda x: x[1], reverse=True)[:limit]
    return [(inv, round(score, 4), snip, how) for inv, score, snip, how in ranked]
