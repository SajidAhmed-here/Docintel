"use client";

import { RotateCw, Search } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { DocumentsTable } from "@/components/documents-table";
import { useToast } from "@/components/toast";
import { PageHeader } from "@/components/ui";
import { UploadPanel } from "@/components/upload-panel";
import { api, qs, type DocumentOut, type Paged } from "@/lib/api";

const STATUSES = [["", "All statuses"], ["processing", "Processing"], ["queued", "Queued"], ["completed", "Completed"], ["needs_review", "Needs review"], ["failed", "Failed"]];
const TYPES = [["", "All types"], ["pdf", "PDF"], ["image", "Image"], ["xlsx", "Excel"], ["xls", "Excel 97"], ["csv", "CSV"]];

export default function DocumentsPage() {
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const toast = useToast();
  const key = `/documents${qs({ status, file_type: type, q, page, page_size: 25 })}`;
  const { data, isLoading, mutate } = useSWR<Paged<DocumentOut>>(key, {
    refreshInterval: (d) => (d?.items.some((x) => x.status === "queued" || x.status === "processing") ? 1500 : 15000),
  });
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  async function retryFailed() {
    try {
      const r = await api<{ requeued: number }>("/jobs/reprocess-failed", { method: "POST" });
      toast({ tone: r.requeued ? "info" : "ok", title: r.requeued ? `${r.requeued} failed document(s) re-queued` : "No failed documents" });
      mutate();
    } catch (e) {
      toast({ tone: "bad", title: "Could not re-queue", body: (e as Error).message });
    }
  }

  return (
    <>
      <PageHeader title="Documents" description="Every uploaded file, its processing status and what was extracted."
        actions={<button className="btn-secondary" onClick={retryFailed}><RotateCw className="size-4" /> Retry failed</button>} />
      <UploadPanel compact />
      <section className="card mt-6 overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
          <div className="relative min-w-[220px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
            <input className="input pl-9" placeholder="Filter by file name" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} aria-label="Filter by file name" />
          </div>
          <select className="input w-auto" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label="Status">
            {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <select className="input w-auto" value={type} onChange={(e) => { setType(e.target.value); setPage(1); }} aria-label="File type">
            {TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
        <DocumentsTable docs={data?.items} loading={isLoading} onChange={() => mutate()} />
        {data && data.total > data.page_size && (
          <div className="flex items-center justify-between px-4 py-3 text-[13px] text-muted">
            <span className="num">{(page - 1) * data.page_size + 1}–{Math.min(page * data.page_size, data.total)} of {data.total}</span>
            <div className="flex gap-2">
              <button className="btn-secondary h-8" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
              <button className="btn-secondary h-8" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          </div>
        )}
      </section>
    </>
  );
}
