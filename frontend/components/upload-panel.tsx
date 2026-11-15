"use client";

import clsx from "clsx";
import { CloudUpload, Loader2 } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { useSWRConfig } from "swr";
import { uploadFiles } from "@/lib/api";
import { bytes } from "@/lib/format";
import { useToast } from "./toast";

const ACCEPT = ".pdf,.jpg,.jpeg,.png,.tif,.tiff,.xlsx,.xls,.csv";
const ALLOWED = new Set(ACCEPT.split(","));
const MAX_FILES = 50;

export function UploadPanel({ compact = false }: { compact?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState<{ count: number; size: number; pct: number } | null>(null);
  const toast = useToast();
  const { mutate } = useSWRConfig();

  async function send(list: FileList | File[]) {
    const files = Array.from(list);
    const bad = files.filter((f) => !ALLOWED.has(`.${f.name.split(".").pop()?.toLowerCase()}`));
    const good = files.filter((f) => !bad.includes(f));
    if (bad.length) toast({ tone: "bad", title: `${bad.length} file(s) skipped`, body: `Unsupported type: ${bad.slice(0, 3).map((f) => f.name).join(", ")}` });
    if (!good.length) return;
    // send in batches so a 300-file drop doesn't hit the per-request limit
    for (let i = 0; i < good.length; i += MAX_FILES) {
      const batch = good.slice(i, i + MAX_FILES);
      setBusy({ count: batch.length, size: batch.reduce((s, f) => s + f.size, 0), pct: 0 });
      try {
        const res = await uploadFiles(batch, (pct) => setBusy((b) => (b ? { ...b, pct } : b)));
        const ok = res.filter((r) => r.accepted);
        const rejected = res.filter((r) => !r.accepted);
        const dups = ok.filter((r) => r.duplicate_of);
        if (ok.length) toast({ tone: "ok", title: `${ok.length} document${ok.length > 1 ? "s" : ""} queued for processing`, body: dups.length ? `${dups.length} look like files you've uploaded before.` : undefined });
        rejected.forEach((r) => toast({ tone: "bad", title: `${r.filename} was rejected`, body: r.error ?? undefined }));
      } catch (e) {
        toast({ tone: "bad", title: "Upload failed", body: (e as Error).message });
      }
    }
    setBusy(null);
    mutate((k) => typeof k === "string" && (k.startsWith("/documents") || k.startsWith("/stats")));
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDrag(false);
    if (e.dataTransfer.files.length) send(e.dataTransfer.files);
  }

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
      onDragLeave={() => setDrag(false)}
      onDrop={onDrop}
      onClick={() => !busy && input.current?.click()}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
      role="button"
      tabIndex={0}
      aria-label="Upload documents"
      className={clsx(
        "group relative flex cursor-pointer items-center rounded-xl border border-dashed transition-colors",
        compact ? "gap-4 px-5 py-4" : "flex-col justify-center gap-3 px-6 py-10 text-center",
        drag ? "border-accent bg-accent-soft" : "border-line-strong bg-surface hover:border-accent/60 hover:bg-paper",
      )}
    >
      <input ref={input} type="file" multiple accept={ACCEPT} className="hidden" onChange={(e) => { if (e.target.files) send(e.target.files); e.target.value = ""; }} />
      <div className={clsx("grid shrink-0 place-items-center rounded-full bg-accent-soft text-accent", compact ? "size-10" : "size-12")}>
        {busy ? <Loader2 className="size-5 animate-spin" /> : <CloudUpload className="size-5" />}
      </div>
      {busy ? (
        <div className={clsx("w-full", !compact && "max-w-sm")}>
          <div className="text-[13.5px] font-medium">Uploading {busy.count} file{busy.count > 1 ? "s" : ""} · {bytes(busy.size)}</div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-sunken">
            <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${busy.pct}%` }} />
          </div>
        </div>
      ) : (
        <div className={clsx(compact && "text-left")}>
          <div className="text-[14px] font-medium">
            Drop invoices, receipts or spreadsheets here, or <span className="text-accent underline-offset-2 group-hover:underline">browse</span>
          </div>
          <div className="mt-0.5 text-[12.5px] text-muted">PDF (incl. scanned, 1000+ pages), JPG, PNG, TIFF, XLSX, XLS, CSV · many files at once</div>
        </div>
      )}
    </div>
  );
}
