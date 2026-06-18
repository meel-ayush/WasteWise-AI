from __future__ import annotations

import asyncio
import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

_BRIDGE_URL: str = os.environ.get("BRIDGE_URL", "").rstrip("/")
_INTERNAL_SECRET: str = os.environ.get("INTERNAL_SECRET", "")
_bridge_client: Optional[httpx.AsyncClient] = None


def _get_bridge_client() -> httpx.AsyncClient:
    global _bridge_client
    if _bridge_client is None or _bridge_client.is_closed:
        headers = {}
        if _INTERNAL_SECRET:
            headers["X-Internal-Secret"] = _INTERNAL_SECRET
        _bridge_client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=15.0, pool=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _bridge_client

_TTL_SECONDS: float = 300.0
_MAX_ATTEMPTS: int = 15
_QUEUE_MAX_SIZE: int = 2000

_CB_FAILURE_THRESHOLD: int = 3
_CB_RECOVERY_TIMEOUT: float = 20.0

_RETRYABLE = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)


@dataclass
class _OutMsg:
    chat_id: int
    params: dict
    enqueued_at: float = field(default_factory=time.monotonic)
    attempt: int = 0
    next_attempt_at: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.enqueued_at) > _TTL_SECONDS

    @property
    def due(self) -> bool:
        return time.monotonic() >= self.next_attempt_at

    def schedule_retry(self) -> None:
        self.attempt += 1
        cap = 30.0
        sleep = random.uniform(0.0, min(cap, 1.0 * (2.0 ** self.attempt)))
        self.next_attempt_at = time.monotonic() + sleep


class _CircuitBreaker:
    _CLOSED, _OPEN, _HALF_OPEN = "closed", "open", "half_open"

    def __init__(
        self,
        failure_threshold: int = _CB_FAILURE_THRESHOLD,
        recovery_timeout: float = _CB_RECOVERY_TIMEOUT,
    ) -> None:
        self._state: str = self._CLOSED
        self._failures: int = 0
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._opened_at: float = 0.0

    @property
    def allowing(self) -> bool:
        if self._state == self._CLOSED:
            return True
        if self._state == self._OPEN:
            if time.monotonic() - self._opened_at >= self._recovery_timeout:
                self._state = self._HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        if self._state != self._CLOSED:
            print("[SendQueue] Circuit CLOSED — Telegram API recovered ✓")
        self._failures = 0
        self._state = self._CLOSED

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._failure_threshold and self._state == self._CLOSED:
            print(
                f"[SendQueue] Circuit OPEN after {self._failures} consecutive failures. "
                f"Pausing {self._recovery_timeout:.0f}s before next attempt."
            )
        if self._failures >= self._failure_threshold:
            self._state = self._OPEN
            self._opened_at = time.monotonic()


class _UpdateDedup:
    _TTL = 600.0

    def __init__(self) -> None:
        self._seen: dict[int, float] = {}

    def is_duplicate(self, update_id: int) -> bool:
        now = time.monotonic()
        if len(self._seen) > 5000:
            stale = [uid for uid, ts in self._seen.items() if now - ts > self._TTL]
            for uid in stale:
                del self._seen[uid]
        if update_id in self._seen:
            return True
        self._seen[update_id] = now
        return False


class TelegramSendQueue:

    def __init__(self) -> None:
        self._queue: asyncio.Queue[_OutMsg] = asyncio.Queue(maxsize=_QUEUE_MAX_SIZE)
        self._retries: list[_OutMsg] = []
        self._breaker = _CircuitBreaker()
        self._api_base: str = ""
        self._worker_task: Optional[asyncio.Task] = None

    def configure(self, tg_api_base: str) -> None:
        self._api_base = tg_api_base

    async def enqueue(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "Markdown",
        reply_markup=None,
    ) -> None:
        self._ensure_started()
        params: dict = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        if reply_markup is not None:
            if not isinstance(reply_markup, str):
                reply_markup = json.dumps(reply_markup)
            params["reply_markup"] = reply_markup
        msg = _OutMsg(chat_id=chat_id, params=params)
        try:
            self._queue.put_nowait(msg)
        except asyncio.QueueFull:
            print(f"[SendQueue] Queue full — dropping message to chat {chat_id}.")

    def _ensure_started(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(
                self._worker(), name="tg_send_queue_worker"
            )

    async def _attempt(self, msg: _OutMsg) -> bool:
        if not self._api_base:
            return False

        if _BRIDGE_URL:
            url = f"{_BRIDGE_URL}/api/forward"
            client = _get_bridge_client()
            payload = {"method": "sendMessage", "params": msg.params}
            try:
                resp = await client.post(url, json=payload)
            except _RETRYABLE as exc:
                print(
                    f"[SendQueue] Bridge attempt {msg.attempt + 1} to chat {msg.chat_id} "
                    f"failed ({type(exc).__name__})."
                )
                self._breaker.record_failure()
                return False
            except Exception as exc:
                print(f"[SendQueue] Bridge non-retryable error for chat {msg.chat_id}: {exc}")
                return True

            try:
                data = resp.json()
            except Exception:
                data = {}

            if resp.status_code == 200 and data.get("ok"):
                if msg.attempt > 0:
                    print(
                        f"[SendQueue] ✓ Bridge delivered to chat {msg.chat_id} "
                        f"after {msg.attempt} retr{'y' if msg.attempt == 1 else 'ies'}."
                    )
                self._breaker.record_success()
                return True

            if resp.status_code in (400, 401, 403):
                desc = data.get("description", "")
                print(
                    f"[SendQueue] Bridge permanent error {resp.status_code} "
                    f"for chat {msg.chat_id}: {desc}"
                )
                return True

            if resp.status_code == 429:
                retry_after = float(
                    data.get("parameters", {}).get("retry_after", 5)
                )
                print(f"[SendQueue] Bridge rate-limited — pausing {retry_after:.0f}s.")
                await asyncio.sleep(retry_after)
                self._breaker.record_failure()
                return False

            self._breaker.record_failure()
            return False

        print(
            f"[SendQueue] FATAL: No TELEGRAM_BRIDGE_URL or BRIDGE_URL set. Cannot deliver message to chat {msg.chat_id}. "
            "Set BRIDGE_URL in environment variables."
        )
        return True

    async def _worker(self) -> None:
        print("[SendQueue] Worker started.")
        while True:
            try:
                await self._run_one_cycle()
            except Exception as exc:
                print(f"[SendQueue] Worker error (continuing): {exc}")
                await asyncio.sleep(1.0)

    async def _run_one_cycle(self) -> None:
        now = time.monotonic()

        due = [m for m in self._retries if m.due]
        for m in due:
            self._retries.remove(m)
            try:
                self._queue.put_nowait(m)
            except asyncio.QueueFull:
                pass

        if not self._breaker.allowing:
            await asyncio.sleep(1.0)
            return

        timeout = 0.1 if self._retries else 10.0
        try:
            msg: _OutMsg = await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return

        if msg.expired:
            print(
                f"[SendQueue] Message to chat {msg.chat_id} expired after "
                f"{msg.attempt} attempt(s). Dropping."
            )
            return

        delivered = await self._attempt(msg)
        if not delivered:
            if msg.attempt >= _MAX_ATTEMPTS:
                print(
                    f"[SendQueue] Permanently dropping message to chat {msg.chat_id} "
                    f"after {msg.attempt} attempts."
                )
                return
            msg.schedule_retry()
            self._retries.append(msg)


_queue_singleton: Optional[TelegramSendQueue] = None
_dedup_singleton: Optional[_UpdateDedup] = None


def get_send_queue() -> TelegramSendQueue:
    global _queue_singleton
    if _queue_singleton is None:
        _queue_singleton = TelegramSendQueue()
    return _queue_singleton


def get_update_dedup() -> _UpdateDedup:
    global _dedup_singleton
    if _dedup_singleton is None:
        _dedup_singleton = _UpdateDedup()
    return _dedup_singleton
