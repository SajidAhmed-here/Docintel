"use client";

import clsx from "clsx";
import { AlertTriangle, ArrowLeft, CheckCircle2, ChevronRight, Download, RotateCw, Trash2, XCircle } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import { PageViewer } from "@/components/page-viewer";
import { useToast } from "@/components/toast";
import { DocStatusBadge, Empty, FileTypeIcon, InvStatusBadge, PageHeader, Progress, Skeleton } from "@/components/ui";
import { api, type DocumentDetail } from "@/lib/api";
import { bytes, date, duration, FILE_TYPE_LABEL, money, relTime } from "@/lib/format";

const STAGE_LABEL: Record<string, string> = { detect: "File check", text: "Text & OCR", extract: "Field extraction", validate: "Validation", index: "Search index", failed: "Failed" };

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const [page, setPage] = useState(1);
  const { data: d, mutate, error } = useSWR<DocumentDetail>(`/documents/${id}`, {
    refreshInterval: (x) => (x && (x.status === "queued" || x.status === "processing") ? 1200 : 0),
  });

  async function reprocess() {
    try {
      await api(`/documents/${id}/reprocess`, { method: "POST" });
      toast({ tone: "info", title: "Re-queued for processing" });
      mutate();
    } catch (e) { toast({ tone: "bad", title: "Could not reprocess", body: (e as Error).message }); }
  }
  async function confirmNoInvoice() {
    try {
      await api(`/documents/${id}/confirm-no-invoice`, { method: "POST" });
      toast({ tone: "ok", title: "Marked as not an invoice" });
      mutate();
    } catch (e) { toast({ tone: "bad", title: "Could not mark as not an invoice", body: (e as Error).message }); }
  }
  async function remove() {
    if (!confirm("Delete this document and everything extracted from it?")) return;
    await api(`/documents/${id}`, { method: "DELETE" });
    toast({ tone: "ok", title: "Document deleted" });
    router.push("/documents");
  }

  if (error) return <Empty title="Document not found">{(error as Error).message}</Empty>;
  if (!d) return <div className="space-y-4"><Skeleton className="h-10 w-1/2" /><Skeleton className="h-[600px]" /></div>;
  const live = d.status === "queued" || d.status === "processing";

  return (
    <>
      <PageHeader
        eyebrow={<Link href="/documents" className="inline-flex items-center gap-1 hover:text-ink"><ArrowLeft className="size-3.5" /> Documents</Link>}
        title={<span className="flex items-center gap-2.5"><FileTypeIcon type={d.file_type} className="size-5" />{d.original_filename}</span>}
        description={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <DocStatusBadge status={d.status} />
            <span>{FILE_TYPE_LABEL[d.file_type]} · {bytes(d.size_bytes)}</span>
            {d.page_count != null && <span className="num">{d.page_count} {d.file_type === "pdf" || d.file_type === "image" ? "pages" : "sheets"}{d.ocr_pages ? ` (${d.ocr_pages} OCR)` : ""}</span>}
            {d.processing_ms != null && <span className="num">processed in {duration(d.processing_ms)}</span>}
            <span>uploaded {relTime(d.created_at)}</span>
          </span>
        }
        actions={<>
          <a className="btn-secondary" href={`/api/documents/${id}/file`}><Download className="size-4" /> Original</a>
          <button className="btn-secondary" onClick={reprocess} disabled={live}><RotateCw className="size-4" /> Reprocess</button>
          <button className="btn-danger" onClick={remove} aria-label="Delete document"><Trash2 className="size-4" /></button>
        </>}
      />

      {live && (
        <div className="card mb-5 p-4">
          <div className="mb-2 flex justify-between text-[13px]"><span className="font-medium">{d.stage}</span><span className="num text-muted">{d.progress}%</span></div>
          <Progress value={d.progress} status={d.status} />
        </div>
      )}
      {d.status === "failed" && (
        <div className="mb-5 flex gap-3 rounded-xl border border-bad/20 bg-bad-soft p-4">
          <XCircle className="mt-0.5 size-5 shrink-0 text-bad" />
          <div className="flex-1">
            <div className="font-medium text-bad">Processing failed <span className="num text-[12px] font-normal">({d.error_code})</span></div>
            <div className="mt-0.5 text-[13px] text-ink-2">{d.error_message}</div>
          </div>
          <button className="btn-secondary" onClick={reprocess}><RotateCw className="size-4" /> Try again</button>
        </div>
      )}
      {d.duplicate_of && (
        <div className="mb-5 flex items-center gap-2.5 rounded-xl border border-warn/20 bg-warn-soft px-4 py-3 text-[13px]">
          <AlertTriangle className="size-4 text-warn" />
          This exact file was uploaded before.
          <Link href={`/documents/${d.duplicate_of}`} className="font-medium text-warn underline">Open the earlier copy</Link>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <div className="h-[calc(100vh-210px)] min-h-[520px] lg:sticky lg:top-6">
          <PageViewer documentId={d.id} fileType={d.file_type} pages={d.pages} initialPage={page} />
        </div>

        <div className="space-y-6">
          <section className="card overflow-hidden">
            <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
              <h2 className="font-semibold">Extracted invoices</h2>
              <span className="num text-[12.5px] text-muted">{d.invoices.length} found{d.review_count ? ` · ${d.review_count} need review` : ""}</span>
            </div>
            {d.invoices.length === 0 ? (
              <Empty title={live ? "Still processing…" : "No invoice data found"}>
                {live ? "Results appear here as soon as extraction finishes." : "Nothing in this file looked like an invoice. Check the extracted text on the left; if it really isn't an invoice, mark it so it leaves the review queue."}
                {!live && d.status === "needs_review" && (
                  <div className="mt-4 flex justify-center gap-2">
                    <button className="btn-secondary h-8" onClick={confirmNoInvoice}><CheckCircle2 className="size-3.5" /> Not an invoice</button>
                    <button className="btn-ghost h-8" onClick={reprocess}><RotateCw className="size-3.5" /> Reprocess</button>
                  </div>
                )}
              </Empty>
            ) : (
              <ul className="max-h-[560px] divide-y divide-line overflow-auto">
                {d.invoices.map((inv) => (
                  <li key={inv.id}>
                    <Link href={`/invoices/${inv.id}`} onMouseEnter={() => inv.page_start && setPage(inv.page_start)}
                      className="flex items-center gap-4 px-5 py-3.5 transition-colors hover:bg-paper">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="num font-medium">{inv.invoice_number ?? <span className="text-bad">No number</span>}</span>
                          <InvStatusBadge status={inv.status} />
                        </div>
                        <div className="mt-0.5 truncate text-[12.5px] text-muted">
                          {inv.supplier_name ?? "Unknown supplier"} · {date(inv.invoice_date)}
                          {inv.page_start != null && <> · p.{inv.page_start}{inv.page_end !== inv.page_start ? `–${inv.page_end}` : ""}</>}
                          {inv.source_ref && <> · {inv.source_ref}</>}
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="num font-semibold">{money(inv.total, inv.currency)}</div>
                        {(inv.error_count > 0 || inv.warning_count > 0) && (
                          <div className="text-[11.5px]">
                            {inv.error_count > 0 && <span className="text-bad">{inv.error_count} error{inv.error_count > 1 && "s"}</span>}
                            {inv.error_count > 0 && inv.warning_count > 0 && <span className="text-faint"> · </span>}
                            {inv.warning_count > 0 && <span className="text-warn">{inv.warning_count} warning{inv.warning_count > 1 && "s"}</span>}
                          </div>
                        )}
                      </div>
                      <ChevronRight className="size-4 text-faint" />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card p-5">
            <h2 className="font-semibold">Processing history</h2>
            <ol className="relative mt-4 space-y-4 border-l border-line pl-5">
              {d.events.map((e, i) => (
                <li key={i} className="relative">
                  <span className={clsx("absolute -left-[25.5px] top-1 size-2.5 rounded-full ring-4 ring-surface",
                    e.status === "failed" ? "bg-bad" : e.status === "warning" ? "bg-warn" : "bg-ok")} />
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-[13px] font-medium">{STAGE_LABEL[e.stage] ?? e.stage}{e.attempt > 1 && <span className="ml-1.5 text-[11.5px] font-normal text-muted">attempt {e.attempt}</span>}</span>
                    <span className="num shrink-0 text-[11.5px] text-muted">{duration(e.duration_ms)}</span>
                  </div>
                  {e.message && <div className="mt-0.5 break-words text-[12.5px] text-muted">{e.message.length > 300 ? `${e.message.slice(0, 300)}…` : e.message}</div>}
                </li>
              ))}
              {!d.events.length && <li className="text-[13px] text-muted">Waiting for a worker…</li>}
            </ol>
          </section>
        </div>
      </div>
    </>
  );
}
