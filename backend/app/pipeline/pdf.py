"""PDF text extraction for 1-5000 page files.

* Digital pages: text layer via PDFium (fast, ~ms per page).
* Scanned pages (little/no text layer): rendered at OCR_DPI and OCR'd.
* Large files: page ranges are processed in parallel worker processes, each
  opening the PDF itself (PDFium handles are not shareable across processes),
  with a progress callback after every chunk.
"""
from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Callable

import pypdfium2 as pdfium

from ..config import settings
from . import PageResult, PipelineError
from .ocr import ocr_image

log = logging.getLogger(__name__)
CHUNK = 25  # pages per unit of parallel work


def _open(path: str) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(path)
    except pdfium.PdfiumError as e:
        msg = str(e).lower()
        if "password" in msg:
            raise PipelineError("encrypted_pdf", "The PDF is password-protected.") from e
        raise PipelineError("corrupted_pdf", f"The PDF could not be opened: {e}") from e


def page_count(path: str) -> int:
    pdf = _open(path)
    try:
        return len(pdf)
    finally:
        pdf.close()


def _extract_range(path: str, start: int, end: int) -> list[PageResult]:
    """Runs inside a worker process. Pages are 0-based [start, end)."""
    pdf = _open(path)
    out: list[PageResult] = []
    try:
        for i in range(start, end):
            try:
                page = pdf[i]
                tp = page.get_textpage()
                text = tp.get_text_range() or ""
                tp.close()
                text = text.replace("\r\n", "\n").replace("\r", "\n")
                if len(text.strip()) >= settings.min_text_chars:
                    out.append(PageResult(i + 1, text, "text"))
                else:
                    img = page.render(scale=settings.ocr_dpi / 72).to_pil()
                    ocr_text, conf = ocr_image(img)
                    out.append(PageResult(i + 1, ocr_text, "ocr", conf))
                page.close()
            except PipelineError:
                raise
            except Exception as e:  # one broken page must not sink a 1000-page file
                log.warning("page %s failed: %s", i + 1, e)
                out.append(PageResult(i + 1, "", "text", warning=f"Page {i + 1} could not be read: {e}"))
    finally:
        pdf.close()
    return out


def extract_pdf(path: str, progress: Callable[[int, int], None] | None = None) -> list[PageResult]:
    n = page_count(path)
    if n == 0:
        raise PipelineError("empty_pdf", "The PDF has no pages.")
    if n > settings.max_pdf_pages:
        raise PipelineError("too_many_pages", f"PDF has {n} pages; the limit is {settings.max_pdf_pages}.")

    ranges = [(s, min(s + CHUNK, n)) for s in range(0, n, CHUNK)]
    results: list[PageResult] = []
    done = 0
    workers = min(settings.ocr_workers, len(ranges))
    if workers <= 1:
        for s, e in ranges:
            results.extend(_extract_range(path, s, e))
            done += e - s
            if progress:
                progress(done, n)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_extract_range, path, s, e): (s, e) for s, e in ranges}
            for fut in as_completed(futs):
                s, e = futs[fut]
                results.extend(fut.result())
                done += e - s
                if progress:
                    progress(done, n)
    results.sort(key=lambda p: p.page_number)
    return results


def render_page_png(path: str, page_number: int, dpi: int = 110) -> bytes:
    import io

    pdf = _open(path)
    try:
        if not 1 <= page_number <= len(pdf):
            raise PipelineError("page_out_of_range", "Page does not exist.")
        img = pdf[page_number - 1].render(scale=dpi / 72).to_pil()
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    finally:
        pdf.close()
