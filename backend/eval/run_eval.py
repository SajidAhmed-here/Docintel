"""Run the full system over the labelled dataset and report measured metrics.

    python -m eval.run_eval                     # accuracy dataset
    python -m eval.run_eval --perf              # + 1000-page and 40-page scanned benchmarks

Every file goes through the real REST API (upload -> background processing ->
results), in a fresh database, so timings include detection, OCR, extraction,
validation and indexing. Results: eval/results/results.json and
docs/evaluation-report.md.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="docintel-eval-"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TMP}/eval.db")
os.environ.setdefault("STORAGE_DIR", str(TMP / "uploads"))
os.environ["TASK_MODE"] = "sync"
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.extraction.normalize import norm_key  # noqa: E402
from app.main import app  # noqa: E402

FIELDS = ["invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax", "total", "currency"]
MONEY = {"subtotal", "tax", "total"}


def same(field: str, exp, got) -> bool:
    if exp is None or got is None:
        return exp is None and got is None
    if field in MONEY:
        return abs(Decimal(str(exp)) - Decimal(str(got))) <= Decimal("0.01")
    if field in ("supplier_name", "customer_name", "invoice_number"):
        return norm_key(str(exp)) == norm_key(str(got))
    return str(exp).strip().upper() == str(got).strip().upper()


def match_invoices(expected: list[dict], predicted: list[dict]) -> list[tuple[dict, dict | None]]:
    by_no = {}
    for p in predicted:
        by_no.setdefault(norm_key(p.get("invoice_number")), []).append(p)
    used, pairs = set(), []
    for e in expected:
        cands = [p for p in by_no.get(norm_key(e["invoice_number"]), []) if id(p) not in used]
        if not cands:  # fall back to best field overlap
            cands = sorted((p for p in predicted if id(p) not in used),
                           key=lambda p: -sum(same(f, e[f], p.get(f)) for f in FIELDS))[:1]
            if cands and sum(same(f, e[f], cands[0].get(f)) for f in FIELDS) < 3:
                cands = []
        p = cands[0] if cands else None
        if p is not None:
            used.add(id(p))
        pairs.append((e, p))
    return pairs


def line_item_scores(exp_items: list[dict], got_items: list[dict]) -> tuple[int, int, int]:
    """(true positives, expected count, predicted count) on (description, amount)."""
    remaining = list(got_items)
    tp = 0
    for e in exp_items:
        for g in remaining:
            if norm_key(e["description"]) == norm_key(g.get("description")) and g.get("amount") is not None \
                    and abs(Decimal(e["amount"]) - Decimal(g["amount"])) <= Decimal("0.01"):
                tp += 1
                remaining.remove(g)
                break
    return tp, len(exp_items), len(got_items)


def run_file(client: TestClient, path: Path) -> tuple[dict, list[dict], float]:
    t0 = time.perf_counter()
    with open(path, "rb") as f:
        r = client.post("/api/documents", files=[("files", (path.name, f, "application/octet-stream"))])
    elapsed = time.perf_counter() - t0
    res = r.json()[0]
    if not res["accepted"]:
        return {"status": "rejected", "error_code": "rejected_at_upload", "error_message": res["error"], "page_count": 0,
                "ocr_pages": 0, "processing_ms": int(elapsed * 1000)}, [], elapsed
    doc = client.get(f"/api/documents/{res['document_id']}").json()
    invs = [client.get(f"/api/invoices/{i['id']}").json() for i in doc["invoices"]]
    return doc, invs, elapsed


def evaluate(client: TestClient, dataset: Path) -> dict:
    labels = json.loads((dataset / "labels.json").read_text())["files"]
    per_file = []
    field_hits = defaultdict(lambda: [0, 0])
    field_hits_by_cat = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    hallucinated = 0
    li_tp = li_exp = li_pred = 0
    inv_found = inv_expected = inv_predicted = 0
    for lab in labels:
        path = dataset / lab["file"]
        doc, invs, elapsed = run_file(client, path)
        failed = doc["status"] in ("failed", "rejected")
        rec = {"file": lab["file"], "category": lab["category"], "expect": lab["expect"], "status": doc["status"],
               "error_code": doc.get("error_code"), "seconds": round(elapsed, 3), "pages": doc.get("page_count") or 0,
               "ocr_pages": doc.get("ocr_pages") or 0, "invoices_expected": len(lab["invoices"]), "invoices_found": len(invs)}
        if lab["expect"] == "failure":
            rec["handled_correctly"] = failed
            rec["error_code_matches"] = failed and (doc.get("error_code") == lab["expected_error"] or doc.get("error_code") == "rejected_at_upload")
            per_file.append(rec)
            continue
        rec["processed"] = not failed
        pairs = match_invoices(lab["invoices"], invs)
        inv_expected += len(lab["invoices"])
        inv_predicted += len(invs)
        file_correct = file_total = 0
        for e, p in pairs:
            if p is not None:
                inv_found += 1
            for f in FIELDS:
                ok = p is not None and same(f, e[f], p.get(f))
                field_hits[f][0] += ok
                field_hits[f][1] += 1
                field_hits_by_cat[lab["category"]][f][0] += ok
                field_hits_by_cat[lab["category"]][f][1] += 1
                file_correct += ok
                file_total += 1
                if p is not None and e[f] is None and p.get(f) is not None:
                    hallucinated += 1
            tp, ne, npred = line_item_scores(e["line_items"], (p or {}).get("line_items", []))
            li_tp, li_exp, li_pred = li_tp + tp, li_exp + ne, li_pred + npred
        codes = sorted({i["code"] for p in invs for i in p.get("issues", [])})
        review = any(p.get("needs_review") for p in invs)
        rec.update(field_accuracy=round(file_correct / file_total, 4) if file_total else None,
                   expected_issues=lab["expect_issues"], actual_issues=codes,
                   issues_detected=all(c in codes for c in lab["expect_issues"]),
                   expect_review=lab["expect_review"], needs_review=review,
                   methods=sorted({p.get("extraction_method") for p in invs}))
        per_file.append(rec)

    ok_files = [r for r in per_file if r["expect"] == "success"]
    bad_files = [r for r in per_file if r["expect"] == "failure"]
    total_fields = sum(v[1] for v in field_hits.values())
    correct_fields = sum(v[0] for v in field_hits.values())
    with_errors = [r for r in ok_files if r["expected_issues"]]
    review_tp = sum(1 for r in ok_files if r["expect_review"] and r["needs_review"])
    review_fp = sum(1 for r in ok_files if not r["expect_review"] and r["needs_review"])
    review_fn = sum(1 for r in ok_files if r["expect_review"] and not r["needs_review"])
    clean = sum(1 for r in ok_files if not r["expect_review"])

    by_cat = defaultdict(list)
    for r in per_file:
        by_cat[r["category"]].append(r)
    cat_rows = []
    for cat, rs in by_cat.items():
        fh = field_hits_by_cat.get(cat, {})
        tf, cf = sum(v[1] for v in fh.values()), sum(v[0] for v in fh.values())
        pages = sum(r["pages"] for r in rs)
        secs = sum(r["seconds"] for r in rs)
        cat_rows.append({"category": cat, "files": len(rs), "field_accuracy": round(cf / tf, 4) if tf else None,
                         "success": sum(1 for r in rs if r.get("processed") or r.get("handled_correctly")),
                         "median_seconds_per_file": round(statistics.median(r["seconds"] for r in rs), 3),
                         "seconds_per_page": round(secs / pages, 3) if pages and cat != "corrupted" else None})
    return {
        "files": len(per_file),
        "valid_files": len(ok_files),
        "invalid_files": len(bad_files),
        "document_success_rate": round(sum(r["processed"] for r in ok_files) / len(ok_files), 4),
        "document_failure_rate": round(1 - sum(r["processed"] for r in ok_files) / len(ok_files), 4),
        "invalid_files_rejected": round(sum(r["handled_correctly"] for r in bad_files) / len(bad_files), 4) if bad_files else None,
        "invalid_files_correct_error_code": round(sum(r["error_code_matches"] for r in bad_files) / len(bad_files), 4) if bad_files else None,
        "field_accuracy_overall": round(correct_fields / total_fields, 4),
        "field_accuracy": {f: round(v[0] / v[1], 4) for f, v in field_hits.items()},
        "invoice_recall": round(inv_found / inv_expected, 4),
        "invoice_precision": round(inv_found / inv_predicted, 4) if inv_predicted else None,
        "line_items": {"precision": round(li_tp / li_pred, 4) if li_pred else None, "recall": round(li_tp / li_exp, 4) if li_exp else None},
        "hallucinated_values": hallucinated,
        "validation": {
            "files_with_injected_errors": len(with_errors),
            "injected_errors_detected": sum(r["issues_detected"] for r in with_errors),
            "review_flag_recall": round(review_tp / (review_tp + review_fn), 4) if (review_tp + review_fn) else None,
            "clean_files_flagged_for_review": f"{review_fp}/{clean}",
        },
        "by_category": sorted(cat_rows, key=lambda r: r["category"]),
        "total_seconds": round(sum(r["seconds"] for r in per_file), 2),
        "per_file": per_file,
    }


def perf(client: TestClient, perf_dir: Path) -> list[dict]:
    labels = json.loads((perf_dir / "perf_labels.json").read_text())
    out = []
    for name, exp in labels.items():
        doc, invs, elapsed = run_file(client, perf_dir / name)
        pairs = match_invoices(exp, invs)
        tf = len(pairs) * len(FIELDS)
        cf = sum(same(f, e[f], (p or {}).get(f)) for e, p in pairs for f in FIELDS)
        out.append({"file": name, "status": doc["status"], "pages": doc["page_count"], "ocr_pages": doc["ocr_pages"],
                    "seconds": round(elapsed, 2), "pages_per_second": round(doc["page_count"] / elapsed, 2),
                    "invoices_expected": len(exp), "invoices_found": len(invs),
                    "invoice_recall": round(sum(p is not None for _, p in pairs) / len(exp), 4),
                    "field_accuracy": round(cf / tf, 4)})
    return out


def pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def write_report(res: dict, perf_rows: list[dict] | None, path: Path) -> None:
    llm = f"{settings.llm_provider} / {settings.llm_endpoint[1]}" if settings.llm_enabled else "none (rule-based extractor only)"
    L = [
        "# Evaluation report", "",
        f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} by `python -m eval.run_eval`. All numbers below are measured, not estimated._", "",
        "## Setup", "",
        f"- Machine: {platform.machine()}, {os.cpu_count()} CPU cores, Python {platform.python_version()}",
        f"- Database: {'PostgreSQL' if settings.is_postgres else 'SQLite'}; tasks run synchronously through the REST API",
        f"- LLM: **{llm}**", f"- OCR: Tesseract ({settings.ocr_lang}), {settings.ocr_dpi} DPI render for scanned PDF pages",
        f"- Dataset: {res['files']} labelled files ({res['valid_files']} valid, {res['invalid_files']} corrupted/invalid), generated by `eval/generate_dataset.py` (seed 7)", "",
        "## Headline metrics", "",
        "| Metric | Result |", "|---|---|",
        f"| Field-level extraction accuracy (8 fields) | **{pct(res['field_accuracy_overall'])}** |",
        f"| Invoice detection recall / precision | {pct(res['invoice_recall'])} / {pct(res['invoice_precision'])} |",
        f"| Line items precision / recall | {pct(res['line_items']['precision'])} / {pct(res['line_items']['recall'])} |",
        f"| Document success rate (valid files) | **{pct(res['document_success_rate'])}** |",
        f"| Document failure rate (valid files) | {pct(res['document_failure_rate'])} |",
        f"| Corrupted/invalid files rejected cleanly | {pct(res['invalid_files_rejected'])} (correct error code: {pct(res['invalid_files_correct_error_code'])}) |",
        f"| Values invented where the document had none | {res['hallucinated_values']} |",
        f"| Injected errors caught by validation | {res['validation']['injected_errors_detected']}/{res['validation']['files_with_injected_errors']} files |",
        f"| Review-flag recall (files that should be reviewed) | {pct(res['validation']['review_flag_recall'])} |",
        f"| Clean files flagged for review anyway | {res['validation']['clean_files_flagged_for_review']} |",
        f"| Total processing time, all files | {res['total_seconds']} s |", "",
        "## Accuracy by field", "", "| Field | Accuracy |", "|---|---|",
        *[f"| {f} | {pct(v)} |" for f, v in res["field_accuracy"].items()], "",
        "## By document type", "", "| Category | Files | Field accuracy | Processed OK | Median s/file | s/page |", "|---|---|---|---|---|---|",
        *[f"| {r['category']} | {r['files']} | {pct(r['field_accuracy'])} | {r['success']}/{r['files']} | {r['median_seconds_per_file']} | {r['seconds_per_page'] or '—'} |" for r in res["by_category"]], "",
    ]
    if perf_rows:
        L += ["## Large-document benchmarks", "", "| File | Pages | OCR pages | Time | Pages/s | Invoices found | Field accuracy |", "|---|---|---|---|---|---|---|",
              *[f"| {r['file']} | {r['pages']} | {r['ocr_pages']} | {r['seconds']} s | {r['pages_per_second']} | {r['invoices_found']}/{r['invoices_expected']} | {pct(r['field_accuracy'])} |" for r in perf_rows], ""]
    misses = [r for r in res["per_file"] if r["expect"] == "success" and (r.get("field_accuracy") or 0) < 1]
    miss_rows = [f"| {r['file']} | {pct(r['field_accuracy'])} | {', '.join(r['actual_issues']) or '—'} |" for r in misses] or ["| — | — | — |"]
    L += ["## Files with extraction errors", "", "| File | Field accuracy | Issues raised |", "|---|---|---|",
          *miss_rows, "",
          "## Method notes", "",
          "- A field counts as correct only if it exactly matches the label (names compared case/punctuation-insensitively and ignoring Ltd/Inc; money within 0.01). An expected-empty field is correct only if the system also leaves it empty.",
          "- Invoices are matched to labels by invoice number, falling back to best field overlap.",
          "- \"Values invented\" counts fields the label says are absent but the system filled in.",
          "- The dataset is synthetic (see generator docstring). Real-world accuracy on unseen supplier layouts will be lower; rerun with your own labelled files by adding them to `labels.json`.",
          "- **Tuning disclosure:** the rule-based extractor and OCR post-processing were refined while looking at failures on the seed-7 development set (`evaluation-report.md`). `evaluation-heldout.md` is a fresh seed-99 set generated after tuning, never inspected during development: treat it as the fairer number. Both sets share the same three layout families.",
          "- Timings were measured on the machine above with one worker and no LLM calls; with an LLM, add the provider's latency per invoice (typically 0.5–3 s on Groq). OCR throughput scales roughly linearly with CPU cores/workers.", ""]
    path.write_text("\n".join(L))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(ROOT / "eval" / "dataset"))
    ap.add_argument("--perf", action="store_true")
    ap.add_argument("--report", default=str(ROOT.parent / "docs" / "evaluation-report.md"))
    ap.add_argument("--results", default=str(ROOT / "eval" / "results" / "results.json"))
    a = ap.parse_args()
    with TestClient(app) as client:
        res = evaluate(client, Path(a.dataset))
        perf_rows = perf(client, Path(a.dataset).parent / "perf") if a.perf else None
    Path(a.results).parent.mkdir(parents=True, exist_ok=True)
    Path(a.results).write_text(json.dumps({"accuracy": res, "perf": perf_rows}, indent=2, default=str))
    write_report(res, perf_rows, Path(a.report))
    summary = {k: v for k, v in res.items() if k not in ("per_file", "by_category")}
    print(json.dumps(summary, indent=2))
    if perf_rows:
        print(json.dumps(perf_rows, indent=2))
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
