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
    "get_cvp_all_inventory": (6, 60.0),
    "map_cvp_network_topology": (4, 60.0),
    "search_cvp_events": (10, 60.0),
}

_buckets: dict[str, _TokenBucket] = {
    name: _TokenBucket(rate, period)
    for name, (rate, period) in _EXPENSIVE_TOOL_LIMITS.items()
}


def rate_limited_tool(tool_name: str) -> Callable[[_F], _F]:
    """Decorator that rejects calls when the per-tool rate limit is exceeded."""

    def decorator(fn: _F) -> _F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            bucket = _buckets.get(tool_name)
            if bucket is not None and not bucket.allow():
                logging.warning("Rate limit exceeded for %s", tool_name)
                return {"error": "rate_limit_exceeded", "tool": tool_name}
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
