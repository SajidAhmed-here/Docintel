"use client";

import { ArrowRight, CheckCircle2, Download, FileQuestion, RotateCw } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { useToast } from "@/components/toast";
import { Empty, FileTypeIcon, InvStatusBadge, PageHeader, Skeleton } from "@/components/ui";
import { api, type DocumentOut, type InvoiceSummary, type Paged } from "@/lib/api";
import { date, money, relTime } from "@/lib/format";

/** Files that finished processing but where no invoice was found: a person should confirm that's right. */
function NoInvoiceDocuments({ docs, onChange }: { docs: DocumentOut[]; onChange: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);

  async function act(d: DocumentOut, kind: "confirm" | "reprocess") {
    setBusy(d.id);
    try {
      if (kind === "confirm") {
        await api(`/documents/${d.id}/confirm-no-invoice`, { method: "POST" });
        toast({ tone: "ok", title: "Marked as not an invoice", body: d.original_filename });
      } else {
        await api(`/documents/${d.id}/reprocess`, { method: "POST" });
        toast({ tone: "info", title: "Reprocessing started", body: d.original_filename });
      }
      onChange();
    } catch (e) {
      toast({ tone: "bad", title: kind === "confirm" ? "Could not mark as not an invoice" : "Could not reprocess", body: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card mb-6 overflow-hidden">
      <div className="border-b border-line px-5 py-3.5">
        <h2 className="font-semibold">No invoice found <span className="num ml-1 text-[13px] font-normal text-muted">{docs.length}</span></h2>
        <p className="mt-0.5 text-[13px] text-muted">
          These files were read, but nothing in them looked like an invoice. Open one to check the extracted text, then mark it as not an invoice or reprocess it.
        </p>
      </div>
      <ul className="divide-y divide-line">
        {docs.map((d) => (
          <li key={d.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3">
            <FileTypeIcon type={d.file_type} />
            <div className="min-w-0 flex-1">
              <Link href={`/documents/${d.id}`} className="block truncate font-medium hover:underline">{d.original_filename}</Link>
              <div className="text-[12px] text-muted">
                {d.page_count ?? 0} page{d.page_count === 1 ? "" : "s"} read{d.ocr_pages ? `, ${d.ocr_pages} with OCR` : ""} · uploaded {relTime(d.created_at)}
              </div>
            </div>
            <div className="flex gap-2">
              <button className="btn-ghost h-8" disabled={busy === d.id} onClick={() => act(d, "reprocess")}><RotateCw className="size-3.5" /> Reprocess</button>
              <button className="btn-secondary h-8" disabled={busy === d.id} onClick={() => act(d, "confirm")}><CheckCircle2 className="size-3.5" /> Not an invoice</button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function ReviewQueue() {
  const router = useRouter();
  const { mutate: globalMutate } = useSWRConfig();
  const { data, isLoading } = useSWR<Paged<InvoiceSummary>>("/invoices?needs_review=true&page_size=200", { refreshInterval: 5000 });
  const empty = useSWR<Paged<DocumentOut>>("/documents?status=needs_review&has_invoices=false&page_size=100", { refreshInterval: 5000 });
  const items = data?.items ?? [];
  const emptyDocs = empty.data?.items ?? [];
  const loading = (isLoading && !data) || (empty.isLoading && !empty.data);

  function refresh() {
    empty.mutate();
    globalMutate((k) => typeof k === "string" && (k.startsWith("/documents") || k.startsWith("/stats")));
  }

  return (
    <>
      <PageHeader title="Review queue" description="Invoices that failed a validation rule, were read with low confidence or look like duplicates, plus files where no invoice was found."
        actions={<>
          <a className="btn-secondary" href="/api/invoices/export.csv"><Download className="size-4" /> Export all (CSV)</a>
          {items[0] && <Link href={`/invoices/${items[0].id}`} className="btn-primary">Start reviewing <ArrowRight className="size-4" /></Link>}
        </>} />

      {!loading && emptyDocs.length > 0 && <NoInvoiceDocuments docs={emptyDocs} onChange={refresh} />}

      <section className="card overflow-hidden">
        {emptyDocs.length > 0 && (
          <div className="border-b border-line px-5 py-3.5">
            <h2 className="font-semibold">Invoices to check <span className="num ml-1 text-[13px] font-normal text-muted">{items.length}</span></h2>
          </div>
        )}
        {loading ? <div className="space-y-3 p-4">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10" />)}</div>
          : !items.length ? (
            <Empty icon={emptyDocs.length ? <FileQuestion className="size-5" /> : <CheckCircle2 className="size-5 text-ok" />}
              title={emptyDocs.length ? "No invoices waiting" : "Nothing to review"}>
              {emptyDocs.length ? "Every extracted invoice passed validation or has been approved. Only the files above still need a decision."
                : "Every extracted invoice has passed validation or been approved."}
            </Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="table">
                <thead><tr><th>Invoice</th><th>Supplier</th><th>Date</th><th className="text-right">Total</th><th>Problems</th><th>Source</th></tr></thead>
                <tbody>
                  {items.map((i) => (
                    <tr key={i.id} className="cursor-pointer" onClick={() => router.push(`/invoices/${i.id}`)}>
                      <td><div className="flex items-center gap-2"><span className="num font-medium">{i.invoice_number ?? <span className="text-bad">missing</span>}</span><InvStatusBadge status={i.status} /></div></td>
                      <td className="max-w-[240px] truncate">{i.supplier_name ?? <span className="text-muted">—</span>}</td>
                      <td className="whitespace-nowrap text-ink-2">{date(i.invoice_date)}</td>
                      <td className="num text-right font-medium">{money(i.total, i.currency)}</td>
                      <td className="whitespace-nowrap text-[12.5px]">
                        {i.error_count > 0 && <span className="text-bad">{i.error_count} error{i.error_count > 1 && "s"}</span>}
                        {i.error_count > 0 && i.warning_count > 0 && <span className="text-faint"> · </span>}
                        {i.warning_count > 0 && <span className="text-warn">{i.warning_count} warning{i.warning_count > 1 && "s"}</span>}
                      </td>
                      <td className="max-w-[220px] truncate text-[12.5px] text-muted">{i.document_filename}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </section>
    </>
  );
}
