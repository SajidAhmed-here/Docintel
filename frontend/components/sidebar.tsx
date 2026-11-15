"use client";

import clsx from "clsx";
import { ClipboardCheck, Files, GitCompareArrows, LayoutGrid, Menu, Monitor, Moon, Search, Sparkles, Sun, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { fetcher, type Stats } from "@/lib/api";
import { openCommandPalette } from "./command-palette";

export const NAV = [
  { href: "/", label: "Overview", icon: LayoutGrid },
  { href: "/documents", label: "Documents", icon: Files },
  { href: "/review", label: "Review queue", icon: ClipboardCheck, badge: true },
  { href: "/search", label: "Search", icon: Search },
  { href: "/reconcile", label: "Cross-verify", icon: GitCompareArrows },
];

const PROVIDER_NAMES: Record<string, string> = { gemini: "Google Gemini", groq: "Groq", openai: "OpenAI", openrouter: "OpenRouter", xai: "xAI" };

function Logo() {
  return (
    <Link href="/" className="flex items-center gap-2.5" aria-label="DocIntel home">
      <div className="grid size-8 place-items-center rounded-lg bg-accent text-white">
        <svg viewBox="0 0 24 24" className="size-[18px]" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M6 3h8l4 4v14H6z" /><path d="M14 3v4h4" /><path d="m9 14 2 2 4-4" />
        </svg>
      </div>
      <div>
        <div className="text-[15px] font-semibold leading-none tracking-tight">DocIntel</div>
        <div className="mt-1 text-[11.5px] text-muted">Invoice intelligence</div>
      </div>
    </Link>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const path = usePathname();
  const { data } = useSWR<Stats>("/stats", fetcher, { refreshInterval: 5000 });
  return (
    <nav className="space-y-0.5" aria-label="Main">
      {NAV.map(({ href, label, icon: Icon, badge }) => {
        const active = href === "/" ? path === "/" : path.startsWith(href) || (href === "/documents" && path.startsWith("/invoices"));
        return (
          <Link key={href} href={href} onClick={onNavigate} aria-current={active ? "page" : undefined}
            className={clsx("relative flex h-9 items-center gap-2.5 rounded-lg px-3 text-[13.5px] font-medium transition-colors",
              active ? "bg-accent-soft text-accent-ink" : "text-ink-2 hover:bg-sunken")}>
            {active && <span className="absolute -left-3 top-1.5 bottom-1.5 w-[3px] rounded-r bg-accent" aria-hidden />}
            <Icon className={clsx("size-4", active ? "text-accent" : "text-muted")} />
            <span className="flex-1">{label}</span>
            {badge && !!data && data.needs_review + (data.documents_without_invoices ?? 0) > 0 && (
              <span className="num rounded-full bg-warn px-1.5 py-px text-[11px] font-semibold text-white">{data.needs_review + (data.documents_without_invoices ?? 0)}</span>
            )}
          </Link>
        );
      })}
    </nav>
  );
}

type ThemePref = "system" | "light" | "dark";

function applyTheme(pref: ThemePref) {
  const dark = pref === "dark" || (pref === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

export function ThemeToggle() {
  const [pref, setPref] = useState<ThemePref>("system");
  useEffect(() => {
    let saved: string | null = null;
    try { saved = localStorage.getItem("docintel-theme"); } catch { /* storage blocked */ }
    const p: ThemePref = saved === "light" || saved === "dark" ? saved : "system";
    setPref(p);
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => { if (p === "system") applyTheme("system"); };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  function choose(p: ThemePref) {
    setPref(p);
    try { if (p === "system") localStorage.removeItem("docintel-theme"); else localStorage.setItem("docintel-theme", p); } catch { /* ignore */ }
    applyTheme(p);
  }

  const opts: { v: ThemePref; icon: typeof Sun; label: string }[] = [
    { v: "light", icon: Sun, label: "Light theme" },
    { v: "system", icon: Monitor, label: "Match system theme" },
    { v: "dark", icon: Moon, label: "Dark theme" },
  ];
  return (
    <div className="flex rounded-lg border border-line bg-paper p-0.5" role="radiogroup" aria-label="Theme">
      {opts.map(({ v, icon: Icon, label }) => (
        <button key={v} role="radio" aria-checked={pref === v} aria-label={label} title={label} onClick={() => choose(v)}
          className={clsx("grid h-7 flex-1 place-items-center rounded-md transition-colors",
            pref === v ? "bg-surface text-ink shadow-[var(--shadow-card)]" : "text-muted hover:text-ink")}>
          <Icon className="size-3.5" />
        </button>
      ))}
    </div>
  );
}

function EngineCard() {
  const { data } = useSWR<Stats>("/stats", fetcher, { refreshInterval: 10000 });
  const on = data?.llm.enabled;
  return (
    <div className="rounded-lg border border-line bg-paper p-3 text-[12px]">
      <div className="flex items-center gap-1.5 font-medium text-ink-2">
        <Sparkles className="size-3.5 text-accent" /> Extraction engine
        <span className={clsx("ml-auto size-1.5 rounded-full", data ? (on ? "bg-ok" : "bg-faint") : "bg-line-strong")}
          title={on ? "AI extraction on" : "AI extraction off"} />
      </div>
      <div className="mt-1 leading-snug text-muted">
        {!data ? "Connecting…" : on ? (
          <>{PROVIDER_NAMES[data.llm.provider ?? ""] ?? data.llm.provider}<br /><span className="num text-[11.5px] text-ink-2">{data.llm.model}</span></>
        ) : "Rule-based only. Add an LLM key to .env to turn on AI extraction."}
      </div>
    </div>
  );
}

function SearchButton() {
  return (
    <button onClick={openCommandPalette}
      className="flex h-9 w-full items-center gap-2 rounded-lg border border-line bg-paper px-3 text-[13px] text-muted transition-colors hover:border-line-strong hover:text-ink">
      <Search className="size-4" />
      <span className="flex-1 text-left">Jump to…</span>
      <kbd className="num rounded border border-line bg-surface px-1.5 text-[11px]">Ctrl K</kbd>
    </button>
  );
}

export function Sidebar() {
  return (
    <aside className="sticky top-0 hidden h-screen w-[240px] shrink-0 flex-col border-r border-line bg-surface md:flex">
      <div className="flex h-16 items-center px-5"><Logo /></div>
      <div className="px-3 pb-3"><SearchButton /></div>
      <div className="flex-1 px-3"><NavLinks /></div>
      <div className="space-y-2 p-3">
        <EngineCard />
        <ThemeToggle />
      </div>
    </aside>
  );
}

export function MobileNav() {
  const [open, setOpen] = useState(false);
  const path = usePathname();
  useEffect(() => setOpen(false), [path]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = ""; };
  }, [open]);

  return (
    <>
      <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-line bg-surface/90 px-4 backdrop-blur md:hidden">
        <Logo />
        <div className="flex items-center gap-1">
          <button className="btn-ghost h-9 px-2.5" onClick={openCommandPalette} aria-label="Search and jump"><Search className="size-5" /></button>
          <button className="btn-ghost h-9 px-2.5" onClick={() => setOpen(true)} aria-label="Open menu" aria-expanded={open}><Menu className="size-5" /></button>
        </div>
      </header>
      {open && (
        <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true" aria-label="Menu">
          <div className="absolute inset-0 bg-ink/40" onClick={() => setOpen(false)} />
          <div className="pop-in absolute right-0 top-0 flex h-full w-[min(300px,85vw)] flex-col border-l border-line bg-surface">
            <div className="flex h-14 items-center justify-between px-4">
              <span className="font-semibold">Menu</span>
              <button className="btn-ghost h-9 px-2.5" onClick={() => setOpen(false)} aria-label="Close menu"><X className="size-5" /></button>
            </div>
            <div className="flex-1 px-3 pl-6"><NavLinks onNavigate={() => setOpen(false)} /></div>
            <div className="space-y-2 p-3"><EngineCard /><ThemeToggle /></div>
          </div>
        </div>
      )}
    </>
  );
}
