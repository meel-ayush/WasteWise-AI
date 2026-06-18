from __future__ import annotations

import asyncio
import threading
import httpx

TG_CONNECT_TIMEOUT: float = 40.0
TG_READ_TIMEOUT:    float = 30.0
TG_WRITE_TIMEOUT:   float = 15.0
TG_POOL_TIMEOUT:    float = 10.0

_client: httpx.AsyncClient | None = None
_client_lock = threading.Lock()


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(
            retries=0,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=60,
            ),
        ),
        timeout=httpx.Timeout(
            connect=TG_CONNECT_TIMEOUT,
            read=TG_READ_TIMEOUT,
            write=TG_WRITE_TIMEOUT,
            pool=TG_POOL_TIMEOUT,
        ),
    )


def get_tg_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        with _client_lock:
            if _client is None or _client.is_closed:
                _client = _make_client()
    return _client


async def recycle_tg_client() -> httpx.AsyncClient:
    global _client
    old = _client
    _client = _make_client()
    if old and not old.is_closed:
        try:
            await old.aclose()
        except Exception:
            pass
    print("[TgHttp] Client recycled — fresh connection pool.")
    return _client
