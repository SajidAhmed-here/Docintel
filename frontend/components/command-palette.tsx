"use client";

import clsx from "clsx";
import { ClipboardCheck, CornerDownLeft, FileText, Files, GitCompareArrows, LayoutGrid, Receipt, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { qs, type DocumentOut, type Paged, type SearchHit } from "@/lib/api";
import { money } from "@/lib/format";

const EVENT = "docintel:palette";
export const openCommandPalette = () => window.dispatchEvent(new Event(EVENT));

type Item = { id: string; group: string; label: string; hint?: string; icon: typeof Search; href: string };

const PAGES: Item[] = [
  { id: "p-/", group: "Go to", label: "Overview", icon: LayoutGrid, href: "/" },
  { id: "p-docs", group: "Go to", label: "Documents", icon: Files, href: "/documents" },
  { id: "p-review", group: "Go to", label: "Review queue", icon: ClipboardCheck, href: "/review" },
  { id: "p-search", group: "Go to", label: "Search", icon: Search, href: "/search" },
  { id: "p-recon", group: "Go to", label: "Cross-verify two documents", icon: GitCompareArrows, href: "/reconcile" },
];

function useDebounced<T>(v: T, ms = 200) {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const dq = useDebounced(q.trim());

  useEffect(() => {
    const show = () => { setOpen(true); setQ(""); setSel(0); };
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); show(); }
    };
    window.addEventListener(EVENT, show);
    document.addEventListener("keydown", onKey);
    return () => { window.removeEventListener(EVENT, show); document.removeEventListener("keydown", onKey); };
  }, []);
  useEffect(() => { if (open) setTimeout(() => input.current?.focus(), 0); }, [open]);

  const hits = useSWR<SearchHit[]>(open && dq.length > 1 ? `/search${qs({ q: dq, mode: "hybrid", limit: 6 })}` : null);
  const docs = useSWR<Paged<DocumentOut>>(open && dq.length > 1 ? `/documents${qs({ q: dq, page_size: 4 })}` : null);

  const items = useMemo<Item[]>(() => {
    const ql = dq.toLowerCase();
    const pages = PAGES.filter((p) => !ql || p.label.toLowerCase().includes(ql));
    const inv: Item[] = (hits.data ?? []).map((h) => ({
      id: `i-${h.invoice.id}`, group: "Invoices", icon: Receipt, href: `/invoices/${h.invoice.id}`,
      label: `${h.invoice.supplier_name ?? "Unknown supplier"} · #${h.invoice.invoice_number ?? "no number"}`,
      hint: money(h.invoice.total, h.invoice.currency),
    }));
    const dd: Item[] = (docs.data?.items ?? []).map((d) => ({
      id: `d-${d.id}`, group: "Documents", icon: FileText, href: `/documents/${d.id}`, label: d.original_filename,
      hint: `${d.invoice_count} invoice${d.invoice_count === 1 ? "" : "s"}`,
    }));
    const all = [...pages, ...inv, ...dd];
    if (dq.length > 1) all.push({ id: "s-full", group: "Search", icon: Search, href: `/search?q=${encodeURIComponent(dq)}`, label: `Search everything for “${dq}”` });
    return all;
  }, [dq, hits.data, docs.data]);

  useEffect(() => setSel(0), [dq]);

  function go(item: Item | undefined) {
    if (!item) return;
    setOpen(false);
    router.push(item.href);
  }

  if (!open) return null;
  let lastGroup = "";
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[12vh]" role="dialog" aria-modal="true" aria-label="Jump to">
      <div className="absolute inset-0 bg-ink/40" onClick={() => setOpen(false)} />
      <div className="pop-in relative w-full max-w-[560px] overflow-hidden rounded-xl border border-line bg-surface" style={{ boxShadow: "var(--shadow-pop)" }}>
        <div className="flex items-center gap-3 border-b border-line px-4">
          <Search className="size-[18px] shrink-0 text-faint" />
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Invoice number, supplier, file name, or a page…"
            className="h-12 w-full bg-transparent text-[15px] outline-none placeholder:text-faint" aria-label="Search"
            onKeyDown={(e) => {
              if (e.key === "Escape") setOpen(false);
              else if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(items.length - 1, s + 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
              else if (e.key === "Enter") { e.preventDefault(); go(items[sel]); }
            }} />
          <kbd className="num shrink-0 rounded border border-line px-1.5 text-[11px] text-muted">Esc</kbd>
        </div>
        <ul className="max-h-[52vh] overflow-y-auto p-1.5" role="listbox">
          {items.map((it, i) => {
            const header = it.group !== lastGroup ? (lastGroup = it.group) : null;
            const Icon = it.icon;
            return (
              <li key={it.id} role="option" aria-selected={i === sel}>
                {header && <div className="px-2.5 pb-1 pt-2.5 text-[11.5px] font-medium text-muted">{header}</div>}
                <button onMouseMove={() => setSel(i)} onClick={() => go(it)}
                  className={clsx("flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left text-[13.5px]", i === sel ? "bg-accent-soft text-accent-ink" : "text-ink")}>
                  <Icon className={clsx("size-4 shrink-0", i === sel ? "text-accent" : "text-muted")} />
                  <span className="min-w-0 flex-1 truncate">{it.label}</span>
                  {it.hint && <span className="num shrink-0 text-[12px] text-muted">{it.hint}</span>}
                  {i === sel && <CornerDownLeft className="size-3.5 shrink-0 text-accent" />}
                </button>
              </li>
            );
          })}
          {!items.length && <li className="px-3 py-6 text-center text-[13px] text-muted">No matches. Try a supplier name or invoice number.</li>}
        </ul>
      </div>
    </div>
  );
}
