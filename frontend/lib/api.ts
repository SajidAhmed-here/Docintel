export type DocStatus = "queued" | "processing" | "completed" | "needs_review" | "failed";
export type InvStatus = "ok" | "needs_review" | "corrected" | "approved";

export interface DocumentOut {
  id: string;
  original_filename: string;
  file_type: "pdf" | "image" | "xlsx" | "xls" | "csv";
  size_bytes: number;
  status: DocStatus;
  stage: string | null;
  progress: number;
  page_count: number | null;
  ocr_pages: number;
  error_code: string | null;
  error_message: string | null;
  attempts: number;
  duplicate_of: string | null;
  processing_ms: number | null;
  created_at: string;
  finished_at: string | null;
  invoice_count: number;
  review_count: number;
}

export interface InvoiceSummary {
  id: string;
  document_id: string;
  segment_index: number;
  page_start: number | null;
  page_end: number | null;
  source_ref: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  supplier_name: string | null;
  customer_name: string | null;
  subtotal: string | null;
  tax: string | null;
  total: string | null;
  currency: string | null;
  status: InvStatus;
  needs_review: boolean;
  extraction_method: string;
  error_count: number;
  warning_count: number;
  document_filename?: string | null;
}

export interface Issue { code: string; severity: "error" | "warning"; field: string | null; message: string; details?: Record<string, unknown> | null }
export interface LineItem { position: number; description: string | null; quantity: string | null; unit_price: string | null; amount: string | null }
export interface Correction { field: string; old_value: string | null; new_value: string | null; corrected_by: string | null; created_at: string }
export interface Event { attempt: number; stage: string; status: string; message: string | null; duration_ms: number | null; created_at: string }
export interface PageMeta { page_number: number; sheet_name: string | null; method: string; ocr_confidence: number | null; chars: number }

export interface InvoiceDetail extends InvoiceSummary {
  model: string | null;
  field_confidence: Record<string, number>;
  line_items: LineItem[];
  issues: Issue[];
  corrections: Correction[];
  notes: string[];
  document_filename: string | null;
}

export interface DocumentDetail extends DocumentOut {
  invoices: InvoiceSummary[];
  events: Event[];
  pages: PageMeta[];
}

export interface Paged<T> { items: T[]; total: number; page: number; page_size: number }
export interface SearchHit { score: number; snippet: string | null; matched_by: ("keyword" | "semantic")[]; invoice: InvoiceSummary; document_filename: string }

export interface Stats {
  documents: number;
  documents_by_status: Record<string, number>;
  invoices: number;
  needs_review: number;
  documents_without_invoices: number;
  pages_processed: number;
  ocr_pages: number;
  avg_processing_ms: number | null;
  success_rate: number | null;
  by_currency: { currency: string; count: number; total: string }[];
  top_issues: { code: string; count: number }[];
  by_method: Record<string, number>;
  llm: { enabled: boolean; provider: string | null; model: string | null };
}

export interface ReconcileResult {
  document_a: { id: string; filename: string; file_type: string; invoices: number };
  document_b: { id: string; filename: string; file_type: string; invoices: number };
  summary: { matched: number; mismatched: number; only_in_a: number; only_in_b: number };
  pairs: { invoice_number: string; a_invoice_id: string; b_invoice_id: string; status: "match" | "mismatch"; differences: { field: string; a: string | null; b: string | null; severity: string }[] }[];
  only_in_a: { invoice_id: string; invoice_number: string | null; supplier_name: string | null; total: string | null; currency: string | null }[];
  only_in_b: { invoice_id: string; invoice_number: string | null; supplier_name: string | null; total: string | null; currency: string | null }[];
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function parse(res: Response) {
  if (res.status === 204) return null;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const d = body?.detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? d.map((x: { msg: string }) => x.msg).join("; ") : `Request failed (${res.status})`;
    throw new ApiError(res.status, msg);
  }
  return body;
}

export async function api<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, { ...init, headers: { ...(init?.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}), ...init?.headers } });
  return parse(res) as Promise<T>;
}

export const fetcher = <T,>(path: string) => api<T>(path);

export function qs(params: Record<string, string | number | boolean | null | undefined>) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export interface UploadResult { filename: string; accepted: boolean; document_id: string | null; duplicate_of: string | null; error: string | null }

/** XHR instead of fetch so we get upload progress events. */
export function uploadFiles(files: File[], onProgress: (pct: number) => void): Promise<UploadResult[]> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/documents");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      let body: unknown = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as UploadResult[]);
      else reject(new ApiError(xhr.status, (body as { detail?: string })?.detail || `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error during upload."));
    xhr.send(form);
  });
}
