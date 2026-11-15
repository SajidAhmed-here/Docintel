"use client";

import clsx from "clsx";
import { ArrowLeftRight, CheckCircle2, GitCompareArrows, Loader2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { useToast } from "@/components/toast";
import { Empty, PageHeader, Stat } from "@/components/ui";
import { api, type DocumentOut, type Paged, type ReconcileResult } from "@/lib/api";
import { FIELD_LABELS, FILE_TYPE_LABEL, money } from "@/lib/format";

export default function ReconcilePage() {
  const { data } = useSWR<Paged<DocumentOut>>("/documents?page_size=200");
  const docs = (data?.items ?? []).filter((d) => d.status === "completed" || d.status === "needs_review");
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ReconcileResult | null>(null);
  const toast = useToast();

  async function run() {
    setBusy(true);
    try { setRes(await api<ReconcileResult>("/reconcile", { method: "POST", body: JSON.stringify({ document_a: a, document_b: b }) })); }
    catch (e) { toast({ tone: "bad", title: "Cross-verification failed", body: (e as Error).message }); }
    finally { setBusy(false); }
  }

  const opt = (d: DocumentOut) => <option key={d.id} value={d.id}>{d.original_filename} · {FILE_TYPE_LABEL[d.file_type]} · {d.invoice_count} invoice{d.invoice_count !== 1 && "s"}</option>;

  return (
    <>
      <PageHeader title="Cross-verify" description="Compare two documents invoice by invoice: a supplier PDF against your Excel ledger, or two PDFs against each other." />
      <div className="card p-5">
        <div className="grid items-end gap-4 md:grid-cols-[1fr_auto_1fr_auto]">
          <label><span className="label">Document A</span><select className="input mt-1.5" value={a} onChange={(e) => setA(e.target.value)}><option value="">Choose a document…</option>{docs.map(opt)}</select></label>
          <button className="btn-ghost mb-0.5 h-9 px-2" aria-label="Swap documents" onClick={() => { setA(b); setB(a); }}><ArrowLeftRight className="size-4" /></button>
          <label><span className="label">Document B</span><select className="input mt-1.5" value={b} onChange={(e) => setB(e.target.value)}><option value="">Choose a document…</option>{docs.filter((d) => d.id !== a).map(opt)}</select></label>
          <button className="btn-primary" disabled={!a || !b || a === b || busy} onClick={run}>{busy ? <Loader2 className="size-4 animate-spin" /> : <GitCompareArrows className="size-4" />} Compare</button>
        </div>
        <p className="mt-3 text-[12.5px] text-muted">Invoices are matched on invoice number (supplier as tie-break). Dates, parties, subtotal, tax, total, currency and line-item sums are compared field by field.</p>
      </div>

      {!res ? (
        <div className="card mt-6"><Empty icon={<GitCompareArrows className="size-5" />} title="Pick two processed documents">Results show matched invoices, field-level differences, and invoices that appear in only one document.</Empty></div>
      ) : (
        <div className="mt-6 space-y-6">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label="Match exactly" value={res.summary.matched} tone="ok" />
            <Stat label="Differences" value={res.summary.mismatched} tone={res.summary.mismatched ? "bad" : undefined} />
            <Stat label={`Only in A`} value={res.summary.only_in_a} tone={res.summary.only_in_a ? "warn" : undefined} hint={res.document_a.filename} />
            <Stat label={`Only in B`} value={res.summary.only_in_b} tone={res.summary.only_in_b ? "warn" : undefined} hint={res.document_b.filename} />
          </div>

          <section className="card overflow-hidden">
            <div className="border-b border-line px-5 py-3.5"><h2 className="font-semibold">Matched invoices</h2></div>
            {res.pairs.length === 0 ? <Empty title="No invoice numbers in common" /> : (
              <div className="overflow-x-auto">
                <table className="table">
                  <thead><tr><th>Invoice</th><th>Result</th><th>Field</th><th>{res.document_a.filename}</th><th>{res.document_b.filename}</th></tr></thead>
                  <tbody>
                    {[...res.pairs].sort((x, y) => (x.status === y.status ? 0 : x.status === "mismatch" ? -1 : 1)).map((p) =>
                      p.status === "match" ? (
                        <tr key={p.a_invoice_id}>
                          <td><Link className="num font-medium hover:underline" href={`/invoices/${p.a_invoice_id}`}>{p.invoice_number}</Link></td>
                          <td><span className="inline-flex items-center gap-1.5 text-[12.5px] text-ok"><CheckCircle2 className="size-3.5" /> All fields agree</span></td>
                          <td colSpan={3} />
                        </tr>
                      ) : p.differences.map((d, i) => (
                        <tr key={`${p.a_invoice_id}-${d.field}`} className="bg-bad-soft/30">
                          {i === 0 && <td rowSpan={p.differences.length} className="align-top"><Link className="num font-medium hover:underline" href={`/invoices/${p.a_invoice_id}`}>{p.invoice_number}</Link></td>}
                          {i === 0 && <td rowSpan={p.differences.length} className="align-top text-[12.5px] font-medium text-bad">{p.differences.length} difference{p.differences.length > 1 && "s"}</td>}
                          <td className={clsx("text-[13px]", d.severity === "error" ? "text-bad" : "text-warn")}>{FIELD_LABELS[d.field] ?? d.field}</td>
                          <td className="num">{d.a ?? <span className="text-faint">empty</span>}</td>
                          <td className="num">{d.b ?? <span className="text-faint">empty</span>}</td>
                        </tr>
                      )),
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <div className="grid gap-6 lg:grid-cols-2">
            {([["only_in_a", res.document_a.filename], ["only_in_b", res.document_b.filename]] as const).map(([k, name]) => (
              <section key={k} className="card overflow-hidden">
                <div className="border-b border-line px-5 py-3.5"><h2 className="font-semibold">Only in <span className="font-normal text-muted">{name}</span></h2></div>
                {res[k].length === 0 ? <div className="px-5 py-5 text-[13px] text-muted">None. Every invoice has a counterpart.</div> : (
                  <ul className="divide-y divide-line">
                    {res[k].map((i) => (
                      <li key={i.invoice_id}><Link href={`/invoices/${i.invoice_id}`} className="flex justify-between px-5 py-3 text-[13px] hover:bg-paper">
                        <span><span className="num font-medium">{i.invoice_number ?? "—"}</span> <span className="text-muted">· {i.supplier_name ?? "unknown"}</span></span>
                        <span className="num">{money(i.total, i.currency)}</span>
                      </Link></li>
                    ))}
                  </ul>
                )}
              </section>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
