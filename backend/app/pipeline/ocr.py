"""OCR with image clean-up for phone photos, faxes and low-quality scans.

Strategy: run Tesseract on a cleaned-up (grey, upscaled, deskewed, denoised,
binarised) image; if confidence is still poor, also try the lightly processed
image and keep whichever result Tesseract is more confident in.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageOps

from ..config import settings
from . import PipelineError

TESS_CONFIG = "--oem 1 --psm 6 -c preserve_interword_spaces=1"          # dense block: keeps table rows together
TESS_SPARSE = "--oem 1 --psm 11 -c preserve_interword_spaces=1"         # sparse: finds isolated text the block pass drops
LOW_CONFIDENCE = 70.0

if settings.tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd


def _to_gray(img: Image.Image) -> np.ndarray:
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    arr = np.array(img)
    return arr if arr.ndim == 2 else cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)


def _upscale(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape[:2]
    if w < 1600:  # Tesseract wants ~30px cap height; small images lose characters
        f = min(3.0, 1600 / max(w, 1))
        gray = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    return gray


def _deskew(gray: np.ndarray) -> np.ndarray:
    inv = cv2.bitwise_not(gray)
    thr = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thr > 0))
    if len(coords) < 200:
        return gray
    angle = cv2.minAreaRect(coords)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if angle > 45:
        angle -= 90
    if abs(angle) < 0.3 or abs(angle) > 15:  # skip tiny or implausible corrections
        return gray
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def preprocess(img: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """Returns (light, strong): light = grey+upscale+deskew, strong = + denoise + binarise."""
    gray = _deskew(_upscale(_to_gray(img)))
    den = cv2.fastNlMeansDenoising(gray, None, h=12, templateWindowSize=7, searchWindowSize=21)
    binar = cv2.adaptiveThreshold(den, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)
    return gray, binar


def _run(arr: np.ndarray, config: str = TESS_CONFIG) -> tuple[str, float]:
    data = pytesseract.image_to_data(arr, lang=settings.ocr_lang, config=config, output_type=pytesseract.Output.DICT)
    lines: dict[tuple[int, int, int], list[str]] = {}
    confs: list[float] = []
    for i, word in enumerate(data["text"]):
        word = (word or "").strip()
        conf = float(data["conf"][i])
        if not word or conf < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)
        confs.append(conf)
    text = "\n".join(" ".join(ws) for _, ws in sorted(lines.items()))
    return text, (sum(confs) / len(confs) if confs else 0.0)


def _flat(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _merge_sparse(dense: str, sparse: str) -> str:
    """Append lines the dense pass missed (e.g. right-aligned header fields beside a logo)."""
    have = _flat(dense)
    missing = [l for l in sparse.splitlines() if len(_flat(l)) >= 6 and _flat(l) not in have]
    return dense if not missing else dense + "\n" + "\n".join(missing)


def ocr_image(img: Image.Image) -> tuple[str, float]:
    """OCR one image. Returns (text, mean word confidence 0-100)."""
    try:
        light, strong = preprocess(img)
        text, conf = _run(strong)
        if conf < LOW_CONFIDENCE:
            text2, conf2 = _run(light)
            if conf2 > conf:
                text, conf = text2, conf2
        if settings.ocr_sparse_pass:
            text = _merge_sparse(text, _run(strong, TESS_SPARSE)[0])
        return text, round(conf, 1)
    except pytesseract.TesseractNotFoundError as e:
        raise PipelineError("ocr_unavailable", "Tesseract OCR is not installed on the worker.") from e
    except cv2.error as e:
        raise PipelineError("image_unreadable", f"Image could not be processed: {e}") from e
