from __future__ import annotations

from typing import Callable

from PIL import Image, ImageSequence, UnidentifiedImageError

from . import PageResult, PipelineError
from .ocr import ocr_image

Image.MAX_IMAGE_PIXELS = 180_000_000  # guard against decompression bombs


def _open(path: str) -> Image.Image:
    try:
        with Image.open(path) as probe:
            probe.verify()  # catches truncated/corrupt files early
        return Image.open(path)
    except (UnidentifiedImageError, OSError, SyntaxError) as e:
        raise PipelineError("corrupted_image", f"The image could not be read: {e}") from e
    except Image.DecompressionBombError as e:
        raise PipelineError("image_too_large", "The image is too large to process safely.") from e


def extract_image(path: str, progress: Callable[[int, int], None] | None = None) -> list[PageResult]:
    img = _open(path)
    try:
        frames = [f.copy() for f in ImageSequence.Iterator(img)]  # multi-page TIFF
    except OSError as e:
        raise PipelineError("corrupted_image", f"The image is truncated or damaged: {e}") from e
    finally:
        img.close()
    out: list[PageResult] = []
    for i, frame in enumerate(frames, start=1):
        text, conf = ocr_image(frame)
        warn = "Very low OCR confidence; image may be blurry or too small." if conf < 45 else None
        out.append(PageResult(i, text, "ocr", conf, warning=warn))
        if progress:
            progress(i, len(frames))
    return out


def render_image_png(path: str, page_number: int = 1, max_width: int = 1400) -> bytes:
    import io

    img = _open(path)
    try:
        for i, frame in enumerate(ImageSequence.Iterator(img), start=1):
            if i == page_number:
                f = frame.convert("RGB")
                if f.width > max_width:
                    f = f.resize((max_width, int(f.height * max_width / f.width)))
                buf = io.BytesIO()
                f.save(buf, format="PNG", optimize=True)
                return buf.getvalue()
        raise PipelineError("page_out_of_range", "Page does not exist.")
    finally:
        img.close()
