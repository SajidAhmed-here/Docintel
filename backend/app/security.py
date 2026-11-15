from __future__ import annotations

import hmac
import re
import threading
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request, status

from .config import settings


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """If API_KEY is set, every /api route requires the X-API-Key header."""
    if not settings.api_key:
        return
    if not x_api_key or not hmac.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API key.")


class RateLimiter:
    """Sliding-window limiter per client IP (single process; use Redis in a multi-node deployment)."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def __call__(self, request: Request) -> None:
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with self.lock:
            q = self.hits[ip]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many uploads. Try again in a minute.")
            q.append(now)


upload_limiter = RateLimiter(settings.uploads_per_minute)


def safe_filename(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w.\- ()]", "_", name).strip(" .")
    return (name or "upload")[:200]


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-site",
}
