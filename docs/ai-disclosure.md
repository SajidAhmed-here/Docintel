# AI prompts and AI-assisted development disclosure

## 1. AI used inside the product

The only AI model the running system calls is the **invoice extraction LLM** (default: Groq, `llama-3.3-70b-versatile`; any OpenAI-compatible endpoint can be configured). The full prompts are in [`backend/app/extraction/prompts.py`](../backend/app/extraction/prompts.py) and reproduced below.

**System prompt**

```text
You are a meticulous accounts-payable data extraction engine.
You read the text of ONE business document (invoice, receipt or bill), which may come from OCR and contain errors.

Return ONLY a JSON object with exactly these keys:
{ "invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax", "total",
  "currency", "line_items": [{"description","quantity","unit_price","amount"}], "confidence": {field: 0..1} }

Rules — follow strictly:
1. NEVER invent, infer, calculate or guess a value. If a value is not explicitly written in the text, use null.
   Do not compute subtotal or tax yourself even if you could.
2. Copy values exactly as they appear. Numbers: plain decimals, no thousands separators or symbols.
3. If the text contains several invoices, extract only the FIRST one.
4. Do not treat a PO number, order number, account number or VAT registration number as the invoice number.
5. If the text is not an invoice at all, return every field as null and line_items as [].
6. confidence reflects how clearly the value is stated.
7. Output JSON only.
```

**User message**: the document text between `<<<DOCUMENT` / `DOCUMENT>>>` markers, clipped to head + tail if long.

**Settings**: `temperature=0`, `response_format={"type":"json_object"}`.

**Safeguards around the model** (so the "never invent values" rule doesn't rely on the prompt alone):
- Every returned value is checked against the source text (`app/extraction/grounding.py`). Anything that cannot be found is removed and logged as `UNGROUNDED_VALUE` for the reviewer.
- Validation rules re-check all arithmetic independently of the model.
- If the model is unavailable or rate-limited, a deterministic rule-based extractor is used and the invoice is flagged `LLM_FALLBACK`.

No document content is sent anywhere except the configured LLM endpoint. With no `LLM_API_KEY`, nothing leaves the server.

## 2. AI-assisted development

This codebase was produced with substantial help from an AI coding assistant (**Claude, by Anthropic**), working in an iterative loop: design → implementation → automated tests → measured evaluation → fixes.

What the assistant did:
- Drafted the architecture, backend, processing pipelines, extraction/validation logic, frontend, MCP server, tests, evaluation harness, synthetic dataset generator and documentation.
- Ran the test suite and the evaluation in a sandbox, then fixed the defects they exposed. Examples: invoice-number detection matching the "INV" inside `INV-12345`, OCR merging two columns onto one line, a notes sheet being extracted as an invoice, and a corrected invoice staying in the review queue.

How the work was checked:
- 98 automated tests (see [test-results.txt](test-results.txt)).
- Measured metrics on a labelled dataset and on a held-out dataset generated with a different seed (see [evaluation-report.md](evaluation-report.md)).
- Visual review of every screen in a headless browser.

**Prompts used during development**: the work was driven by the assignment brief (reproduced in the README) plus instructions to use Python/FastAPI, Groq as the LLM, a professional UI, and an MCP server as a bonus.

> Candidate: edit this section so it accurately describes your own role: what you designed, reviewed, changed or decided yourself, and any other AI tools you used.
