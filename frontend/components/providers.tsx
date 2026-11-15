"use client";

import type { ReactNode } from "react";
import { SWRConfig } from "swr";
import { fetcher } from "@/lib/api";
import { ToastProvider } from "./toast";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <SWRConfig value={{ fetcher, revalidateOnFocus: true, keepPreviousData: true, errorRetryCount: 2 }}>
      <ToastProvider>{children}</ToastProvider>
    </SWRConfig>
  );
}
