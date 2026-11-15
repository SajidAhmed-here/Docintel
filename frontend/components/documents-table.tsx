"use client";

import { AlertTriangle, FileStack, RotateCw } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, type DocumentOut } from "@/lib/api";
import { bytes, duration, FILE_TYPE_LABEL, relTime } from "@/lib/format";
import { useToast } from "./toast";
import { DocStatusBadge, Empty, FileTypeIcon, Progress, Skeleton } from "./ui";

export function DocumentsTable({ docs, loading, onChange }: { docs: DocumentOut[] | undefined; loading?: boolean; onChange?: () => void }) {
  const router = useRouter();
  const toast = useToast();

  async function retry(id: string) {
    try {
      await api(`/documents/${id}/reprocess`, { method: "POST" });
      toast({ tone: "info", title: "Re-queued for processing" });
      onChange?.();
    } catch (e) {
      toast({ tone: "bad", title: "Could not reprocess", body: (e as Error).message });
    }
  }

  if (loading && !docs) {
    return <div className="space-y-3 p-4">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-10" />)}</div>;
  }
  if (!docs?.length) {
    return <Empty icon={<FileStack className="size-5" />} title="No documents yet">Upload invoices, receipts or spreadsheets to start extracting data.</Empty>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="table">
        <thead>
          <tr>
            <th>Document</th>
            <th className="w-[220px]">Status</th>
            <th className="text-right">Pages</th>
            <th className="text-right">Invoices</th>
            <th className="text-right">Time</th>
            <th className="text-right">Uploaded</th>
            <th className="w-10" aria-label="Actions" />
          </tr>
        </thead>
        <tbody>
          {docs.map((d) => {
            const live = d.status === "processing" || d.status === "queued";
            return (
              <tr key={d.id} className="cursor-pointer" onClick={() => router.push(`/documents/${d.id}`)}>
                <td className="max-w-[320px]">
                  <div className="flex items-center gap-2.5">
                    <FileTypeIcon type={d.file_type} />
                    <div className="min-w-0">
                      <Link href={`/documents/${d.id}`} onClick={(e) => e.stopPropagation()} className="block truncate font-medium hover:underline">{d.original_filename}</Link>
                      <div className="text-[12px] text-muted">
                        {FILE_TYPE_LABEL[d.file_type]} · {bytes(d.size_bytes)}
                        {d.duplicate_of && <span className="ml-1.5 text-warn">· possible duplicate upload</span>}
                      </div>
                    </div>
                  </div>
                </td>
                <td>
                  {live ? (
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between text-[12px]">
                        <span className="truncate text-ink-2">{d.stage ?? "queued"}</span>
                        <span className="num text-muted">{d.progress}%</span>
                      </div>
                      <Progress value={d.progress} status={d.status} />
                    </div>
                  ) : d.status === "failed" ? (
                    <div className="max-w-[240px] space-y-1">
                      <DocStatusBadge status={d.status} />
                      <div className="line-clamp-2 text-[12px] leading-snug text-bad" title={d.error_message ?? ""}>{d.error_message}</div>
                    </div>
                  ) : d.status === "needs_review" && d.invoice_count === 0 ? (
                    <div className="space-y-1">
                      <DocStatusBadge status={d.status} />
                      <div className="text-[12px] leading-snug text-muted">No invoice found in this file</div>
                    </div>
                  ) : (
                    <DocStatusBadge status={d.status} />
                  )}
                </td>
                <td className="num text-right text-ink-2">
                  {d.page_count ?? "—"}
                  {!!d.ocr_pages && <div className="text-[11px] text-muted">{d.ocr_pages} OCR</div>}
                </td>
                <td className="num text-right whitespace-nowrap">
                  {live ? "—" : d.invoice_count}
                  {!!d.review_count && <div className="flex items-center justify-end gap-1 text-[11px] text-warn" title={`${d.review_count} need review`}><AlertTriangle className="size-3" />{d.review_count} to review</div>}
                </td>
                <td className="num whitespace-nowrap text-right text-ink-2">{duration(d.processing_ms)}</td>
                <td className="text-right text-[12.5px] text-muted whitespace-nowrap">{relTime(d.created_at)}</td>
                <td onClick={(e) => e.stopPropagation()}>
                  {d.status === "failed" && (
                    <button className="btn-ghost h-8 px-2" title="Reprocess" aria-label={`Reprocess ${d.original_filename}`} onClick={() => retry(d.id)}>
                      <RotateCw className="size-4" />
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
