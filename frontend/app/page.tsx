"use client";

import clsx from "clsx";
import { ArrowRight, ChevronRight } from "lucide-react";
import Link from "next/link";
import useSWR from "swr";
import { DocumentsTable } from "@/components/documents-table";
import { PageHeader } from "@/components/ui";
import { UploadPanel } from "@/components/upload-panel";
import type { DocumentOut, Paged, Stats } from "@/lib/api";
import { duration, ISSUE_LABELS, money, number } from "@/lib/format";

const METHOD_LABELS: Record<string, string> = {
  llm: "AI model",
  rules: "Rule-based",
  rules_fallback: "Rules (AI unavailable)",
  table: "Spreadsheet columns",
};

/** One step of the processing pipeline: what came in, what was read, extracted, validated. */
function Step({ n, title, value, detail, tone, href, last }: {
  n: number; title: string; value: string; detail: React.ReactNode; tone?: "warn" | "ok" | "bad"; href?: string; last?: boolean;
}) {
  const body = (
    <div className="relative h-full px-5 py-4">
      <div className="flex items-center gap-2 text-[12.5px] font-medium text-muted">
        <span className="num grid size-5 place-items-center rounded-full border border-line-strong bg-surface text-[11px] text-ink-2">{n}</span>
        {title}
      </div>
      <div className={clsx("num mt-3 text-[34px] font-semibold leading-none tracking-tight",
        tone === "warn" ? "text-warn" : tone === "bad" ? "text-bad" : tone === "ok" ? "text-ok" : "text-ink")}>{value}</div>
      <div className="mt-2 text-[12.5px] text-muted">{detail}</div>
      {!last && <ChevronRight className="absolute -right-2.5 top-1/2 z-[1] hidden size-5 -translate-y-1/2 rounded-full border border-line bg-surface p-0.5 text-faint lg:block" aria-hidden />}
    </div>
  );
  return href ? <Link href={href} className="block transition-colors hover:bg-paper/70">{body}</Link> : body;
}

function StatusBar({ by }: { by: Record<string, number> }) {
  const parts = [
    { k: "completed", label: "Completed", cls: "bg-ok" },
    { k: "needs_review", label: "Needs review", cls: "bg-warn" },
    { k: "processing", label: "Processing", cls: "bg-accent" },
    { k: "queued", label: "Queued", cls: "bg-line-strong" },
    { k: "failed", label: "Failed", cls: "bg-bad" },
  ].map((p) => ({ ...p, v: by[p.k] ?? 0 }));
  const total = parts.reduce((s, p) => s + p.v, 0);
  if (!total) return null;
  return (
    <div className="border-t border-line px-5 py-3.5">
      <div className="flex h-2 overflow-hidden rounded-full bg-sunken" role="img"
        aria-label={parts.filter((p) => p.v).map((p) => `${p.v} ${p.label.toLowerCase()}`).join(", ")}>
        {parts.map((p) => p.v ? <div key={p.k} className={clsx(p.cls, "h-full transition-[width] duration-500")} style={{ width: `${(p.v / total) * 100}%` }} /> : null)}
      </div>
      <div className="mt-2.5 flex flex-wrap gap-x-5 gap-y-1 text-[12px] text-muted">
        {parts.filter((p) => p.v).map((p) => (
          <span key={p.k} className="inline-flex items-center gap-1.5">
            <span className={clsx("size-2 rounded-sm", p.cls)} />{p.label} <span className="num text-ink-2">{p.v}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

export default function Overview() {
  const docs = useSWR<Paged<DocumentOut>>("/documents?page_size=8", {
    refreshInterval: (d) => (d?.items.some((x) => x.status === "queued" || x.status === "processing") ? 1500 : 10000),
  });
  const { data: s } = useSWR<Stats>("/stats", { refreshInterval: 4000 });
  const maxIssue = Math.max(1, ...(s?.top_issues.map((i) => i.count) ?? [1]));
  const inFlight = (s?.documents_by_status.processing ?? 0) + (s?.documents_by_status.queued ?? 0);
  const validated = s ? Math.max(0, s.invoices - s.needs_review) : undefined;
  const methods = Object.entries(s?.by_method ?? {}).sort((a, b) => b[1] - a[1]);
  const methodTotal = methods.reduce((t, [, v]) => t + v, 0);

  return (
    <>
      <PageHeader title="Overview" description="Every document moves through the same four steps. Anything that doesn't add up lands in the review queue." />

      <section className="card overflow-hidden" aria-label="Processing pipeline">
        <div className="ledger-rule grid grid-cols-2 lg:grid-cols-4 [&>*]:border-line [&>*:nth-child(odd)]:border-r [&>*:nth-child(-n+2)]:border-b lg:[&>*:nth-child(-n+2)]:border-b-0 lg:[&>*:not(:last-child)]:border-r">
          <Step n={1} title="Received" value={number(s?.documents)} href="/documents"
            detail={inFlight ? <span className="text-accent">{inFlight} in progress now</span> : `${s?.documents_by_status.failed ?? 0} could not be read`} />
          <Step n={2} title="Read" value={number(s?.pages_processed)}
            detail={s ? `pages · ${number(s.ocr_pages)} needed OCR` : "pages"} />
          <Step n={3} title="Extracted" value={number(s?.invoices)}
            detail={`invoices · avg ${duration(s?.avg_processing_ms)} per document`} />
          <Step n={4} title="Validated" value={number(validated)} tone={validated ? "ok" : undefined} last href={s?.needs_review ? "/review" : undefined}
            detail={s?.needs_review ? <span className="inline-flex items-center gap-1 text-warn">{s.needs_review} waiting for review <ArrowRight className="size-3" /></span> : "all arithmetic checks pass"} />
        </div>
        {s && <StatusBar by={s.documents_by_status} />}
      </section>

      <div className="mt-6"><UploadPanel /></div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_320px]">
        <section className="card min-w-0 overflow-hidden">
          <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
            <h2 className="font-semibold">Recent documents</h2>
            <Link href="/documents" className="text-[13px] font-medium text-accent hover:underline">View all</Link>
          </div>
          <DocumentsTable docs={docs.data?.items} loading={docs.isLoading} onChange={() => docs.mutate()} />
        </section>

        <div className="space-y-6">
          <section className="card p-5">
            <h2 className="font-semibold">What validation caught</h2>
            <div className="mt-4 space-y-3">
              {s?.top_issues.length ? s.top_issues.map((i) => (
                <div key={i.code}>
                  <div className="mb-1 flex justify-between gap-3 text-[12.5px]">
                    <span className="text-ink-2">{ISSUE_LABELS[i.code] ?? i.code}</span>
                    <span className="num text-muted">{i.count}</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-sunken">
                    <div className="h-full rounded-full bg-warn" style={{ width: `${(i.count / maxIssue) * 100}%` }} />
                  </div>
                </div>
              )) : <div className="text-[13px] text-muted">Nothing flagged yet.</div>}
            </div>
          </section>

          {methodTotal > 0 && (
            <section className="card p-5">
              <h2 className="font-semibold">How invoices were read</h2>
              <div className="mt-3 space-y-2">
                {methods.map(([m, v]) => (
                  <div key={m} className="flex items-center gap-3 text-[13px]">
                    <span className="w-[150px] shrink-0 truncate text-ink-2">{METHOD_LABELS[m] ?? m}</span>
                    <div className="h-1.5 flex-1 rounded-full bg-sunken">
                      <div className="h-full rounded-full bg-accent" style={{ width: `${(v / methodTotal) * 100}%` }} />
                    </div>
                    <span className="num w-8 text-right text-muted">{v}</span>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="card p-5">
            <h2 className="font-semibold">Value by currency</h2>
            <div className="mt-3 divide-y divide-line">
              {s?.by_currency.length ? s.by_currency.map((c) => (
                <div key={c.currency} className="flex items-center justify-between py-2 text-[13px]">
                  <span><span className="font-medium">{c.currency}</span> <span className="text-muted">{c.count} invoices</span></span>
                  <span className="num font-medium">{money(c.total, c.currency)}</span>
                </div>
              )) : <div className="py-2 text-[13px] text-muted">No invoices yet.</div>}
            </div>
          </section>
        </div>
      </div>
    </>
  );
}
