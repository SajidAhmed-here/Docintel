# REST API

Interactive OpenAPI docs: **http://localhost:8000/docs** (generated from the code, always current).
If `API_KEY` is set, send it as the `X-API-Key` header on every `/api` call except `/api/health`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/documents` | Upload 1–50 files (`multipart/form-data`, field `files`). Returns `202` with one result per file (`accepted`, `document_id`, `duplicate_of`, `error`). Processing runs in the background. |
| GET | `/api/documents` | List documents. Filters: `status`, `file_type`, `q` (file name), `page`, `page_size`. |
| GET | `/api/documents/status?ids=a,b,c` | Batch processing status for polling (status, progress %, stage, error). |
| GET | `/api/documents/{id}` | Document detail: invoices found, per-page metadata, full processing history. |
| GET | `/api/documents/{id}/status` | Status of one document. |
| GET | `/api/documents/{id}/pages/{n}` | Extracted text of page/sheet *n* (method, OCR confidence). |
| GET | `/api/documents/{id}/pages/{n}/image` | PNG preview of a PDF/image page. |
| GET | `/api/documents/{id}/file` | Download the original. |
| POST | `/api/documents/{id}/reprocess` | Re-run the pipeline (`409` if already running). |
| DELETE | `/api/documents/{id}` | Delete a document and everything derived from it. |
| POST | `/api/jobs/reprocess-failed` | Re-queue every failed document. |
| GET | `/api/invoices` | List/filter invoices: `needs_review`, `status`, `supplier`, `currency`, `date_from`, `date_to`, `min_total`, `max_total`, `file_type`, paging. `needs_review=true` is the review queue. |
| GET | `/api/invoices/export.csv` | CSV export for import into internal systems. |
| GET | `/api/invoices/{id}` | Full extracted result: fields, confidence, line items, validation issues, notes, correction history. |
| PATCH | `/api/invoices/{id}` | Manual correction: `{"fields": {"total": "120.00"}, "line_items": [...], "approve": false, "corrected_by": "name"}`. Values are validated, every change is audited, and the invoice is re-validated. |
| GET | `/api/search` | `q`, `mode=hybrid\|keyword\|semantic`, plus the invoice filters above. |
| POST | `/api/reconcile` | `{"document_a": id, "document_b": id}`: cross-verify PDF↔PDF or PDF↔Excel/CSV. |
| GET | `/api/stats` | Dashboard metrics. |
| GET | `/api/health` | Liveness and configuration summary. |

## Example

```bash
curl -F files=@invoice.pdf -F files=@ledger.xlsx http://localhost:8000/api/documents
# [{"filename":"invoice.pdf","accepted":true,"document_id":"9f2c…","duplicate_of":null,"error":null}, …]

curl http://localhost:8000/api/documents/9f2c…/status
# {"status":"completed","progress":100,"stage":"done",…}

curl http://localhost:8000/api/invoices/{invoice_id}
```

```json
{
  "invoice_number": "INV-2026-0042",
  "invoice_date": "2026-03-14",
  "supplier_name": "Northwind Supplies Ltd",
  "customer_name": "Contoso Retail plc",
  "subtotal": "165.00", "tax": "33.00", "total": "198.00", "currency": "GBP",
  "line_items": [{"position": 0, "description": "Printer paper A4", "quantity": "10.00", "unit_price": "4.50", "amount": "45.00"}],
  "field_confidence": {"invoice_number": 0.9, "total": 0.85},
  "issues": [],
  "status": "ok", "needs_review": false,
  "extraction_method": "llm", "model": "llama-3.3-70b-versatile"
}
```

Missing values are always `null`, never guessed. Money is returned as decimal strings to avoid float rounding.
