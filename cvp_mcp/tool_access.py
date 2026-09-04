"""Optional per-tool disable list for MCP deployments (capability flags)."""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Any, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])


def disabled_tools() -> set[str]:
    raw = os.environ.get("CVP_MCP_DISABLED_TOOLS", "")
    return {t.strip() for t in raw.split(",") if t.strip()}


def is_tool_disabled(tool_key: str) -> bool:
    """True when ``tool_key`` or its group prefix is in ``CVP_MCP_DISABLED_TOOLS``."""
    disabled = disabled_tools()
    if tool_key in disabled:
        return True
    if "." in tool_key:
        group = tool_key.split(".", 1)[0]
        return group in disabled
    return False


def tool_enabled(tool_name: str) -> Callable[[_F], _F]:
    """Skip tool execution when listed in ``CVP_MCP_DISABLED_TOOLS``."""

    def decorator(fn: _F) -> _F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if tool_name in disabled_tools():
                return {"error": "tool_disabled", "tool": tool_name}
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
