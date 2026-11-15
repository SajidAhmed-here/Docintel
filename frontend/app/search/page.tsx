"use client";

import { Search as SearchIcon, SlidersHorizontal, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { Empty, FileTypeIcon, InvStatusBadge, PageHeader, Segmented, Skeleton } from "@/components/ui";
import { qs, type SearchHit } from "@/lib/api";
import { date, money } from "@/lib/format";

type Mode = "hybrid" | "keyword" | "semantic";
const EMPTY = { currency: "", status: "", file_type: "", supplier: "", date_from: "", date_to: "", min_total: "", max_total: "" };

function useDebounced<T>(v: T, ms = 300) {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

export default function SearchPage() {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<Mode>("hybrid");
  const [showFilters, setShowFilters] = useState(false);
  const [f, setF] = useState(EMPTY);
  useEffect(() => {
    const initial = new URLSearchParams(window.location.search).get("q");
    if (initial) setQ(initial);
  }, []);
  const dq = useDebounced(q);
  const df = useDebounced(f);
  const { data, isLoading } = useSWR<SearchHit[]>(`/search${qs({ q: dq, mode, limit: 50, ...df })}`);
  const active = Object.values(f).filter(Boolean).length;

  return (
    <>
      <PageHeader title="Search" description="Find invoices by number, supplier or amount, or by meaning (e.g. “office furniture”, “IT support last spring”)." />
      <div className="card p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative min-w-[260px] flex-1">
            <SearchIcon className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-faint" />
            <input autoFocus className="input h-11 pl-11 text-[15px]" placeholder="Search invoices, suppliers, line items…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" />
          </div>
          <Segmented value={mode} onChange={setMode} options={[{ value: "hybrid", label: "Hybrid" }, { value: "keyword", label: "Keyword" }, { value: "semantic", label: "Semantic" }]} />
          <button className="btn-secondary h-10" onClick={() => setShowFilters((s) => !s)} aria-expanded={showFilters}>
            <SlidersHorizontal className="size-4" /> Filters{active ? <span className="num rounded-full bg-accent px-1.5 text-[11px] text-white">{active}</span> : null}
          </button>
        </div>
        {showFilters && (
          <div className="mt-4 grid grid-cols-2 gap-3 border-t border-line pt-4 md:grid-cols-4">
            <label><span className="label">Supplier</span><input className="input mt-1" value={f.supplier} onChange={(e) => setF({ ...f, supplier: e.target.value })} /></label>
            <label><span className="label">Currency</span><input className="input mt-1 uppercase" maxLength={3} placeholder="GBP" value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} /></label>
            <label><span className="label">Status</span>
              <select className="input mt-1" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
                <option value="">Any</option><option value="ok">Validated</option><option value="needs_review">Needs review</option><option value="corrected">Corrected</option><option value="approved">Approved</option>
              </select></label>
            <label><span className="label">Source type</span>
              <select className="input mt-1" value={f.file_type} onChange={(e) => setF({ ...f, file_type: e.target.value })}>
                <option value="">Any</option><option value="pdf">PDF</option><option value="image">Image</option><option value="xlsx">Excel</option><option value="xls">Excel 97</option><option value="csv">CSV</option>
              </select></label>
            <label><span className="label">Date from</span><input type="date" className="input mt-1" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} /></label>
            <label><span className="label">Date to</span><input type="date" className="input mt-1" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} /></label>
            <label><span className="label">Min total</span><input inputMode="decimal" className="input num mt-1" value={f.min_total} onChange={(e) => setF({ ...f, min_total: e.target.value })} /></label>
            <label><span className="label">Max total</span><input inputMode="decimal" className="input num mt-1" value={f.max_total} onChange={(e) => setF({ ...f, max_total: e.target.value })} /></label>
            {active > 0 && <button className="btn-ghost col-span-2 justify-start md:col-span-4" onClick={() => setF(EMPTY)}><X className="size-4" /> Clear filters</button>}
          </div>
        )}
      </div>

      <div className="mt-5">
        {isLoading && !data ? <div className="space-y-3">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-20" />)}</div>
          : !data?.length ? <div className="card"><Empty icon={<SearchIcon className="size-5" />} title={q || active ? "No matching invoices" : "No invoices yet"}>{q ? "Try different words, switch to Semantic, or remove filters." : "Upload documents to build the searchable archive."}</Empty></div>
          : (
            <>
              <div className="mb-2 text-[12.5px] text-muted">{data.length} result{data.length > 1 && "s"}{mode === "hybrid" && q ? " · exact keyword matches first, then similar invoices" : mode === "semantic" && q ? " · most similar first" : ""}</div>
              <ul className="space-y-2.5">
                {data.map((h) => (
                  <li key={h.invoice.id}>
                    <Link href={`/invoices/${h.invoice.id}`} className="card flex items-center gap-5 px-5 py-4 transition-colors hover:border-line-strong hover:bg-paper">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium">{h.invoice.supplier_name ?? "Unknown supplier"}</span>
                          <span className="num text-[13px] text-muted">#{h.invoice.invoice_number ?? "—"}</span>
                          <InvStatusBadge status={h.invoice.status} />
                        </div>
                        <div className="mt-1 flex items-center gap-1.5 text-[12.5px] text-muted">
                          <FileTypeIcon type={h.document_filename.split(".").pop()?.toLowerCase() === "pdf" ? "pdf" : /xlsx?|csv/i.test(h.document_filename) ? "csv" : "image"} className="size-3.5" />
                          <span className="truncate">{h.document_filename}</span> · {date(h.invoice.invoice_date)}{h.invoice.customer_name ? ` · billed to ${h.invoice.customer_name}` : ""}
                        </div>
                        {h.snippet && <p className="mt-1.5 line-clamp-2 text-[12.5px] text-ink-2">…{h.snippet}…</p>}
                      </div>
                      <div className="text-right">
                        <div className="num text-[15px] font-semibold">{money(h.invoice.total, h.invoice.currency)}</div>
                        {q && h.matched_by.length > 0 && (
                          <div className="mt-1 flex justify-end gap-1">
                            {h.matched_by.map((m) => (
                              <span key={m} className={m === "keyword" ? "rounded bg-ink px-1.5 py-px text-[10.5px] font-medium text-paper" : "rounded bg-accent-soft px-1.5 py-px text-[10.5px] font-medium text-accent-ink"}
                                title={m === "semantic" ? `Similarity ${(h.score % 1).toFixed(2)}` : "Contains all search words"}>{m}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            </>
          )}
      </div>
    </>
  );
}
