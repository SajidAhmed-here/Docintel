"""Background job dispatch.

TASK_MODE=celery  Redis-backed Celery workers. Scale with `--concurrency` and
                  more worker containers; acks_late + one task per worker
                  process means a crashed worker's job is redelivered.
TASK_MODE=inline  thread pool inside the API process (local dev, no Redis).
TASK_MODE=sync    run immediately in the request (tests).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from celery import Celery

from .config import settings
from .processor import process_document

celery_app = Celery("docintel", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=60 * 60 * 2,       # 1000-page scanned PDFs can take a while
    task_soft_time_limit=60 * 60 * 2 - 60,
    task_default_queue="documents",
    broker_connection_retry_on_startup=True,
)


@celery_app.task(name="process_document", bind=True, max_retries=2)
def process_document_task(self, doc_id: str) -> str:
    process_document(doc_id)
    return doc_id


_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="docworker")


def enqueue(doc_id: str) -> None:
    if settings.task_mode == "celery":
        process_document_task.delay(doc_id)
    elif settings.task_mode == "sync":
        process_document(doc_id)
    else:
        _pool.submit(process_document, doc_id)
