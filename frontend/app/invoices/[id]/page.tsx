"use client";

import clsx from "clsx";
import { AlertTriangle, ArrowLeft, Check, CircleAlert, History, Info, Plus, RotateCcw, Save, ShieldCheck, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { PageViewer } from "@/components/page-viewer";
import { useToast } from "@/components/toast";
import { ConfidenceDot, Empty, InvStatusBadge, PageHeader, SeverityPill, Skeleton } from "@/components/ui";
import { api, type DocumentDetail, type InvoiceDetail, type InvoiceSummary, type Paged } from "@/lib/api";
import { date as fmtDate, FIELD_LABELS, ISSUE_LABELS, money, relTime } from "@/lib/format";

const FIELDS: { key: keyof InvoiceDetail; type: "text" | "date" | "money" | "currency"; wide?: boolean }[] = [
  { key: "invoice_number", type: "text" },
  { key: "invoice_date", type: "date" },
  { key: "supplier_name", type: "text", wide: true },
  { key: "customer_name", type: "text", wide: true },
  { key: "subtotal", type: "money" },
  { key: "tax", type: "money" },
  { key: "total", type: "money" },
  { key: "currency", type: "currency" },
];

type Row = { description: string; quantity: string; unit_price: string; amount: string };
const toNum = (s: string) => (s.trim() === "" ? null : Number(s.replace(/,/g, "")));
const near = (a: number, b: number, tol = 0.011) => Math.abs(a - b) <= tol;

export default function InvoicePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const { mutate: globalMutate } = useSWRConfig();
  const { data: inv, mutate, error } = useSWR<InvoiceDetail>(`/invoices/${id}`);
  const { data: doc } = useSWR<DocumentDetail>(inv ? `/documents/${inv.document_id}` : null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [rows, setRows] = useState<Row[]>([]);
  const [saving, setSaving] = useState(false);
  const refs = useRef<Record<string, HTMLInputElement | null>>({});

  const reset = useCallback((x: InvoiceDetail) => {
    setForm(Object.fromEntries(FIELDS.map((f) => [f.key, (x[f.key] as string | null) ?? ""])));
    setRows(x.line_items.map((l) => ({ description: l.description ?? "", quantity: l.quantity ?? "", unit_price: l.unit_price ?? "", amount: l.amount ?? "" })));
  }, []);
  useEffect(() => { if (inv) reset(inv); }, [inv, reset]);

  const changedFields = useMemo(() => inv ? FIELDS.filter((f) => (form[f.key] ?? "") !== ((inv[f.key] as string | null) ?? "")).map((f) => f.key as string) : [], [form, inv]);
  const rowsChanged = useMemo(() => inv ? JSON.stringify(rows) !== JSON.stringify(inv.line_items.map((l) => ({ description: l.description ?? "", quantity: l.quantity ?? "", unit_price: l.unit_price ?? "", amount: l.amount ?? "" }))) : false, [rows, inv]);
  const dirty = changedFields.length > 0 || rowsChanged;

  // live arithmetic checks on what's currently in the form
  const checks = useMemo(() => {
    const sub = toNum(form.subtotal ?? ""), tax = toNum(form.tax ?? ""), tot = toNum(form.total ?? "");
    const amounts = rows.map((r) => toNum(r.amount));
    const sum = amounts.every((a) => a !== null) && rows.length ? amounts.reduce((s, a) => s + (a as number), 0) : null;
    return {
      totalOk: sub !== null && tax !== null && tot !== null ? near(sub + tax, tot, 0.02) : null,
      expectedTotal: sub !== null && tax !== null ? sub + tax : null,
      sum,
      sumOk: sum !== null && (sub !== null || tot !== null) ? near(sum, sub ?? tot ?? 0, 0.01 * (rows.length + 1)) || (tot !== null && near(sum, tot, 0.02)) : null,
      rowOk: rows.map((r) => {
        const q = toNum(r.quantity), u = toNum(r.unit_price), a = toNum(r.amount);
        return q !== null && u !== null && a !== null ? Math.abs(q * u - a) <= Math.max(0.01, Math.abs(q) * 0.005) : null;
      }),
    };
  }, [form, rows]);

  const save = useCallback(async (approve = false) => {
    if (!inv) return;
    setSaving(true);
    try {
      const body: Record<string, unknown> = { approve, corrected_by: "reviewer" };
      if (changedFields.length) body.fields = Object.fromEntries(changedFields.map((k) => [k, form[k] === "" ? null : form[k]]));
      if (rowsChanged) body.line_items = rows.map((r) => ({ description: r.description || null, quantity: toNum(r.quantity), unit_price: toNum(r.unit_price), amount: toNum(r.amount) }));
      const updated = await api<InvoiceDetail>(`/invoices/${id}`, { method: "PATCH", body: JSON.stringify(body) });
      await mutate(updated, { revalidate: false });
      globalMutate((k) => typeof k === "string" && (k.startsWith("/documents") || k.startsWith("/stats") || k.startsWith("/invoices?")));
      if (approve) {
        toast({ tone: "ok", title: "Invoice approved" });
        const next = await api<Paged<InvoiceSummary>>("/invoices?needs_review=true&page_size=1");
        if (next.items[0]) router.push(`/invoices/${next.items[0].id}`);
      } else {
        const errs = updated.issues.filter((i) => i.severity === "error").length;
        toast({ tone: errs ? "info" : "ok", title: "Corrections saved", body: errs ? `${errs} validation error(s) remain.` : "All validation checks pass." });
      }
    } catch (e) {
      toast({ tone: "bad", title: "Could not save", body: (e as Error).message });
    } finally { setSaving(false); }
  }, [inv, changedFields, rowsChanged, form, rows, id, mutate, globalMutate, toast, router]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); if (dirty && !saving) save(false); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dirty, saving, save]);

  if (error) return <Empty title="Invoice not found">{(error as Error).message}</Empty>;
  if (!inv) return <div className="space-y-4"><Skeleton className="h-10 w-1/3" /><Skeleton className="h-[640px]" /></div>;

  const fieldIssues = (k: string) => inv.issues.filter((i) => i.field === k);
  const errors = inv.issues.filter((i) => i.severity === "error");
  const warnings = inv.issues.filter((i) => i.severity === "warning");
  const highlight = [inv.invoice_number, inv.supplier_name, inv.customer_name, inv.total, inv.subtotal, inv.tax].filter(Boolean) as string[];

  return (
    <>
      <PageHeader
        eyebrow={<Link href={`/documents/${inv.document_id}`} className="inline-flex items-center gap-1 hover:text-ink"><ArrowLeft className="size-3.5" /> {inv.document_filename}</Link>}
        title={<span className="flex items-center gap-3"><span className="num">{inv.invoice_number ?? "Invoice"}</span><InvStatusBadge status={inv.status} /></span>}
        description={<>
          {inv.supplier_name ?? "Unknown supplier"} · {fmtDate(inv.invoice_date)} · extracted by{" "}
          <span className="font-medium text-ink-2">{inv.extraction_method === "llm" ? `AI (${inv.model})` : inv.extraction_method === "spreadsheet" ? "spreadsheet mapping" : inv.extraction_method === "rules_fallback" ? "rules (AI unavailable)" : "rules"}</span>
        </>}
        actions={<>
          {dirty && <button className="btn-ghost" onClick={() => reset(inv)}><RotateCcw className="size-4" /> Discard</button>}
          <button className="btn-secondary" disabled={!dirty || saving} onClick={() => save(false)} title="Save (Ctrl+S)"><Save className="size-4" /> Save</button>
          <button className="btn-primary" disabled={saving} onClick={() => save(true)}><ShieldCheck className="size-4" /> {dirty ? "Save & approve" : "Approve"}</button>
        </>}
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
        <div className="h-[calc(100vh-190px)] min-h-[560px] lg:sticky lg:top-6">
          {doc ? <PageViewer documentId={doc.id} fileType={doc.file_type} pages={doc.pages} initialPage={inv.page_start ?? 1} highlight={highlight} /> : <Skeleton className="h-full" />}
        </div>

        <div className="space-y-5">
          {(errors.length > 0 || warnings.length > 0) && (
            <section className={clsx("rounded-xl border p-4", errors.length ? "border-bad/20 bg-bad-soft/60" : "border-warn/20 bg-warn-soft/60")}>
              <div className="mb-2.5 flex items-center gap-2 font-medium">
                {errors.length ? <CircleAlert className="size-4 text-bad" /> : <AlertTriangle className="size-4 text-warn" />}
                {errors.length ? `${errors.length} problem${errors.length > 1 ? "s" : ""} to fix` : "Worth a look"}
                {warnings.length > 0 && errors.length > 0 && <span className="text-[12.5px] font-normal text-muted">+ {warnings.length} warning{warnings.length > 1 ? "s" : ""}</span>}
              </div>
              <ul className="space-y-2">
                {[...errors, ...warnings].map((i, n) => (
                  <li key={n} className="flex gap-2.5 text-[13px]">
                    <SeverityPill severity={i.severity}>{ISSUE_LABELS[i.code] ?? i.code}</SeverityPill>
                    <button className="text-left text-ink-2 hover:text-ink disabled:cursor-default" disabled={!i.field || !refs.current[i.field]}
                      onClick={() => i.field && refs.current[i.field]?.focus()}>{i.message}</button>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {errors.length === 0 && warnings.length === 0 && (
            <div className="flex items-center gap-2.5 rounded-xl border border-ok/20 bg-ok-soft/70 px-4 py-3 text-[13px] text-ok">
              <Check className="size-4" /> All validation checks pass.
            </div>
          )}

          <section className="card p-5">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="font-semibold">Invoice details</h2>
              {dirty && <span className="text-[12px] text-info">Unsaved changes · Ctrl+S</span>}
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-3.5">
              {FIELDS.map((f) => {
                const k = f.key as string;
                const issues = fieldIssues(k);
                const err = issues.some((i) => i.severity === "error");
                const warn = !err && issues.length > 0;
                const changed = changedFields.includes(k);
                return (
                  <label key={k} className={clsx("block", f.wide && "col-span-2 sm:col-span-1")}>
                    <span className="mb-1 flex items-center justify-between">
                      <span className="text-[12.5px] font-medium text-ink-2">{FIELD_LABELS[k]}{changed && <span className="ml-1.5 text-info">• edited</span>}</span>
                      {!changed && <ConfidenceDot value={(inv.field_confidence ?? {})[k]} />}
                    </span>
                    <input
                      ref={(el) => { refs.current[k] = el; }}
                      className={clsx("input", (f.type === "money" || k === "invoice_number") && "num",
                        err && "border-bad/60 bg-bad-soft/40 focus:border-bad", warn && "border-warn/60", changed && "border-info/60 bg-info-soft/30")}
                      type={f.type === "date" ? "date" : "text"}
                      inputMode={f.type === "money" ? "decimal" : undefined}
                      maxLength={f.type === "currency" ? 3 : 300}
                      placeholder={f.type === "money" ? "0.00" : f.type === "currency" ? "GBP" : "Not found"}
                      value={form[k] ?? ""}
                      onChange={(e) => setForm((s) => ({ ...s, [k]: f.type === "currency" ? e.target.value.toUpperCase() : e.target.value }))}
                    />
                  </label>
                );
              })}
            </div>
            <div className={clsx("mt-4 flex items-center justify-between rounded-lg px-3.5 py-2.5 text-[13px]",
              checks.totalOk === null ? "bg-sunken text-muted" : checks.totalOk ? "bg-ok-soft text-ok" : "bg-bad-soft text-bad")}>
              <span>Subtotal + tax = total</span>
              <span className="num font-medium">
                {checks.expectedTotal === null ? "can't check" : checks.totalOk ? <span className="inline-flex items-center gap-1"><Check className="size-3.5" /> {money(checks.expectedTotal, form.currency)}</span>
                  : `${money(checks.expectedTotal, form.currency)} ≠ ${money(form.total, form.currency)}`}
              </span>
            </div>
          </section>

          <section className="card overflow-hidden">
            <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
              <h2 className="font-semibold">Line items <span className="num ml-1 text-[12.5px] font-normal text-muted">{rows.length}</span></h2>
              <button className="btn-ghost h-8" onClick={() => setRows((r) => [...r, { description: "", quantity: "", unit_price: "", amount: "" }])}><Plus className="size-4" /> Add line</button>
            </div>
            {rows.length === 0 ? (
              <div className="px-5 py-6 text-center text-[13px] text-muted">No line items were extracted.</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="table">
                  <thead><tr><th>Description</th><th className="w-[84px] text-right">Qty</th><th className="w-[112px] text-right">Unit price</th><th className="w-[120px] text-right">Amount</th><th className="w-9" /></tr></thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i} className={clsx(checks.rowOk[i] === false && "bg-bad-soft/40")}>
                        {(["description", "quantity", "unit_price", "amount"] as const).map((c) => (
                          <td key={c} className="px-2 py-1.5">
                            <input aria-label={`${c} line ${i + 1}`} className={clsx("input h-8", c !== "description" && "num text-right", checks.rowOk[i] === false && c === "amount" && "border-bad/60")}
                              value={r[c]} onChange={(e) => setRows((rs) => rs.map((x, j) => (j === i ? { ...x, [c]: e.target.value } : x)))} />
                          </td>
                        ))}
                        <td className="px-1"><button className="btn-ghost h-8 px-2 text-faint hover:text-bad" aria-label={`Remove line ${i + 1}`} onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}><Trash2 className="size-3.5" /></button></td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td colSpan={3} className="px-4 py-2.5 text-right text-[12.5px] text-muted">Sum of line items</td>
                      <td className={clsx("num px-4 py-2.5 text-right font-medium", checks.sumOk === false && "text-bad", checks.sumOk && "text-ok")}>{money(checks.sum, form.currency)}</td>
                      <td />
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
          </section>

          {inv.notes.length > 0 && (
            <section className="card p-5">
              <h2 className="flex items-center gap-2 font-semibold"><Info className="size-4 text-info" /> Extraction notes</h2>
              <ul className="mt-2.5 list-disc space-y-1 pl-5 text-[13px] text-ink-2">{inv.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
            </section>
          )}

          <section className="card p-5">
            <h2 className="flex items-center gap-2 font-semibold"><History className="size-4 text-muted" /> Change history</h2>
            {inv.corrections.length === 0 ? <p className="mt-2 text-[13px] text-muted">No manual changes yet. Every edit is recorded here.</p> : (
              <ul className="mt-3 divide-y divide-line">
                {[...inv.corrections].reverse().map((c, i) => (
                  <li key={i} className="py-2.5 text-[13px]">
                    <div className="flex justify-between gap-3"><span className="font-medium">{FIELD_LABELS[c.field] ?? c.field}</span><span className="shrink-0 text-[12px] text-muted">{c.corrected_by} · {relTime(c.created_at)}</span></div>
                    {c.field !== "line_items" && (
                      <div className="num mt-0.5 text-[12.5px]"><span className="text-bad line-through">{c.old_value ?? "empty"}</span> <span className="text-faint">→</span> <span className="text-ok">{c.new_value ?? "empty"}</span></div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </>
  );
}
