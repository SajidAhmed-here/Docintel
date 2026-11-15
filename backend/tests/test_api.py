"""Task 4/5: REST API, background processing, search, corrections, reprocessing, reconciliation, failures."""
import io

import pytest

from app import processor
from app.config import settings
from eval import generate_dataset as gen


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_upload_multiple_and_results(client, api_upload, invoice_pdf, invoice_png):
    pdf, inv = invoice_pdf()
    png, inv2 = invoice_png()
    res = api_upload(("a.pdf", pdf), ("b.png", png), ("evil.exe", b"MZ"))
    assert [r["accepted"] for r in res] == [True, True, False]
    assert "Unsupported" in res[2]["error"]
    doc = client.get(f"/api/documents/{res[0]['document_id']}").json()
    assert doc["status"] == "completed" and doc["progress"] == 100 and doc["invoice_count"] == 1
    assert [e["stage"] for e in doc["events"]] == ["detect", "text", "extract", "validate", "index"]
    detail = client.get(f"/api/invoices/{doc['invoices'][0]['id']}").json()
    assert detail["invoice_number"] == inv.invoice_number and detail["total"] == f"{inv.total:.2f}"
    assert len(detail["line_items"]) == len(inv.lines)
    st = client.get(f"/api/documents/{res[1]['document_id']}/status").json()
    assert st["status"] in ("completed", "needs_review")
    batch = client.get("/api/documents/status", params={"ids": ",".join(r["document_id"] for r in res[:2])}).json()
    assert len(batch) == 2


def test_list_and_page_endpoints(client, api_upload, invoice_pdf):
    pdf, inv = invoice_pdf()
    doc_id = api_upload(("x.pdf", pdf))[0]["document_id"]
    lst = client.get("/api/documents", params={"q": "x.pdf"}).json()
    assert lst["total"] == 1 and lst["items"][0]["id"] == doc_id
    page = client.get(f"/api/documents/{doc_id}/pages/1").json()
    assert inv.invoice_number in page["text"]
    img = client.get(f"/api/documents/{doc_id}/pages/1/image")
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
    assert client.get(f"/api/documents/{doc_id}/file").content == pdf
    assert client.get("/api/documents/nope").status_code == 404


def test_invalid_files_fail_with_codes(client, api_upload, invoice_pdf):
    pdf, _ = invoice_pdf()
    res = api_upload(("t.pdf", pdf[: len(pdf) // 3]), ("blank.pdf", gen._blank_pdf()), ("x.png", pdf), ("e.csv", b""))
    assert res[2]["accepted"] is False and "content looks like PDF" in res[2]["error"]
    assert res[3]["accepted"] is False
    for r in res[:2]:
        d = client.get(f"/api/documents/{r['document_id']}").json()
        assert d["status"] == "failed" and d["error_code"] in ("corrupted_pdf", "no_text")


def test_spreadsheet_upload_creates_many_invoices(client, api_upload, make_invoice, tmp_path):
    invs = [make_invoice() for _ in range(4)]
    p = tmp_path / "l.xlsx"
    gen.write_xlsx_ledger(p, invs)
    doc_id = api_upload(("ledger.xlsx", p.read_bytes()))[0]["document_id"]
    doc = client.get(f"/api/documents/{doc_id}").json()
    assert doc["invoice_count"] == 4
    assert {i["invoice_number"] for i in doc["invoices"]} == {i.invoice_number for i in invs}
    assert all(i["extraction_method"] == "spreadsheet" for i in doc["invoices"])


def test_validation_flags_and_review_queue(client, api_upload, invoice_pdf):
    bad, _ = invoice_pdf(error="total_mismatch")
    good, _ = invoice_pdf(layout=1)
    api_upload(("bad.pdf", bad), ("good.pdf", good))
    queue = client.get("/api/invoices", params={"needs_review": True}).json()
    assert queue["total"] == 1
    inv = client.get(f"/api/invoices/{queue['items'][0]['id']}").json()
    assert "TOTAL_MISMATCH" in {i["code"] for i in inv["issues"]}


def test_duplicate_upload_detected(client, api_upload, invoice_pdf):
    pdf, _ = invoice_pdf()
    first = api_upload(("a.pdf", pdf))[0]
    second = api_upload(("a-again.pdf", pdf))[0]
    assert second["duplicate_of"] == first["document_id"]
    doc = client.get(f"/api/documents/{second['document_id']}").json()
    inv = client.get(f"/api/invoices/{doc['invoices'][0]['id']}").json()
    assert "DUPLICATE_INVOICE" in {i["code"] for i in inv["issues"]} and inv["needs_review"]


def test_manual_correction_is_audited_and_revalidated(client, api_upload, invoice_pdf):
    bad, inv_truth = invoice_pdf(error="total_mismatch")
    doc_id = api_upload(("bad.pdf", bad))[0]["document_id"]
    inv_id = client.get(f"/api/documents/{doc_id}").json()["invoices"][0]["id"]
    r = client.patch(f"/api/invoices/{inv_id}", json={"fields": {"total": f"{inv_truth.total}"}, "corrected_by": "max"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "corrected" and not body["needs_review"]
    assert body["corrections"][0]["field"] == "total" and body["corrections"][0]["corrected_by"] == "max"
    assert body["field_confidence"]["total"] == 1.0
    assert client.get(f"/api/documents/{doc_id}").json()["status"] == "completed"
    assert client.patch(f"/api/invoices/{inv_id}", json={"fields": {"total": "abc"}}).status_code == 422
    assert client.patch(f"/api/invoices/{inv_id}", json={"fields": {"hack": 1}}).status_code == 422


def test_approve_and_line_item_edit(client, api_upload, invoice_pdf):
    pdf, _ = invoice_pdf(error="line_math")
    doc_id = api_upload(("lm.pdf", pdf))[0]["document_id"]
    inv_id = client.get(f"/api/documents/{doc_id}").json()["invoices"][0]["id"]
    r = client.patch(f"/api/invoices/{inv_id}", json={"line_items": [{"description": "Fixed", "quantity": 1, "unit_price": 10, "amount": 10}], "approve": True}).json()
    assert r["status"] == "approved" and not r["needs_review"] and r["line_items"][0]["description"] == "Fixed"
    assert {c["field"] for c in r["corrections"]} == {"line_items", "status"}


def test_search_modes_and_filters(client, api_upload, make_invoice, rng):
    a = make_invoice(cur=gen.CURRENCIES[0])
    b = make_invoice(cur=gen.CURRENCIES[1])
    a.supplier_name, b.supplier_name = "Zephyr Plumbing Ltd", "Quasar Robotics Inc"
    api_upload(("a.pdf", gen.pdf_bytes([a], rng, [0])), ("b.pdf", gen.pdf_bytes([b], rng, [0])))
    kw = client.get("/api/search", params={"q": "zephyr", "mode": "keyword"}).json()
    assert [h["invoice"]["supplier_name"] for h in kw] == ["Zephyr Plumbing Ltd"]
    sem = client.get("/api/search", params={"q": "robotic", "mode": "semantic"}).json()
    assert sem[0]["invoice"]["supplier_name"] == "Quasar Robotics Inc"
    hyb = client.get("/api/search", params={"q": "plumbing invoice"}).json()
    assert hyb[0]["invoice"]["supplier_name"] == "Zephyr Plumbing Ltd"
    usd = client.get("/api/search", params={"q": "", "currency": "USD"}).json()
    assert [h["invoice"]["currency"] for h in usd] == ["USD"]
    big = client.get("/api/invoices", params={"min_total": float(max(a.total, b.total))}).json()
    assert big["total"] == 1


def test_reprocess_and_reprocess_failed(client, api_upload, invoice_pdf, monkeypatch):
    pdf, _ = invoice_pdf()
    real = processor.extract_document_text

    def flaky(*a, **k):
        raise RuntimeError("worker crashed")
    monkeypatch.setattr(processor, "extract_document_text", flaky)
    doc_id = api_upload(("f.pdf", pdf))[0]["document_id"]
    d = client.get(f"/api/documents/{doc_id}").json()
    assert d["status"] == "failed" and d["error_code"] == "internal_error"
    monkeypatch.setattr(processor, "extract_document_text", real)
    r = client.post("/api/jobs/reprocess-failed").json()
    assert r["requeued"] == 1
    d = client.get(f"/api/documents/{doc_id}").json()
    assert d["status"] == "completed" and d["attempts"] == 2
    assert client.post(f"/api/documents/{doc_id}/reprocess").status_code == 202
    assert client.get(f"/api/documents/{doc_id}").json()["attempts"] == 3


def test_llm_outage_falls_back(client, api_upload, invoice_pdf, monkeypatch):
    from app.extraction import llm
    monkeypatch.setattr(settings, "llm_api_key", "k")

    def down(text, client=None):
        raise llm.LLMError("HTTP 503 upstream")
    monkeypatch.setattr("app.extraction.service.chat_extract", down)
    pdf, inv = invoice_pdf()
    doc_id = api_upload(("x.pdf", pdf))[0]["document_id"]
    i = client.get(f"/api/invoices/{client.get(f'/api/documents/{doc_id}').json()['invoices'][0]['id']}").json()
    assert i["extraction_method"] == "rules_fallback" and i["invoice_number"] == inv.invoice_number
    assert "LLM_FALLBACK" in {x["code"] for x in i["issues"]}


def test_reconcile_pdf_vs_excel(client, api_upload, make_invoice, rng, tmp_path):
    from decimal import Decimal
    invs = [make_invoice() for _ in range(3)]
    pdf_id = api_upload(("batch.pdf", gen.pdf_bytes(invs, rng, [0, 1, 2])))[0]["document_id"]
    ledger = [gen.Inv(**{**i.__dict__}) for i in invs[:2]]
    ledger[1].printed_total = ledger[1].total + Decimal("50")
    p = tmp_path / "ledger.xlsx"
    gen.write_xlsx_ledger(p, ledger)
    xls_id = api_upload(("ledger.xlsx", p.read_bytes()))[0]["document_id"]
    r = client.post("/api/reconcile", json={"document_a": pdf_id, "document_b": xls_id}).json()
    assert r["summary"] == {"matched": 1, "mismatched": 1, "only_in_a": 1, "only_in_b": 0}
    mism = [p for p in r["pairs"] if p["status"] == "mismatch"][0]
    assert {d["field"] for d in mism["differences"]} == {"total"}
    assert client.post("/api/reconcile", json={"document_a": pdf_id, "document_b": pdf_id}).status_code == 422


def test_stats_export_delete(client, api_upload, invoice_pdf):
    pdf, _ = invoice_pdf()
    doc_id = api_upload(("s.pdf", pdf))[0]["document_id"]
    s = client.get("/api/stats").json()
    assert s["documents"] == 1 and s["invoices"] == 1 and s["success_rate"] == 1.0
    csv = client.get("/api/invoices/export.csv").text
    assert csv.startswith("invoice_id,document,invoice_number") and csv.count("\n") == 2
    assert client.delete(f"/api/documents/{doc_id}").status_code == 204
    assert client.get("/api/stats").json()["documents"] == 0


def test_api_key_and_limits(client, invoice_pdf, monkeypatch):
    monkeypatch.setattr(settings, "api_key", "s3cret")
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/documents", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.get("/api/health").status_code == 200  # health stays open for load balancers
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "max_files_per_request", 2)
    pdf, _ = invoice_pdf()
    r = client.post("/api/documents", files=[("files", (f"{i}.pdf", pdf, "application/pdf")) for i in range(3)])
    assert r.status_code == 413
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    r = client.post("/api/documents", files=[("files", ("big.pdf", pdf, "application/pdf"))]).json()
    assert r[0]["accepted"] is False and "exceeds" in r[0]["error"]


def test_security_headers_and_filename_sanitising(client, api_upload, invoice_pdf):
    pdf, _ = invoice_pdf()
    res = api_upload(("../../etc/passwd.pdf", pdf))
    assert res[0]["filename"] == "passwd.pdf"
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"


def test_document_without_invoice_is_reviewable(client, api_upload):
    """A non-invoice file is flagged for review, surfaces in the queue data, and can be confirmed."""
    csv = b"word,meaning\nubiquitous,found everywhere\nephemeral,lasting a short time\n"
    doc_id = api_upload(("vocab.csv", csv))[0]["document_id"]
    doc = client.get(f"/api/documents/{doc_id}").json()
    assert doc["status"] == "needs_review" and doc["invoice_count"] == 0
    assert client.get("/api/stats").json()["documents_without_invoices"] == 1
    empty = client.get("/api/documents", params={"status": "needs_review", "has_invoices": "false"}).json()
    assert [d["id"] for d in empty["items"]] == [doc_id]
    assert client.get("/api/documents", params={"has_invoices": "true"}).json()["total"] == 0

    r = client.post(f"/api/documents/{doc_id}/confirm-no-invoice")
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert client.get("/api/stats").json()["documents_without_invoices"] == 0
    events = client.get(f"/api/documents/{doc_id}").json()["events"]
    assert events[-1]["stage"] == "review"


def test_confirm_no_invoice_rejected_when_invoices_exist(client, api_upload, invoice_pdf):
    pdf, _ = invoice_pdf()
    doc_id = api_upload(("a.pdf", pdf))[0]["document_id"]
    assert client.post(f"/api/documents/{doc_id}/confirm-no-invoice").status_code == 409
