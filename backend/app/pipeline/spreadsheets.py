"""XLSX / XLS / CSV parsing.

Each sheet becomes (a) a text page, so search and text-based extraction work
for "form-style" invoice sheets, and (b) a SheetTable with a detected header
row, so tabular exports (one row per invoice or per line item) can be mapped
to invoices without an LLM.
"""
from __future__ import annotations

import csv
import io
import math
from datetime import date, datetime
from typing import Any

import pandas as pd

from . import PageResult, PipelineError, SheetTable

MAX_ROWS = 200_000


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        return f"{v:.10g}" if not v.is_integer() else str(int(v))
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.strftime("%Y-%m-%d") if (v.hour, v.minute, v.second) == (0, 0, 0) else v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v).strip()


def _find_header(rows: list[list[str]]) -> int:
    """Header = first row (in the top 15) with >=2 non-empty mostly-alphabetic cells."""
    for i, r in enumerate(rows[:15]):
        cells = [c for c in r if c]
        alpha = [c for c in cells if any(ch.isalpha() for ch in c)]
        if len(cells) >= 2 and len(alpha) >= max(2, int(0.6 * len(cells))):
            return i
    return 0


def _frame_to_outputs(name: str, df: pd.DataFrame, page_no: int) -> tuple[PageResult, SheetTable]:
    if len(df) > MAX_ROWS:
        raise PipelineError("sheet_too_large", f"Sheet '{name}' has {len(df)} rows; limit is {MAX_ROWS}.")
    rows = [[_cell(v) for v in r] for r in df.itertuples(index=False, name=None)]
    rows = [r for r in rows if any(r)]
    # drop fully empty columns
    if rows:
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        keep = [j for j in range(width) if any(r[j] for r in rows)]
        rows = [[r[j] for j in keep] for r in rows]
    text = "\n".join("\t".join(c for c in r) for r in rows)
    h = _find_header(rows) if rows else 0
    table = SheetTable(name, rows[h] if rows else [], rows[h + 1:] if rows else [], h)
    return PageResult(page_no, text, "spreadsheet", sheet_name=name), table


def extract_excel(path: str, kind: str) -> tuple[list[PageResult], list[SheetTable]]:
    engine = "openpyxl" if kind == "xlsx" else "xlrd"
    try:
        sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object, engine=engine)
    except Exception as e:
        name = type(e).__name__
        if "encrypt" in str(e).lower() or "password" in str(e).lower():
            raise PipelineError("encrypted_spreadsheet", "The workbook is password-protected.") from e
        raise PipelineError("corrupted_spreadsheet", f"The workbook could not be read ({name}: {e}).") from e
    if not sheets:
        raise PipelineError("empty_spreadsheet", "The workbook has no sheets.")
    pages, tables = [], []
    for i, (name, df) in enumerate(sheets.items(), start=1):
        p, t = _frame_to_outputs(str(name), df, i)
        pages.append(p)
        tables.append(t)
    if not any(p.text.strip() for p in pages):
        raise PipelineError("empty_spreadsheet", "All sheets are empty.")
    return pages, tables


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise PipelineError("bad_encoding", "Could not determine the CSV text encoding.")


def extract_csv(path: str) -> tuple[list[PageResult], list[SheetTable]]:
    with open(path, "rb") as f:
        text = _decode(f.read())
    if not text.strip():
        raise PipelineError("empty_file", "The CSV file is empty.")
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        sep = dialect.delimiter
    except csv.Error:
        sep = ","
    try:
        df = pd.read_csv(io.StringIO(text), sep=sep, header=None, dtype=str, keep_default_na=False,
                         on_bad_lines="warn", engine="python")
    except Exception as e:
        raise PipelineError("corrupted_csv", f"The CSV could not be parsed: {e}") from e
    p, t = _frame_to_outputs("CSV", df, 1)
    return [p], [t]
