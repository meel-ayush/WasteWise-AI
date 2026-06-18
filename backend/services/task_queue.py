from __future__ import annotations
import os
import logging
from typing import Callable, Any

log = logging.getLogger("task_queue")

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "")


_celery_app = None

if CELERY_BROKER_URL:
    try:
        from celery import Celery

        _celery_app = Celery(
            "wastewise",
            broker=CELERY_BROKER_URL,
            backend=os.environ.get("CELERY_RESULT_BACKEND", CELERY_BROKER_URL),
        )
        _celery_app.conf.update(
            task_serializer="json",
            result_serializer="json",
            accept_content=["json"],
            timezone="Asia/Kolkata",
            enable_utc=True,
            task_acks_late=True,
            worker_prefetch_multiplier=1,
            task_max_retries=3,
            task_default_retry_delay=60,
        )
        log.info("✅ Celery task queue connected: %s", CELERY_BROKER_URL.split("@")[-1])
    except Exception as e:
        log.warning("⚠️  Celery unavailable (%s) — using APScheduler threads", e)
        _celery_app = None
else:
    log.info("ℹ️  CELERY_BROKER_URL not set — using APScheduler (thread-based fallback)")


def enqueue(func: Callable, *args: Any, countdown: int = 0, **kwargs: Any) -> None:
    if _celery_app:

        celery_task = _celery_app.task(func)
        celery_task.apply_async(args=args, kwargs=kwargs, countdown=countdown)
    else:
        import threading

        t = threading.Thread(target=_safe_run, args=(func, args, kwargs), daemon=True)
        t.start()


def _safe_run(func: Callable, args: tuple, kwargs: dict) -> None:
    try:
        func(*args, **kwargs)
    except Exception as e:
        log.error("Background task %s failed: %s", func.__name__, e)


def using_celery() -> bool:
    return _celery_app is not None


def queue_health() -> dict:
    if _celery_app:
        try:
            inspector = _celery_app.control.inspect(timeout=2)
            stats = inspector.stats()
            workers = list(stats.keys()) if stats else []
            return {"queue": "celery", "status": "ok", "workers": len(workers)}
        except Exception as e:
            return {"queue": "celery", "status": "error", "error": str(e)}
    return {"queue": "apscheduler_threads", "status": "ok"}
