"use client";

import clsx from "clsx";
import { AlertTriangle, CheckCircle2, CircleDashed, FileImage, FileSpreadsheet, FileText, Loader2, PencilLine, ShieldCheck, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import type { DocStatus, InvStatus } from "@/lib/api";

const DOC_STATUS: Record<DocStatus, { label: string; cls: string; icon: ReactNode }> = {
  queued: { label: "Queued", cls: "bg-sunken text-ink-2", icon: <CircleDashed className="size-3.5" /> },
  processing: { label: "Processing", cls: "bg-info-soft text-info", icon: <Loader2 className="size-3.5 animate-spin" /> },
  completed: { label: "Completed", cls: "bg-ok-soft text-ok", icon: <CheckCircle2 className="size-3.5" /> },
  needs_review: { label: "Needs review", cls: "bg-warn-soft text-warn", icon: <AlertTriangle className="size-3.5" /> },
  failed: { label: "Failed", cls: "bg-bad-soft text-bad", icon: <XCircle className="size-3.5" /> },
};

const INV_STATUS: Record<InvStatus, { label: string; cls: string; icon: ReactNode }> = {
  ok: { label: "Validated", cls: "bg-ok-soft text-ok", icon: <CheckCircle2 className="size-3.5" /> },
  needs_review: { label: "Needs review", cls: "bg-warn-soft text-warn", icon: <AlertTriangle className="size-3.5" /> },
  corrected: { label: "Corrected", cls: "bg-info-soft text-info", icon: <PencilLine className="size-3.5" /> },
  approved: { label: "Approved", cls: "bg-accent-soft text-accent", icon: <ShieldCheck className="size-3.5" /> },
};

function Pill({ cls, icon, children }: { cls: string; icon?: ReactNode; children: ReactNode }) {
  return (
    <span className={clsx("inline-flex h-6 items-center gap-1.5 rounded-full px-2.5 text-[12px] font-medium whitespace-nowrap", cls)}>
      {icon}
      {children}
    </span>
  );
}

export function DocStatusBadge({ status }: { status: DocStatus }) {
  const s = DOC_STATUS[status] ?? DOC_STATUS.queued;
  return <Pill cls={s.cls} icon={s.icon}>{s.label}</Pill>;
}

export function InvStatusBadge({ status }: { status: InvStatus }) {
  const s = INV_STATUS[status] ?? INV_STATUS.ok;
  return <Pill cls={s.cls} icon={s.icon}>{s.label}</Pill>;
}

export function SeverityPill({ severity, children }: { severity: "error" | "warning"; children: ReactNode }) {
  return <Pill cls={severity === "error" ? "bg-bad-soft text-bad" : "bg-warn-soft text-warn"}>{children}</Pill>;
}

export function Progress({ value, status }: { value: number; status: DocStatus }) {
  const live = status === "processing" || status === "queued";
  const color = status === "failed" ? "bg-bad" : status === "needs_review" ? "bg-warn" : status === "completed" ? "bg-ok" : "bg-accent";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-sunken" role="progressbar" aria-valuenow={value} aria-valuemin={0} aria-valuemax={100}>
      <div className={clsx("h-full rounded-full transition-[width] duration-500", color, live && "progress-live")} style={{ width: `${Math.max(live ? 4 : 0, value)}%` }} />
    </div>
  );
}

export function FileTypeIcon({ type, className }: { type: string; className?: string }) {
  const cls = clsx("shrink-0", className ?? "size-4");
  if (type === "image") return <FileImage className={clsx(cls, "text-violet")} />;
  if (type === "xlsx" || type === "xls" || type === "csv") return <FileSpreadsheet className={clsx(cls, "text-ok")} />;
  return <FileText className={clsx(cls, "text-bad")} />;
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "warn" | "ok" | "bad" }) {
  return (
    <div className="card px-5 py-4">
      <div className="label">{label}</div>
      <div className={clsx("mt-1.5 text-[26px] font-semibold leading-none tracking-tight tabular-nums",
        tone === "warn" && "text-warn", tone === "ok" && "text-ok", tone === "bad" && "text-bad")}>{value}</div>
      {hint && <div className="mt-2 text-[12.5px] text-muted">{hint}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("skeleton", className)} />;
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      {icon && <div className="mb-3 grid size-11 place-items-center rounded-full bg-sunken text-muted">{icon}</div>}
      <div className="font-medium text-ink">{title}</div>
      {children && <div className="mt-1 max-w-sm text-[13px] text-muted">{children}</div>}
    </div>
  );
}

export function PageHeader({ title, description, actions, eyebrow }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <div className="mb-1.5 text-[12.5px] text-muted">{eyebrow}</div>}
        <h1 className="truncate text-[22px] font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-[13.5px] text-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

export function ConfidenceDot({ value }: { value: number | undefined }) {
  if (value === undefined) return null;
  const pct = Math.round(value * 100);
  const cls = value >= 0.85 ? "bg-ok" : value >= 0.6 ? "bg-warn" : "bg-bad";
  return (
    <span className="inline-flex items-center gap-1.5 text-[11.5px] text-muted" title={`Extraction confidence ${pct}%`}>
      <span className={clsx("size-1.5 rounded-full", cls)} />
      <span className="num">{pct}%</span>
    </span>
  );
}

export function Segmented<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: { value: T; label: string }[] }) {
  return (
    <div className="inline-flex rounded-lg border border-line-strong bg-surface p-0.5" role="tablist">
      {options.map((o) => (
        <button key={o.value} role="tab" aria-selected={value === o.value} onClick={() => onChange(o.value)}
          className={clsx("h-8 rounded-md px-3 text-[13px] font-medium transition-colors",
            value === o.value ? "bg-ink text-paper" : "text-ink-2 hover:bg-sunken")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
