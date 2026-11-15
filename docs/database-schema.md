# Database schema

PostgreSQL 16 with the `vector` (pgvector) and `pg_trgm` extensions. Tables are created by SQLAlchemy on startup (`app/models.py`); SQLite is supported for local development.

```mermaid
erDiagram
    documents ||--o{ pages : "has"
    documents ||--o{ invoices : "contains"
    documents ||--o{ processing_events : "history"
    documents ||--o{ embeddings : "indexed as"
    invoices ||--o{ line_items : "has"
    invoices ||--o{ validation_issues : "flagged with"
    invoices ||--o{ corrections : "audit trail"
    invoices ||--o{ embeddings : "summary vector"

    documents {
        varchar32 id PK
        varchar original_filename
        varchar storage_path
        varchar file_type "pdf | image | xlsx | xls | csv"
        int size_bytes
        varchar64 sha256 "indexed - duplicate files"
        varchar status "queued | processing | completed | needs_review | failed"
        varchar stage
        int progress "0-100"
        int page_count
        int ocr_pages
        varchar error_code
        text error_message
        int attempts
        varchar duplicate_of
        int processing_ms
        timestamptz created_at
        timestamptz started_at
        timestamptz finished_at
    }
    pages {
        int id PK
        varchar document_id FK
        int page_number
        varchar sheet_name "spreadsheets"
        varchar method "text | ocr | spreadsheet"
        float ocr_confidence
        text text
    }
    invoices {
        varchar32 id PK
        varchar document_id FK
        int segment_index
        int page_start
        int page_end
        varchar source_ref "e.g. Sheet1 rows 4-9"
        varchar invoice_number "indexed"
        date invoice_date "indexed"
        varchar supplier_name "indexed"
        varchar customer_name
        numeric subtotal "18,4"
        numeric tax
        numeric total "indexed"
        varchar currency "ISO 4217"
        json field_confidence
        varchar extraction_method "llm | rules | rules_fallback | spreadsheet"
        varchar model
        varchar status "ok | needs_review | corrected | approved"
        bool needs_review "indexed - review queue"
        json raw_extraction "LLM output + notes"
        text search_text "trigram index"
    }
    line_items {
        int id PK
        varchar invoice_id FK
        int position
        text description
        numeric quantity
        numeric unit_price
        numeric amount
    }
    validation_issues {
        int id PK
        varchar invoice_id FK
        varchar code "e.g. TOTAL_MISMATCH"
        varchar severity "error | warning"
        varchar field
        text message
        json details
    }
    processing_events {
        int id PK
        varchar document_id FK
        int attempt
        varchar stage "detect | text | extract | validate | index | failed"
        varchar status "ok | warning | failed"
        text message
        int duration_ms
        timestamptz created_at
    }
    corrections {
        int id PK
        varchar invoice_id FK
        varchar field
        text old_value
        text new_value
        varchar corrected_by
        timestamptz created_at
    }
    embeddings {
        int id PK
        varchar document_id FK
        varchar invoice_id FK "null = raw text chunk"
        int chunk_index
        text text
        vector384 vector "HNSW cosine index"
    }
```

## Notes

- **Money** is `NUMERIC(18,4)`, never float, so validation arithmetic is exact.
- **One document → many invoices.** A batch PDF or a ledger spreadsheet yields many `invoices` rows. `page_start/page_end` or `source_ref` point back to where each came from.
- **Indexes** serve the API's hot paths: review queue (`needs_review`), duplicate detection (`supplier_name, invoice_number`), filters (`invoice_date`, `total`, `currency`), keyword search (`gin_trgm_ops` on `search_text`) and semantic search (HNSW on `embeddings.vector`).
- **Deletes cascade**: removing a document removes its pages, invoices, issues, corrections, events and vectors in one transaction.
- **`raw_extraction`** keeps the LLM's untouched JSON and the grounding notes, so reviewers and auditors can see exactly what the model said versus what was kept.

## Validation issue codes

| Code | Severity | Meaning |
|---|---|---|
| `MISSING_FIELD` | error for number/date/supplier/total, warning for others | Field not found in the document |
| `TOTAL_MISMATCH` | error | subtotal + tax ≠ total (beyond rounding) |
| `LINE_ITEM_MATH` | error | quantity × unit price ≠ line amount |
| `LINE_ITEMS_SUM_MISMATCH` | error | Line amounts don't add up to subtotal (or total, if tax-inclusive) |
| `SUBTOTAL_EXCEEDS_TOTAL` | error | Subtotal larger than total |
| `DUPLICATE_INVOICE` | error | Same supplier + invoice number already in another source document |
| `DATE_IN_FUTURE` / `DATE_IMPLAUSIBLE` | error | Date > 30 days ahead or before 2000 |
| `NEGATIVE_AMOUNT`, `ZERO_TOTAL`, `TAX_RATE_UNUSUAL`, `INVALID_CURRENCY`, `TOTAL_UNEXPLAINED`, `NO_LINE_ITEMS` | warning | Suspicious but possibly legitimate |
| `LOW_CONFIDENCE` | warning, triggers review | A field was extracted with confidence < 0.6 |
| `UNGROUNDED_VALUE` | warning, triggers review | The LLM returned a value not present in the text; it was removed |
| `LLM_FALLBACK` | warning | LLM unavailable; rule-based extraction used |
