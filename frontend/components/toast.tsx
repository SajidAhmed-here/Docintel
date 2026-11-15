"use client";

import clsx from "clsx";
import { CheckCircle2, Info, X, XCircle } from "lucide-react";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

type Tone = "ok" | "bad" | "info";
interface Toast { id: number; tone: Tone; title: string; body?: string }

const Ctx = createContext<(t: Omit<Toast, "id">) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { ...t, id }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), t.tone === "bad" ? 7000 : 4000);
  }, []);
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-50 flex w-[360px] flex-col gap-2" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className="pointer-events-auto flex gap-3 rounded-xl border border-line bg-surface p-3.5" style={{ boxShadow: "var(--shadow-pop)" }}>
            {t.tone === "ok" ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-ok" /> : t.tone === "bad" ? <XCircle className="mt-0.5 size-4 shrink-0 text-bad" /> : <Info className="mt-0.5 size-4 shrink-0 text-info" />}
            <div className="min-w-0 flex-1">
              <div className={clsx("text-[13.5px] font-medium")}>{t.title}</div>
              {t.body && <div className="mt-0.5 text-[12.5px] text-muted">{t.body}</div>}
            </div>
            <button aria-label="Dismiss" onClick={() => setItems((xs) => xs.filter((x) => x.id !== t.id))} className="text-faint hover:text-ink">
              <X className="size-4" />
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);
