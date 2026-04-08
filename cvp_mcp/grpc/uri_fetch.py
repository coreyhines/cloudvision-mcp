"""Fetch config or diff bodies referenced by CloudVision Resource API URIs."""

from __future__ import annotations

import logging
import ssl
import urllib.error
import urllib.request


def fetch_uri_with_bearer(
    uri: str,
    bearer_token: str,
    *,
    cafile: str | None = None,
    max_bytes: int = 2_000_000,
    timeout_sec: float = 60.0,
) -> tuple[str | None, str | None]:
    """
    GET ``uri`` with ``Authorization: Bearer``. Returns (text, error_message).
    """
    if not uri or not uri.strip():
        return None, "empty_uri"
    if not bearer_token:
        return None, "missing_token"

    req = urllib.request.Request(
        uri.strip(),
        headers={"Authorization": f"Bearer {bearer_token}"},
        method="GET",
    )
    ctx = ssl.create_default_context(cafile=cafile if cafile else None)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout_sec) as resp:
            data = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        logging.error("URI fetch HTTP error: %s %s", e.code, uri)
        return None, f"http_error:{e.code}"
    except Exception as e:
        logging.error("URI fetch error: %s %s", uri, e)
        return None, str(e)

    if len(data) > max_bytes:
        return (
            data[:max_bytes].decode("utf-8", errors="replace"),
            f"truncated_to_{max_bytes}_bytes",
        )
    return data.decode("utf-8", errors="replace"), None
