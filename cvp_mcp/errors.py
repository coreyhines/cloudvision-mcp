"""Sanitized client-facing error responses (no exception text to MCP callers)."""

from __future__ import annotations

import logging
from typing import Any


def client_error(
    code: str,
    *,
    log_exc: BaseException | None = None,
    context: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Return a generic error dict for MCP tool responses.

    Log the real exception server-side only when ``log_exc`` is provided.
    """
    if log_exc is not None:
        if context:
            logging.error("%s: %s", context, log_exc)
        else:
            logging.error("%s", log_exc)
    out: dict[str, Any] = {"error": code}
    if extra:
        out.update(extra)
    return out


def sanitize_warning(code: str, *, log_exc: BaseException | None = None) -> str:
    """Return a machine-readable warning code; log details server-side."""
    if log_exc is not None:
        logging.warning("%s: %s", code, log_exc)
    return code
