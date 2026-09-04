"""Simple rate limiting for expensive MCP tools."""

from __future__ import annotations

import functools
import logging
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])


class _TokenBucket:
    """Thread-safe token bucket (max ``rate`` calls per ``period_sec``)."""

    def __init__(self, rate: int, period_sec: float) -> None:
        self._rate = max(1, rate)
        self._period = period_sec
        self._lock = threading.Lock()
        self._timestamps: list[float] = []

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            cutoff = now - self._period
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            if len(self._timestamps) >= self._rate:
                return False
            self._timestamps.append(now)
            return True


# Per-tool buckets: (max calls, window seconds)
_EXPENSIVE_TOOL_LIMITS: dict[str, tuple[int, float]] = {
    "inventory.list": (6, 60.0),
    "topology.map": (4, 60.0),
    "events.search": (10, 60.0),
}

_buckets: dict[str, _TokenBucket] = {
    name: _TokenBucket(rate, period)
    for name, (rate, period) in _EXPENSIVE_TOOL_LIMITS.items()
}


def reset_rate_limit_buckets() -> None:
    """Replace all buckets with fresh token buckets (for tests)."""
    _buckets.clear()
    _buckets.update(
        {
            name: _TokenBucket(rate, period)
            for name, (rate, period) in _EXPENSIVE_TOOL_LIMITS.items()
        }
    )


def check_rate_limit(rate_limit_key: str) -> dict[str, str] | None:
    """Return a rate-limit error envelope when the bucket denies, else ``None``."""
    bucket = _buckets.get(rate_limit_key)
    if bucket is not None and not bucket.allow():
        logging.warning("Rate limit exceeded for %s", rate_limit_key)
        return {"error": "rate_limit_exceeded", "tool": rate_limit_key}
    return None


def rate_limited_tool(tool_name: str) -> Callable[[_F], _F]:
    """Decorator that rejects calls when the per-tool rate limit is exceeded."""

    def decorator(fn: _F) -> _F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            err = check_rate_limit(tool_name)
            if err is not None:
                return err
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
