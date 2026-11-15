"use client";

import clsx from "clsx";
import { ChevronLeft, ChevronRight, ScanText, Image as ImageIcon } from "lucide-react";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { fetcher, type PageMeta } from "@/lib/api";
import { Segmented, Skeleton } from "./ui";

interface Props { documentId: string; fileType: string; pages: PageMeta[]; initialPage?: number; highlight?: string[] }

function Highlighted({ text, terms }: { text: string; terms: string[] }) {
  const clean = terms.filter((t) => t && t.length > 2).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!clean.length) return <>{text}</>;
  const re = new RegExp(`(${clean.join("|")})`, "gi");
  return <>{text.split(re).map((part, i) => (i % 2 ? <mark key={i} className="rounded bg-mark px-0.5 text-ink">{part}</mark> : part))}</>;
}

export function PageViewer({ documentId, fileType, pages, initialPage = 1, highlight = [] }: Props) {
  const hasImage = fileType === "pdf" || fileType === "image";
  const [page, setPage] = useState(initialPage);
  const [mode, setMode] = useState<"image" | "text">(hasImage ? "image" : "text");
  const [loaded, setLoaded] = useState(false);
  useEffect(() => setPage(initialPage), [initialPage]);
  useEffect(() => setLoaded(false), [page, mode]);
  const meta = pages.find((p) => p.page_number === page);
  const { data } = useSWR<{ text: string }>(mode === "text" && pages.length ? `/documents/${documentId}/pages/${page}` : null, fetcher);
  const unit = fileType === "pdf" || fileType === "image" ? "Page" : "Sheet";

  return (
    <div className="card flex h-full min-h-0 flex-col overflow-hidden">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2">
        <div className="flex items-center">
          <button className="btn-ghost h-8 px-2" aria-label="Previous page" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}><ChevronLeft className="size-4" /></button>
          <span className="num min-w-[88px] text-center text-[12.5px] text-ink-2">{unit} {page} / {pages.length || "—"}</span>
          <button className="btn-ghost h-8 px-2" aria-label="Next page" disabled={page >= pages.length} onClick={() => setPage((p) => p + 1)}><ChevronRight className="size-4" /></button>
        </div>
        {meta && (
          <span className="truncate text-[12px] text-muted">
            {meta.sheet_name ? `“${meta.sheet_name}” · ` : ""}
            {meta.method === "ocr" ? <>OCR · <span className="num">{meta.ocr_confidence?.toFixed(0)}%</span> confidence</> : meta.method === "text" ? "Digital text" : "Spreadsheet"}
          </span>
        )}
        <div className="ml-auto">
          {hasImage && (
            <Segmented value={mode} onChange={setMode} options={[{ value: "image", label: "Original" }, { value: "text", label: "Extracted text" }]} />
          )}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-sunken/60">
        {!pages.length ? (
          <div className="grid h-full place-items-center p-8 text-[13px] text-muted">No pages available.</div>
        ) : mode === "image" ? (
          <div className="flex justify-center p-4">
            {!loaded && <Skeleton className="aspect-[1/1.414] w-full max-w-[640px]" />}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img key={page} src={`/api/documents/${documentId}/pages/${page}/image`} alt={`Page ${page}`} onLoad={() => setLoaded(true)}
              className={clsx("w-full max-w-[640px] rounded-sm bg-white shadow-[0_2px_12px_rgb(0_0_0/0.12)]", !loaded && "hidden")} />
          </div>
        ) : data ? (
          <pre className="num whitespace-pre-wrap break-words p-5 text-[12.5px] leading-[1.65] text-ink-2"><Highlighted text={data.text || "(no text on this page)"} terms={highlight} /></pre>
        ) : (
          <div className="space-y-2 p-5">{Array.from({ length: 12 }).map((_, i) => <Skeleton key={i} className="h-3" />)}</div>
        )}
      </div>
      <div className="flex items-center gap-1.5 border-t border-line px-3 py-1.5 text-[11.5px] text-muted">
        {mode === "image" ? <ImageIcon className="size-3.5" /> : <ScanText className="size-3.5" />}
        {mode === "image" ? "Rendered from the uploaded file" : "Text the extractor read. Highlights show the extracted values."}
      </div>
    </div>
  );
}
