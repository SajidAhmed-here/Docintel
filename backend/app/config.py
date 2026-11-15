"""Runtime configuration, read once from environment variables.

Every setting has a safe local default so `uvicorn app.main:app` works with
no setup (SQLite + in-process worker + rule-based extraction). Values are
read from the process environment, then from a `.env` file in the repo root
or in backend/ (real environment variables always win).
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    """Minimal .env loader (KEY=VALUE lines, # comments). No extra dependency."""
    here = Path(__file__).resolve().parent.parent  # backend/
    for path in (here / ".env", here.parent / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key, value)


if os.getenv("DOCINTEL_SKIP_DOTENV") != "1":  # tests set this to stay hermetic
    _load_dotenv()


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


# OpenAI-compatible endpoints. Gemini is the default because it has a free tier.
LLM_PROVIDERS = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-flash-lite-latest"),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "xai": ("https://api.x.ai/v1", "grok-3-mini"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
}


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./data/docintel.db"))
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    # celery = Redis-backed workers (production); inline = background threads in
    # the API process (dev, no Redis); sync = run immediately (tests).
    task_mode: str = field(default_factory=lambda: os.getenv("TASK_MODE", "inline"))
    storage_dir: Path = field(default_factory=lambda: Path(os.getenv("STORAGE_DIR", "./data/uploads")))

    api_key: str = field(default_factory=lambda: os.getenv("API_KEY", ""))
    allowed_origins: list[str] = field(
        default_factory=lambda: [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
    )
    max_upload_mb: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 250))
    max_files_per_request: int = field(default_factory=lambda: _int("MAX_FILES_PER_REQUEST", 50))
    max_pdf_pages: int = field(default_factory=lambda: _int("MAX_PDF_PAGES", 5000))
    uploads_per_minute: int = field(default_factory=lambda: _int("UPLOADS_PER_MINUTE", 120))

    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "gemini").lower())
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", ""))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", ""))
    llm_timeout_s: int = field(default_factory=lambda: _int("LLM_TIMEOUT_S", 60))
    llm_max_retries: int = field(default_factory=lambda: _int("LLM_MAX_RETRIES", 4))
    llm_concurrency: int = field(default_factory=lambda: _int("LLM_CONCURRENCY", 2))
    # Gemini "thinking" models: none|low|medium|high. Low keeps extraction fast on the free tier.
    llm_reasoning_effort: str = field(default_factory=lambda: os.getenv("LLM_REASONING_EFFORT", "low"))
    llm_max_input_chars: int = field(default_factory=lambda: _int("LLM_MAX_INPUT_CHARS", 12000))

    tesseract_cmd: str = field(default_factory=lambda: os.getenv("TESSERACT_CMD", ""))
    ocr_lang: str = field(default_factory=lambda: os.getenv("OCR_LANG", "eng"))
    ocr_dpi: int = field(default_factory=lambda: _int("OCR_DPI", 300))
    ocr_sparse_pass: bool = field(default_factory=lambda: _bool("OCR_SPARSE_PASS", True))
    ocr_workers: int = field(default_factory=lambda: _int("OCR_WORKERS", max(1, (os.cpu_count() or 2) - 1)))
    # A PDF page with fewer extractable characters than this is treated as scanned.
    min_text_chars: int = field(default_factory=lambda: _int("MIN_TEXT_CHARS", 40))

    embedding_backend: str = field(default_factory=lambda: os.getenv("EMBEDDING_BACKEND", "hash"))
    embedding_dim: int = 384
    # cosine similarity below this is treated as noise (hash vectors: noise < 0.1; bge-small: noise ~0.5)
    semantic_min_score: float = field(default_factory=lambda: float(os.getenv("SEMANTIC_MIN_SCORE", "0") or 0))
    date_order: str = field(default_factory=lambda: os.getenv("DATE_ORDER", "DMY"))  # tie-break for 03/04/2026

    def __post_init__(self):
        if not self.semantic_min_score:
            self.semantic_min_score = 0.6 if self.embedding_backend == "fastembed" else 0.12

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key) and self.llm_provider != "none"

    @property
    def llm_endpoint(self) -> tuple[str, str]:
        base, model = LLM_PROVIDERS.get(self.llm_provider, LLM_PROVIDERS["gemini"])
        return (self.llm_base_url or base).rstrip("/"), (self.llm_model or model)

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


def _find_tesseract() -> str:
    """Tesseract is often not on PATH on Windows; look in the usual install folders."""
    found = shutil.which("tesseract")
    if found:
        return found
    for base in (os.getenv("ProgramFiles", r"C:\Program Files"), os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.path.join(os.getenv("LOCALAPPDATA", ""), "Programs")):
        cand = Path(base) / "Tesseract-OCR" / "tesseract.exe"
        if cand.is_file():
            return str(cand)
    return ""


settings = Settings()
if not settings.tesseract_cmd:
    settings.tesseract_cmd = _find_tesseract()
settings.storage_dir.mkdir(parents=True, exist_ok=True)
if settings.database_url.startswith("sqlite:///"):
    Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
