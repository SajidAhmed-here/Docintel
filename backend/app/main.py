from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from .api import documents, invoices, misc
from .config import settings
from .db import init_db
from .security import SECURITY_HEADERS, require_api_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="DocIntel API",
    version="1.0.0",
    description="AI-powered document intelligence: upload invoices/receipts/spreadsheets, extract structured data, validate, search and reconcile.",
    lifespan=lifespan,
)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.allowed_origins, allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-API-Key"], allow_credentials=False,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        resp.headers.setdefault(k, v)
    return resp


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    logging.getLogger("api").exception("unhandled error")
    return JSONResponse({"detail": "Internal server error."}, status_code=500)


api_deps = [Depends(require_api_key)]
app.include_router(documents.router, prefix="/api", dependencies=api_deps)
app.include_router(invoices.router, prefix="/api", dependencies=api_deps)
app.include_router(misc.router, prefix="/api", dependencies=api_deps)


@app.get("/api/health", tags=["health"])
def health():
    return {"status": "ok", "task_mode": settings.task_mode, "llm_enabled": settings.llm_enabled,
            "database": "postgresql" if settings.is_postgres else "sqlite"}
