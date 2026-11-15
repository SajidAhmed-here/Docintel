"""Text embeddings for semantic search.

* `fastembed` (production): BAAI/bge-small-en-v1.5, 384-d, runs on CPU via
  ONNX, no API key or GPU needed.
* `hash` (default, offline): feature-hashed word + character-trigram vectors.
  Captures spelling variants and partial matches but not true synonyms; used
  so the system and its tests run with no model download.
"""
from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache

from ..config import settings

DIM = settings.embedding_dim


def _h(token: str) -> tuple[int, float]:
    d = hashlib.blake2b(token.encode(), digest_size=8).digest()
    n = int.from_bytes(d, "little")
    return n % DIM, (1.0 if (n >> 63) & 1 else -1.0)


def hash_embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    words = re.findall(r"[a-z0-9]+", text.lower())
    for w in words:
        i, s = _h("w:" + w)
        vec[i] += 1.0 * s
        padded = f"#{w}#"
        for k in range(len(padded) - 2):
            i, s = _h("c:" + padded[k : k + 3])
            vec[i] += 1.5 * s
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


@lru_cache(maxsize=1)
def _fastembed():
    from fastembed import TextEmbedding

    return TextEmbedding("BAAI/bge-small-en-v1.5")


def embed(texts: list[str]) -> list[list[float]]:
    if settings.embedding_backend == "fastembed":
        return [list(map(float, v)) for v in _fastembed().embed(texts)]
    return [hash_embed(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # vectors are L2-normalised


def chunk(text: str, size: int = 800, overlap: int = 120) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) <= size:
        return [text] if text else []
    out, i = [], 0
    while i < len(text):
        out.append(text[i : i + size])
        i += size - overlap
    return out
