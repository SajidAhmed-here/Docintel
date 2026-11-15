import io
import os
import random
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="docintel-test-")
os.environ["DOCINTEL_SKIP_DOTENV"] = "1"  # never pick up a developer's .env (real API keys) in tests
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["STORAGE_DIR"] = f"{_TMP}/uploads"
os.environ["TASK_MODE"] = "sync"
os.environ["LLM_API_KEY"] = ""
os.environ["API_KEY"] = ""
os.environ["OCR_WORKERS"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from eval import generate_dataset as gen  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def rng():
    return random.Random(42)


@pytest.fixture
def make_invoice(rng):
    def _make(error=None, cur=None):
        return gen.make_invoice(rng, rng.randint(1, 999), error, cur=cur or gen.CURRENCIES[0])
    return _make


@pytest.fixture
def invoice_pdf(make_invoice, rng):
    """(pdf bytes, Inv) for a clean digital invoice."""
    def _pdf(layout=0, error=None):
        inv = make_invoice(error)
        return gen.pdf_bytes([inv], rng, [layout]), inv
    return _pdf


@pytest.fixture
def scanned_pdf(make_invoice, rng):
    def _scan(level="scan"):
        inv = make_invoice()
        img = gen.degrade(gen.render(gen.pdf_bytes([inv], rng, [0]), 170)[0], rng, level)
        return gen.images_to_pdf([img]), inv
    return _scan


@pytest.fixture
def invoice_png(make_invoice, rng):
    def _png(level="clean", fmt="PNG", dpi=170):
        inv = make_invoice()
        img = gen.degrade(gen.render(gen.pdf_bytes([inv], rng, [0]), dpi)[0], rng, level)
        buf = io.BytesIO()
        img.save(buf, format=fmt)
        return buf.getvalue(), inv
    return _png


@pytest.fixture
def tmp_file(tmp_path):
    def _w(name: str, data: bytes) -> str:
        p = tmp_path / name
        p.write_bytes(data)
        return str(p)
    return _w


def upload(client, *files):
    r = client.post("/api/documents", files=[("files", (n, d, "application/octet-stream")) for n, d in files])
    assert r.status_code == 202, r.text
    return r.json()


@pytest.fixture
def api_upload(client):
    return lambda *files: upload(client, *files)
