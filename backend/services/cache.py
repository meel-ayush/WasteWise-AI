import time
import threading
from typing import Any, Optional


class TTLCache:
    """Thread-safe in-memory cache with per-key TTLs."""

    def __init__(self):
        self._store: dict = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        with self._lock:
            self._store[key] = (value, time.monotonic() + ttl_seconds)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def delete_prefix(self, prefix: str) -> int:
        """Delete all keys starting with prefix. Returns count deleted."""
        with self._lock:
            keys = [k for k in list(self._store.keys()) if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._store)


cache = TTLCache()


TTL_WEATHER = 3_600
TTL_AI_CONTEXT = 86_400
TTL_FORECAST = 86_400
TTL_INTELLIGENCE = 1_800
TTL_DB_SNAPSHOT = 10


def get_weather_cache(region: str) -> Optional[str]:
    return cache.get(f"weather:{region }")


def set_weather_cache(region: str, value: str) -> None:
    cache.set(f"weather:{region }", value, TTL_WEATHER)


def get_context_cache(region: str, date_str: str) -> Optional[dict]:
    return cache.get(f"ctx:{region }:{date_str }")


def set_context_cache(region: str, date_str: str, value: dict) -> None:
    cache.set(f"ctx:{region }:{date_str }", value, TTL_AI_CONTEXT)


def get_forecast_cache(restaurant_id: str, date_str: str) -> Optional[str]:
    return cache.get(f"forecast:{restaurant_id }:{date_str }")


def set_forecast_cache(restaurant_id: str, date_str: str, value: str) -> None:
    cache.set(f"forecast:{restaurant_id }:{date_str }", value, TTL_FORECAST)


def invalidate_forecast(restaurant_id: str) -> None:
    """Called after any data upload — clears forecast so next request regenerates."""
    cache.delete_prefix(f"forecast:{restaurant_id }:")


def get_intelligence_cache(restaurant_id: str) -> Optional[str]:
    return cache.get(f"intel:{restaurant_id }")


def set_intelligence_cache(restaurant_id: str, value: str) -> None:
    cache.set(f"intel:{restaurant_id }", value, TTL_INTELLIGENCE)


def invalidate_intelligence(restaurant_id: str) -> None:
    cache.delete(f"intel:{restaurant_id }")


def get_db_snapshot() -> Optional[dict]:
    return cache.get("db:snapshot")


def set_db_snapshot(db: dict) -> None:
    import copy

    cache.set("db:snapshot", copy.deepcopy(db), TTL_DB_SNAPSHOT)


def invalidate_db() -> None:
    cache.delete("db:snapshot")
