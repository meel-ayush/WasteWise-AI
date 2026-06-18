from __future__ import annotations
import datetime
import logging
import threading

log = logging.getLogger("audit")


_audit_queue: list[dict] = []
_audit_lock = threading.Lock()
_audit_thread_started = False


def _audit_worker() -> None:
    import time

    while True:
        time.sleep(5)
        with _audit_lock:
            events = _audit_queue.copy()
            _audit_queue.clear()
        if not events:
            continue
        try:
            from services.supabase_db import _sb

            if _sb:
                _sb.table("audit_log").insert(events).execute()
        except Exception as e:
            log.warning("Audit log flush failed: %s", e)


def _start_audit_worker() -> None:
    global _audit_thread_started
    if _audit_thread_started:
        return
    _audit_thread_started = True
    t = threading.Thread(target=_audit_worker, daemon=True, name="audit-log")
    t.start()


def audit_log(
    actor_email: str | None,
    restaurant_id: str | None,
    action: str,
    endpoint: str = "",
    ip_address: str = "",
    success: bool = True,
    detail: str = "",
) -> None:
    event = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "actor_email": actor_email,
        "restaurant_id": restaurant_id,
        "action": action,
        "endpoint": endpoint,
        "ip_address": ip_address,
        "success": success,
        "detail": detail[:500] if detail else "",
    }
    with _audit_lock:
        _audit_queue.append(event)
        if len(_audit_queue) > 500:
            _audit_queue.pop(0)
    _start_audit_worker()


from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class AuditMiddleware(BaseHTTPMiddleware):

    _SKIP_PATHS = {"/", "/api/health", "/api/telegram_webhook", "/api/bot_info"}
    _WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method not in self._WRITE_METHODS or request.url.path in self._SKIP_PATHS:
            return await call_next(request)

        actor_email = None
        restaurant_id = None
        try:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()
                from services import auth

                info = auth.validate_web_token(token)
                if info:
                    actor_email = info.get("email")
                    restaurant_id = info.get("restaurant_id")
        except Exception:
            pass

        forwarded = request.headers.get("X-Forwarded-For", "")
        ip = (
            forwarded.split(",")[0].strip()
            if forwarded
            else (request.client.host if request.client else "unknown")
        )

        response = await call_next(request)
        success = response.status_code < 400

        audit_log(
            actor_email=actor_email,
            restaurant_id=restaurant_id,
            action=f"{request.method} {request.url.path}",
            endpoint=str(request.url.path),
            ip_address=ip,
            success=success,
            detail=f"status={response.status_code}",
        )
        return response

