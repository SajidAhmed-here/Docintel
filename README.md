# DocIntel: AI-powered document intelligence for invoices

Upload invoices, receipts, scanned PDFs (1,000+ pages), images and spreadsheets. DocIntel reads them (text layer or OCR), extracts structured invoice data with an LLM, refuses to invent values, validates the arithmetic, flags what needs a human, and makes everything searchable and cross-verifiable through a REST API, a web app and an MCP server.

| | |
|---|---|
| **Backend** | Python 3.12 · FastAPI · Celery + Redis · PostgreSQL 16 + pgvector · SQLAlchemy 2 |
| **Processing** | PDFium (text layer) · Tesseract 5 OCR with OpenCV clean-up · pandas / openpyxl / xlrd |
| **AI** | Google Gemini (free tier, `gemini-flash-lite-latest`) or any OpenAI-compatible API (Groq, OpenAI, OpenRouter, xAI) · bge-small-en-v1.5 embeddings (local CPU) |
| **Frontend** | Next.js 16 · React 19 · TypeScript · Tailwind CSS 4 · SWR |
| **Bonus** | MCP server so AI assistants can query and correct the archive |

### Measured results

Held-out labelled set (58 files, never used for tuning), rule-based extractor, 2-CPU machine. Full detail and method: [held-out report](docs/evaluation-heldout.md) · [development report](docs/evaluation-report.md).

| Metric | Result |
|---|---|
| Field-level extraction accuracy (8 fields) | **99.9%** |
| Invoice detection recall / precision | 100% / 100% |
| Line items precision / recall | 92.9% / 91.8% |
| Document success rate / failure rate (valid files) | **100% / 0%** |
| Corrupted or invalid files rejected with the correct error | 7 / 7 |
| Values invented that weren't in the document | **0** |
| Injected errors (wrong totals, line maths, duplicates) caught | 8 / 8 |
| 1,000-page digital PDF | **3.4 s** (≈300 pages/s), 1,000/1,000 invoices, 100% fields |
| 40-page scanned PDF (OCR) | 105 s (≈2.6 s/page on 2 cores), 40/40 invoices, 99.4% fields |

The data is synthetic; see [limitations](#design-decisions-and-limitations). The LLM path is covered by mocked tests; run the evaluation with a Gemini key to measure it.

---

## Contents
- [What it does](#what-it-does)
- [Quick start](#quick-start)
- [Local development](#local-development-manual)
- [Configuration](#configuration)
- [Tests and evaluation](#tests-and-evaluation)
- [MCP server](#mcp-server-bonus)
- [Project layout](#project-layout)
- [Design decisions and limitations](#design-decisions-and-limitations)
- [Deliverables checklist](#deliverables-checklist)

Further documentation: [architecture](docs/architecture.md) · [database schema](docs/database-schema.md) · [API](docs/api.md) · [evaluation](docs/evaluation-report.md) · [AI disclosure](docs/ai-disclosure.md) · [walkthrough script](docs/walkthrough-script.md)

## What it does

| Assignment task | Implementation |
|---|---|
| **1. Document processing** | Multi-file upload (PDF, JPG, PNG, TIFF, XLSX, XLS, CSV). File type is checked by content, not just extension. Digital PDF pages use the text layer; pages without one are OCR'd at 300 DPI with deskew, denoise, binarisation and a second sparse-text pass. 1,000-page PDFs are processed in parallel 25-page chunks with live progress. Multi-frame TIFFs, multi-sheet workbooks, CSV delimiter/encoding sniffing (`;`, decimal commas, cp1252). Corrupted, encrypted, empty, truncated and mismatched files fail with specific error codes; one bad page never sinks a whole file. |
| **2. AI extraction** | Invoice number, date, supplier, customer, subtotal, tax, total, currency and line items as structured JSON, with per-field confidence. Batch PDFs are split into individual invoices. **No invented values**: the LLM is told to return `null`, and then every value it returns is checked against the source text and removed if absent. A rule-based extractor fills gaps and takes over if the LLM is down or rate-limited. Spreadsheet ledgers are mapped by column headers without an LLM. |
| **3. Validation** | Missing fields, invalid numbers and dates, subtotal + tax ≠ total, qty × price ≠ amount, line items ≠ subtotal, duplicate invoices (same supplier + number across documents; exact duplicate files by SHA-256), unusual tax rates, low confidence. Errors put the invoice in the review queue. |
| **4. Backend API** | Upload, status (single and batch), results, search, manual corrections (validated and audited), single and bulk reprocessing of failed jobs, cross-verification, CSV export, stats. OCR and AI run in Celery workers. See [docs/api.md](docs/api.md) or `/docs`. |
| **5. Database and search** | Postgres schema for documents, pages and extracted text, invoices, line items, validation issues, processing history, corrections and embeddings ([schema](docs/database-schema.md)). Keyword filtering (supplier, currency, dates, amount range, status, file type; trigram index) plus semantic search (pgvector HNSW) and a hybrid mode. |
| **6. Frontend** | Overview dashboard with drag-and-drop upload and live progress; documents list; document view with page images, extracted text and processing timeline; invoice review screen with the original beside editable fields, live arithmetic checks, issue list, line-item editor, change history and approve-and-next; review queue; search; cross-verify. |
| **7. Tests and evaluation** | 98 pytest tests covering every pipeline, extraction (with a mocked LLM), validation, all API endpoints and failure scenarios. A labelled-dataset generator and an evaluation harness that reports measured accuracy, timing, success and failure rates. |
| **Scenario extras** | **PDF↔PDF and PDF↔Excel cross-verification**: match invoices across two documents and list field-level differences and invoices missing from either side. |

## Quick start

No Docker needed. Requirements: Python 3.11/3.12, Node 20+, and Tesseract for OCR
(Windows: `winget install UB-Mannheim.TesseractOCR`, macOS: `brew install tesseract`, Linux: `apt install tesseract-ocr`).

```powershell
cp .env.example .env        # put your free Gemini key in LLM_API_KEY (https://aistudio.google.com/apikey)
.\start.ps1                 # Windows: creates the venv, installs everything, starts API + web app
```

- Web app: http://localhost:3000
- API docs (OpenAPI): http://localhost:8000/docs

Without `LLM_API_KEY` the system runs entirely offline using the rule-based extractor.

## Local development (manual)

```bash
# backend (SQLite + in-process background worker, no Redis needed). Reads ../.env automatically.
cd backend
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# frontend
cd frontend
npm install
npm run dev                                          # http://localhost:3000 (proxies /api to API_URL, default http://localhost:8000)
```

To scale out with Postgres + Redis + Celery, set `DATABASE_URL=postgresql+psycopg://…`, `REDIS_URL=redis://…`, `TASK_MODE=celery`, and start workers with `celery -A app.worker.celery_app worker -Q documents`.

## Configuration

All settings are environment variables (`backend/app/config.py`).

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini`, `groq`, `xai`, `openrouter`, `openai` (all OpenAI-compatible) |
| `LLM_API_KEY` | – | Enables AI extraction. Empty means rule-based only |
| `LLM_MODEL` | provider default | e.g. `gemini-flash-lite-latest` |
| `LLM_REASONING_EFFORT` | `low` | Gemini thinking budget (`low`/`medium`/`high`) |
| `TESSERACT_CMD` | auto-detected | Path to tesseract.exe if not on PATH |
| `LLM_CONCURRENCY` | `2` | Parallel LLM calls per worker (keep low on free tiers) |
| `DATABASE_URL` | SQLite file | `postgresql+psycopg://user:pass@host/db` in production |
| `TASK_MODE` | `inline` | `celery` (production), `inline` (dev), `sync` (tests) |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker |
| `API_KEY` | – | If set, required as `X-API-Key` on every `/api` call |
| `EMBEDDING_BACKEND` | `hash` | `fastembed` = bge-small semantic model (set in .env.example); `hash` = offline lexical vectors |
| `OCR_WORKERS` | CPU count − 1 | Parallel OCR processes per document |
| `OCR_DPI` | `300` | Render resolution for scanned PDF pages |
| `OCR_SPARSE_PASS` | `true` | Second OCR pass to recover isolated header fields |
| `MAX_UPLOAD_MB` / `MAX_FILES_PER_REQUEST` / `UPLOADS_PER_MINUTE` | `250` / `50` / `120` | Upload limits |
| `DATE_ORDER` | `DMY` | Tie-break for ambiguous dates like 03/04/2026 (UK default) |

## Tests and evaluation

```bash
cd backend
pytest -q                                     # unit + API + failure-scenario tests
python -m eval.generate_dataset               # labelled dataset (deterministic, seed 7) + perf files
python -m eval.run_eval --perf                # writes docs/evaluation-report.md + eval/results/results.json
python -m eval.generate_dataset --out eval/heldout/dataset --seed 99
python -m eval.run_eval --dataset eval/heldout/dataset --report ../docs/evaluation-heldout.md \
       --results eval/results/results-heldout.json
```

Run the evaluation with `LLM_API_KEY` set to measure the Gemini path; the report records which extractor was used. Latest test output: [docs/test-results.txt](docs/test-results.txt).

## MCP server (bonus)

`mcp_server/server.py` exposes the archive to AI assistants (Claude Desktop, Claude Code, any MCP client) through 9 tools: search invoices, get invoice, review queue, list/get documents, cross-verify, stats, correct invoice (audited) and reprocess.

```bash
pip install -r mcp_server/requirements.txt
DOCINTEL_API_URL=http://localhost:8000 python mcp_server/server.py          # stdio
python mcp_server/server.py --http --port 8765                              # streamable HTTP
```

Claude Desktop config:

```json
{ "mcpServers": { "docintel": { "command": "python", "args": ["/path/to/docintel/mcp_server/server.py"],
  "env": { "DOCINTEL_API_URL": "http://localhost:8000" } } } }
```

## Project layout

```
backend/
  app/
    main.py              FastAPI app, middleware, routers
    api/                 documents, invoices, search/reconcile/jobs/stats endpoints
    pipeline/            detect.py · pdf.py · ocr.py · images.py · spreadsheets.py · segment.py
    extraction/          llm.py · prompts.py · grounding.py · rules.py · tables.py · normalize.py · service.py
    validation/rules.py  business rules
    search/              embeddings + keyword/semantic/hybrid search
    reconcile.py         cross-verification
    processor.py         end-to-end job (stages, progress, history)
    worker.py            Celery / inline / sync dispatch
    models.py db.py config.py security.py schemas.py
  tests/                 pytest suite
  eval/                  dataset generator + evaluation harness
frontend/                Next.js app (app/ routes, components/, lib/)
mcp_server/              MCP server
docs/                    architecture, schema, API, evaluation, AI disclosure, walkthrough script
start.ps1                one-command local start (Windows)
```

## Design decisions and limitations

Design decisions are explained in [docs/architecture.md](docs/architecture.md). In short: Python end-to-end instead of NestJS + Python (one language for OCR, data and API), Postgres + pgvector instead of a separate vector database, and an LLM wrapped in a grounding check, a rule fallback and independent validation.

Known limitations, stated honestly:
- **The evaluation data is synthetic.** It varies layouts, fonts, currencies, date formats, image quality and errors, but it is not a sample of real supplier invoices. The rule-based extractor was refined against the main dataset; the held-out set (different seed) is the fairer number, though it reuses the same three layout families. Real-world accuracy on unfamiliar layouts depends mainly on the LLM. Measure it by adding real labelled files to `labels.json`.
- **OCR quality bounds accuracy** on poor scans. Misreads such as `11 → 1` in a quantity are caught by the arithmetic checks and sent to review, not silently accepted.
- **Hash embeddings are lexical.** True semantic search (synonyms) needs `EMBEDDING_BACKEND=fastembed` (set in .env.example), which downloads a ~130 MB model on first use.
- **Not built:** user accounts/SSO, per-user permissions, antivirus scanning of uploads, handwriting recognition, non-English OCR language packs (set `OCR_LANG` and install the pack to add them).

## Deliverables checklist

1. Git repository: this repo
2. README and setup: this file
3. Architecture diagram and database schema: [docs/architecture.md](docs/architecture.md), [docs/database-schema.md](docs/database-schema.md)
4. Working backend API and frontend: `backend/`, `frontend/`
5. PDF, image, Excel and CSV pipelines: `backend/app/pipeline/`
6. Automated tests and results: `backend/tests/`, [docs/test-results.txt](docs/test-results.txt)
7. Evaluation report with measured metrics: [docs/evaluation-report.md](docs/evaluation-report.md), [docs/evaluation-heldout.md](docs/evaluation-heldout.md)
8. Recorded walkthrough: record using [docs/walkthrough-script.md](docs/walkthrough-script.md)
9. AI prompts and disclosure: [docs/ai-disclosure.md](docs/ai-disclosure.md)
