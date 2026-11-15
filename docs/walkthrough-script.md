# Walkthrough recording script (≈8 minutes)

Record the screen at 1440×900 with `start.ps1` running and a Gemini key configured. Before starting, run `python -m eval.generate_dataset` and keep `backend/eval/dataset/` open in a file browser.

| # | Time | Show | Say |
|---|---|---|---|
| 1 | 0:00 | README architecture diagram | Problem, the pipeline in one sentence, why FastAPI + Celery + Postgres/pgvector + Groq. |
| 2 | 0:45 | Overview page, drag in ~10 files: digital PDFs, `scanned_*.pdf`, `photo_*.jpg`, `multipage_scan.tiff`, `batch_25_invoices.pdf`, `ap_ledger.xlsx`, `line_items_export.csv`, `garbage.pdf` | Multi-file upload; files are queued and processed by background workers. Point out live progress bars and stages. |
| 3 | 1:45 | Documents list after processing | Statuses: completed, needs review, failed with a readable reason (`garbage.pdf`). Click **Retry failed**. |
| 4 | 2:15 | `batch_25_invoices.pdf` detail | 32 pages → 25 invoices; T&C pages attached to the right invoice; page preview vs extracted text; processing history timings. |
| 5 | 3:00 | A scanned PDF / phone photo | OCR confidence on the page header; extracted text toggle; fields still correct. |
| 6 | 3:30 | Review queue → `error_total_mismatch_*` invoice | Problem panel ("subtotal + tax ≠ total"), field highlighted in red, confidence dots. Fix the total and watch the live check turn green. Save, then show the change history. Approve, which jumps to the next item. |
| 7 | 4:45 | `digital_01_resent.pdf` | Duplicate file and duplicate invoice detection. |
| 8 | 5:15 | Search: "network switch" (hybrid), filter currency GBP, then semantic "office furniture" | Keyword vs semantic, filters. |
| 9 | 6:00 | Cross-verify: `batch_25_invoices.pdf` vs `ap_ledger.xlsx` | 10 match, 2 differences found (total 1,422 vs 1,472; missing customer), 13 only in the PDF. |
| 10 | 6:45 | `http://localhost:8000/docs` | REST endpoints, including reprocess-failed and export.csv. |
| 11 | 7:15 | Terminal: `pytest -q`, then `docs/evaluation-report.md` | 90 tests; measured accuracy, speed, success/failure rate; held-out results and stated limitations. |
| 12 | 7:45 | (optional) Claude Desktop with the MCP server | "Which invoices from Brightline need review and why?" |
