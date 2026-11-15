"""Identify file type from content (magic bytes), not from the filename alone.

A file whose extension disagrees with its content is rejected, so a renamed
executable cannot be pushed into the OCR or spreadsheet parsers.
"""
from __future__ import annotations

from pathlib import Path

from . import PipelineError

ALLOWED_EXTENSIONS = {
    ".pdf": "pdf",
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".tif": "image", ".tiff": "image",
    ".xlsx": "xlsx", ".xls": "xls", ".csv": "csv",
}

_MAGIC = [
    (b"%PDF-", "pdf"),
    (b"\xff\xd8\xff", "image"),
    (b"\x89PNG\r\n\x1a\n", "image"),
    (b"II*\x00", "image"),
    (b"MM\x00*", "image"),
    (b"PK\x03\x04", "xlsx"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "xls"),  # OLE2 compound file (legacy Excel)
]


def sniff(head: bytes) -> str | None:
    # PDFs may have junk before the header; the spec allows it within 1 KB.
    if b"%PDF-" in head[:1024]:
        return "pdf"
    for magic, kind in _MAGIC:
        if head.startswith(magic):
            return kind
    return None


def _looks_like_text(head: bytes) -> bool:
    if not head:
        return False
    if b"\x00" in head:
        return False
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            head.decode(enc)
            return True
        except UnicodeDecodeError:
            continue
    return False


def detect_file_type(filename: str, head: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise PipelineError("unsupported_type", f"Unsupported file type '{ext or 'none'}'. Allowed: PDF, JPG, PNG, TIFF, XLSX, XLS, CSV.")
    if not head:
        raise PipelineError("empty_file", "The file is empty.")
    expected = ALLOWED_EXTENSIONS[ext]
    if expected == "csv":
        if not _looks_like_text(head):
            raise PipelineError("type_mismatch", "File has a .csv extension but is not a text file.")
        return "csv"
    actual = sniff(head)
    if actual is None:
        raise PipelineError("corrupted_file", f"File content is not a valid {ext[1:].upper()} (unrecognised header).")
    if actual != expected:
        raise PipelineError("type_mismatch", f"File extension is {ext} but the content looks like {actual.upper()}.")
    return actual
