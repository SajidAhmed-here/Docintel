from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class PipelineError(Exception):
    """A processing failure with a stable, machine-readable code."""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass
class PageResult:
    page_number: int
    text: str
    method: str  # text | ocr | spreadsheet
    ocr_confidence: float | None = None
    sheet_name: str | None = None
    warning: str | None = None


@dataclass
class SheetTable:
    """A parsed spreadsheet sheet: header row + data rows (all strings)."""

    sheet_name: str
    header: list[str]
    rows: list[list[Any]]
    header_row_index: int = 0


@dataclass
class TextResult:
    pages: list[PageResult] = field(default_factory=list)
    tables: list[SheetTable] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ocr_pages(self) -> int:
        return sum(1 for p in self.pages if p.method == "ocr")
