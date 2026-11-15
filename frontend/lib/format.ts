const ISO = /^[A-Z]{3}$/;

export function money(v: string | number | null | undefined, currency?: string | null) {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return String(v);
  if (currency && ISO.test(currency)) {
    try {
      return new Intl.NumberFormat("en-GB", { style: "currency", currency, currencyDisplay: "narrowSymbol" }).format(n);
    } catch { /* unknown code */ }
  }
  return new Intl.NumberFormat("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n);
}

export function number(v: number | string | null | undefined, digits = 0) {
  if (v === null || v === undefined || v === "") return "—";
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: digits }).format(Number(v));
}

export function date(v: string | null | undefined) {
  if (!v) return "—";
  const d = new Date(v.length === 10 ? `${v}T00:00:00` : v);
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

export function relTime(v: string | null | undefined) {
  if (!v) return "—";
  const s = (Date.now() - new Date(v).getTime()) / 1000;
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return date(v);
}

export function bytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 ** 2).toFixed(1)} MB`;
}

export function duration(ms: number | null | undefined) {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

export const FIELD_LABELS: Record<string, string> = {
  invoice_number: "Invoice number",
  invoice_date: "Invoice date",
  supplier_name: "Supplier",
  customer_name: "Customer",
  subtotal: "Subtotal",
  tax: "Tax",
  total: "Total",
  currency: "Currency",
  line_items: "Line items",
  line_items_sum: "Line items sum",
  line_item_count: "Line item count",
  status: "Status",
};

export const ISSUE_LABELS: Record<string, string> = {
  MISSING_FIELD: "Missing field",
  TOTAL_MISMATCH: "Total doesn't add up",
  TOTAL_UNEXPLAINED: "Total unexplained",
  LINE_ITEM_MATH: "Line item maths",
  LINE_ITEMS_SUM_MISMATCH: "Line items ≠ subtotal",
  DUPLICATE_INVOICE: "Duplicate invoice",
  NEGATIVE_AMOUNT: "Negative amount",
  ZERO_TOTAL: "Zero total",
  SUBTOTAL_EXCEEDS_TOTAL: "Subtotal > total",
  TAX_RATE_UNUSUAL: "Unusual tax rate",
  INVALID_CURRENCY: "Invalid currency",
  DATE_IN_FUTURE: "Future date",
  DATE_IMPLAUSIBLE: "Implausible date",
  LOW_CONFIDENCE: "Low confidence",
  NO_LINE_ITEMS: "No line items",
  UNGROUNDED_VALUE: "Unverified value removed",
  LLM_FALLBACK: "AI unavailable",
};

export const FILE_TYPE_LABEL: Record<string, string> = { pdf: "PDF", image: "Image", xlsx: "Excel", xls: "Excel 97", csv: "CSV" };
