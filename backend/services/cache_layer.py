from __future__ import annotations
import os
import json
import time
import threading
import logging
from typing import Any, Optional

log = logging.getLogger("cache_layer")


_redis_client = None
_REDIS_URL = os.environ.get("REDIS_URL", "")

if _REDIS_URL:
    try:
        import redis

        _kwargs = dict(
            decode_responses=True,
            socket_connect_timeout=10,
            socket_timeout=15,
            retry_on_timeout=True,
            health_check_interval=60,
            max_connections=5,
        )
        if _REDIS_URL.startswith("rediss://"):
            _kwargs["ssl_cert_reqs"] = None

        _redis_client = redis.from_url(_REDIS_URL, **_kwargs)
        _redis_client.ping()
        log.info("âœ… Redis cache connected (Upstash): %s", _REDIS_URL.split("@")[-1])
    except Exception as e:
        log.warning("âš ï¸  Redis unavailable (%s) â€” using in-memory fallback", e)
        _redis_client = None
else:
    log.info("â„¹ï¸  REDIS_URL not set â€” using in-memory cache")


# In-memory fallback store: maps key -> (value, expires_at_monotonic_or_None)
_mem_store: dict[str, tuple[Any, Optional[float]]] = {}
_mem_lock = threading.Lock()


DEFAULT_TTL = 300


def _mem_evict_expired() -> None:
    """Remove all expired keys from the in-memory store.
    Must be called while holding _mem_lock.
    """
    now = time.monotonic()
    expired = [k for k, (_, exp) in _mem_store.items() if exp is not None and exp <= now]
    for k in expired:
        del _mem_store[k]


def cache_get(key: str) -> Optional[Any]:
    """Get a value from cache. Returns None if missing or expired."""
    if _redis_client:
        try:
            raw = _redis_client.get(key)
            return json.loads(raw) if raw is not None else None
        except Exception as e:
            log.warning("Redis GET failed (%s) â€” using memory fallback", e)

    with _mem_lock:
        entry = _mem_store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and time.monotonic() > expires_at:
            del _mem_store[key]
            return None
        return value


def cache_set(key: str, value: Any, ttl: int = DEFAULT_TTL) -> None:
    """Set a value in cache with optional TTL (seconds)."""
    if _redis_client:
        try:
            raw = json.dumps(value, ensure_ascii=False, default=str)
            if ttl:
                _redis_client.setex(key, ttl, raw)
            else:
                _redis_client.set(key, raw)
            return
        except Exception as e:
            log.warning("Redis SET failed (%s) â€” using memory fallback", e)
    with _mem_lock:
        expires_at = time.monotonic() + ttl if ttl else None
        _mem_store[key] = (value, expires_at)
        # Opportunistic eviction: keep store size bounded
        if len(_mem_store) > 500:
            _mem_evict_expired()


def cache_delete(key: str) -> None:
    """Delete a specific key from cache."""
    if _redis_client:
        try:
            _redis_client.delete(key)
            return
        except Exception as e:
            log.warning("Redis DEL failed (%s)", e)
    with _mem_lock:
        _mem_store.pop(key, None)


def cache_flush(pattern: str = "*") -> None:
    """Delete all keys matching pattern. Use '*' to flush everything."""
    if _redis_client:
        try:
            keys = _redis_client.keys(pattern)
            if keys:
                _redis_client.delete(*keys)
            return
        except Exception as e:
            log.warning("Redis FLUSH failed (%s)", e)
    with _mem_lock:
        if pattern == "*":
            _mem_store.clear()
        else:
            import fnmatch

            to_delete = [k for k in _mem_store if fnmatch.fnmatch(k, pattern)]
            for k in to_delete:
                del _mem_store[k]


def cache_using_redis() -> bool:
    """Returns True if Redis is active, False if using in-memory fallback."""
    return _redis_client is not None


def cache_health() -> dict:
    """Health check â€” called from /api/health endpoint."""
    if _redis_client:
        try:
            _redis_client.ping()
            return {"cache": "redis", "status": "ok"}
        except Exception as e:
            return {"cache": "redis", "status": "error", "error": str(e)}
    return {"cache": "memory", "status": "ok", "keys": len(_mem_store)}


