"""DocIntel MCP server (bonus).

Lets an AI assistant (Claude Desktop, Claude Code, any MCP client) search the
processed invoice archive, inspect validation problems, cross-verify documents
and apply audited corrections, through the same REST API the web app uses.

Run (stdio, for desktop clients):   python server.py
Run (HTTP):                         python server.py --http --port 8765
Config: DOCINTEL_API_URL (default http://localhost:8000), DOCINTEL_API_KEY.
"""
from __future__ import annotations

import argparse
import os
from typing import Any, Literal

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError  # reported to the client as an is_error tool result
from mcp.types import ToolAnnotations
from pydantic import Field

API_URL = os.getenv("DOCINTEL_API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.getenv("DOCINTEL_API_KEY", "")

mcp = MCPServer(
    "docintel_mcp",
    title="DocIntel",
    version="1.0.0",
    instructions=(
        "Tools for a company's processed invoice archive. Start with docintel_search_invoices or "
        "docintel_list_review_queue, then docintel_get_invoice for full detail (line items, validation issues, "
        "edit history). Amounts are decimal strings in the invoice's own currency. Never invent invoice values: "
        "if a field is null, the document did not contain it."
    ),
)

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


async def _call(method: str, path: str, **kw) -> Any:
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    try:
        async with httpx.AsyncClient(base_url=f"{API_URL}/api", timeout=60, headers=headers) as c:
            r = await c.request(method, path, **kw)
    except httpx.TransportError as e:
        raise ToolError(f"Cannot reach the DocIntel API at {API_URL} ({e.__class__.__name__}). Is the backend running? Set DOCINTEL_API_URL.") from e
    if r.status_code == 401:
        raise ToolError("The DocIntel API rejected the API key. Set DOCINTEL_API_KEY to the backend's API_KEY.")
    if r.status_code == 404:
        raise ToolError("Not found. Check the id: use docintel_search_invoices or docintel_list_documents to find valid ids.")
    if r.status_code >= 400:
        detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise ToolError(f"API error {r.status_code}: {detail}")
    return r.json() if r.content else None


def _inv_brief(i: dict) -> dict:
    keep = ("id", "document_id", "invoice_number", "invoice_date", "supplier_name", "customer_name",
            "subtotal", "tax", "total", "currency", "status", "needs_review", "error_count", "warning_count")
    return {k: i.get(k) for k in keep}


@mcp.tool(name="docintel_search_invoices", annotations=READ)
async def search_invoices(
    query: str = Field("", description="Words to search for: invoice number, supplier, customer, line-item text, or a description like 'office furniture'. Empty = filters only."),
    mode: Literal["hybrid", "keyword", "semantic"] = Field("hybrid", description="hybrid = exact keyword hits first then similar invoices; keyword = all words must appear; semantic = similarity only"),
    supplier: str | None = Field(None, description="Supplier name contains this text"),
    currency: str | None = Field(None, description="ISO code, e.g. GBP", min_length=3, max_length=3),
    date_from: str | None = Field(None, description="Invoice date on/after, YYYY-MM-DD"),
    date_to: str | None = Field(None, description="Invoice date on/before, YYYY-MM-DD"),
    min_total: float | None = Field(None, ge=0),
    max_total: float | None = Field(None, ge=0),
    needs_review: bool | None = Field(None, description="true = only invoices flagged for manual review"),
    limit: int = Field(20, ge=1, le=100),
) -> dict:
    """Search processed invoices with keyword/semantic search and filters. Returns compact invoice summaries; call docintel_get_invoice for line items and issues."""
    params = {k: v for k, v in dict(q=query, mode=mode, supplier=supplier, currency=currency, date_from=date_from, date_to=date_to,
                                     min_total=min_total, max_total=max_total, needs_review=needs_review, limit=limit).items() if v not in (None, "")}
    hits = await _call("GET", "/search", params=params)
    return {"count": len(hits), "results": [{**_inv_brief(h["invoice"]), "document": h["document_filename"], "matched_by": h.get("matched_by", []),
                                             "snippet": h.get("snippet")} for h in hits]}


@mcp.tool(name="docintel_get_invoice", annotations=READ)
async def get_invoice(invoice_id: str = Field(..., description="Invoice id from a search or document result")) -> dict:
    """Full detail for one invoice: all fields, per-field extraction confidence, line items, validation issues, extraction notes and the manual edit history."""
    i = await _call("GET", f"/invoices/{invoice_id}")
    return {**_inv_brief(i), "document": i.get("document_filename"), "pages": [i.get("page_start"), i.get("page_end")],
            "extraction_method": i["extraction_method"], "field_confidence": i["field_confidence"], "line_items": i["line_items"],
            "issues": [{k: x[k] for k in ("severity", "code", "field", "message")} for x in i["issues"]],
            "notes": i["notes"], "corrections": i["corrections"]}


@mcp.tool(name="docintel_list_review_queue", annotations=READ)
async def list_review_queue(page: int = Field(1, ge=1), page_size: int = Field(25, ge=1, le=100)) -> dict:
    """Invoices that need a human: failed validation (totals don't add up, duplicates, missing fields) or low-confidence extraction."""
    r = await _call("GET", "/invoices", params={"needs_review": True, "page": page, "page_size": page_size})
    return {"total": r["total"], "page": page, "has_more": page * page_size < r["total"],
            "invoices": [{**_inv_brief(i), "document": i.get("document_filename")} for i in r["items"]]}


@mcp.tool(name="docintel_list_documents", annotations=READ)
async def list_documents(
    status: Literal["queued", "processing", "completed", "needs_review", "failed"] | None = None,
    filename: str | None = Field(None, description="File name contains"),
    page: int = Field(1, ge=1), page_size: int = Field(25, ge=1, le=100),
) -> dict:
    """List uploaded documents with processing status, page counts and how many invoices each produced."""
    r = await _call("GET", "/documents", params={k: v for k, v in dict(status=status, q=filename, page=page, page_size=page_size).items() if v})
    keep = ("id", "original_filename", "file_type", "status", "progress", "page_count", "ocr_pages", "invoice_count", "review_count", "error_code", "error_message", "created_at")
    return {"total": r["total"], "page": page, "has_more": page * page_size < r["total"], "documents": [{k: d[k] for k in keep} for d in r["items"]]}


@mcp.tool(name="docintel_get_document", annotations=READ)
async def get_document(document_id: str, include_text_of_page: int | None = Field(None, ge=1, description="Also return the extracted text of this page/sheet number")) -> dict:
    """One document's status, invoices found in it, and processing history. Optionally the extracted text of a page."""
    d = await _call("GET", f"/documents/{document_id}")
    out = {k: d[k] for k in ("id", "original_filename", "file_type", "status", "page_count", "ocr_pages", "error_code", "error_message", "duplicate_of", "processing_ms")}
    out["invoices"] = [_inv_brief(i) for i in d["invoices"]]
    out["history"] = [{k: e[k] for k in ("stage", "status", "message")} for e in d["events"]]
    if include_text_of_page:
        p = await _call("GET", f"/documents/{document_id}/pages/{include_text_of_page}")
        out["page_text"] = p["text"][:20000]
    return out


@mcp.tool(name="docintel_reconcile_documents", annotations=READ)
async def reconcile_documents(document_a: str = Field(..., description="First document id, e.g. a supplier PDF"),
                              document_b: str = Field(..., description="Second document id, e.g. the AP ledger spreadsheet")) -> dict:
    """Cross-verify two documents (PDF vs PDF, PDF vs Excel/CSV): invoices matched by number, field differences, and invoices present in only one."""
    return await _call("POST", "/reconcile", json={"document_a": document_a, "document_b": document_b})


@mcp.tool(name="docintel_get_stats", annotations=READ)
async def get_stats() -> dict:
    """Archive-wide numbers: documents by status, invoices, review backlog, success rate, totals by currency, most common validation issues."""
    return await _call("GET", "/stats")


@mcp.tool(name="docintel_correct_invoice", annotations=WRITE)
async def correct_invoice(
    invoice_id: str,
    fields: dict[str, str | None] = Field(default_factory=dict, description="Field -> new value. Editable: invoice_number, invoice_date (YYYY-MM-DD), supplier_name, customer_name, subtotal, tax, total, currency. null clears."),
    approve: bool = Field(False, description="Mark reviewed/approved even if warnings remain"),
    corrected_by: str = Field("mcp-assistant", max_length=128),
) -> dict:
    """Apply manual corrections (recorded in the audit trail) and re-run validation. Only correct values you have verified against the document text."""
    i = await _call("PATCH", f"/invoices/{invoice_id}", json={"fields": fields, "approve": approve, "corrected_by": corrected_by})
    return {**_inv_brief(i), "issues": [x["message"] for x in i["issues"]]}


@mcp.tool(name="docintel_reprocess_document", annotations=WRITE)
async def reprocess_document(document_id: str) -> dict:
    """Re-run OCR, extraction and validation for a document (e.g. after a failure). Processing is asynchronous: poll docintel_get_document."""
    d = await _call("POST", f"/documents/{document_id}/reprocess")
    return {"id": d["id"], "status": d["status"], "attempts": d["attempts"]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    if a.http:
        mcp.run("streamable-http", port=a.port)
    else:
        mcp.run()
