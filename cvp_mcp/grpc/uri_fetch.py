"""Fetch config or diff bodies referenced by CloudVision Resource API URIs."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import urllib.error
import urllib.request
from typing import Any

from cvp_mcp.grpc.uri_allowlist import is_uri_host_allowed

try:
    import aiohttp
except Exception:  # pragma: no cover - optional at runtime
    aiohttp = None

_DEFAULT_MAX_CONCURRENT = 10


def _check_uri_allowed(uri: str, cvp_endpoint: str | None) -> str | None:
    if not is_uri_host_allowed(uri, cvp_endpoint):
        logging.error("URI fetch blocked (host not allowlisted): %s", uri)
        return "uri_host_not_allowed"
    return None


def fetch_uri_with_bearer(
    uri: str,
    bearer_token: str,
    *,
    cafile: str | None = None,
    cvp_endpoint: str | None = None,
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

    blocked = _check_uri_allowed(uri, cvp_endpoint)
    if blocked:
        return None, blocked

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
        return None, "uri_fetch_failed"

    if len(data) > max_bytes:
        return (
            data[:max_bytes].decode("utf-8", errors="replace"),
            f"truncated_to_{max_bytes}_bytes",
        )
    return data.decode("utf-8", errors="replace"), None


def get_json_with_bearer(
    uri: str,
    bearer_token: str,
    *,
    cafile: str | None = None,
    cvp_endpoint: str | None = None,
    max_bytes: int = 5_000_000,
    timeout_sec: float = 60.0,
) -> tuple[dict | list | None, str | None]:
    """GET URI with bearer auth and decode response JSON."""
    text, err = fetch_uri_with_bearer(
        uri,
        bearer_token,
        cafile=cafile,
        cvp_endpoint=cvp_endpoint,
        max_bytes=max_bytes,
        timeout_sec=timeout_sec,
    )
    if err:
        return None, err
    if not text:
        return None, "empty_response"
    text = text.strip()
    if text.startswith(")]}'"):
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1 :].strip()
    try:
        obj = json.loads(text)
    except Exception:
        # Some endpoints can return JSON lines; parse first valid object line.
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                break
            except Exception:
                continue
        else:
            return None, "invalid_json_response"
    if not isinstance(obj, (dict, list)):
        return None, "unexpected_json_type"
    return obj, None


def post_json_with_bearer(
    uri: str,
    payload: dict,
    bearer_token: str,
    *,
    cafile: str | None = None,
    cvp_endpoint: str | None = None,
    max_bytes: int = 2_000_000,
    timeout_sec: float = 60.0,
) -> tuple[dict | list | None, str | None]:
    """POST JSON with bearer auth and decode response JSON."""
    if not uri or not uri.strip():
        return None, "empty_uri"
    if not bearer_token:
        return None, "missing_token"

    blocked = _check_uri_allowed(uri, cvp_endpoint)
    if blocked:
        return None, blocked

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        uri.strip(),
        data=data,
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    ctx = ssl.create_default_context(cafile=cafile if cafile else None)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout_sec) as resp:
            raw = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        logging.error("POST HTTP error: %s %s", e.code, uri)
        return None, f"http_error:{e.code}"
    except Exception as e:
        logging.error("POST error: %s %s", uri, e)
        return None, "uri_post_failed"

    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
    try:
        obj = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return None, "invalid_json_response"
    if not isinstance(obj, (dict, list)):
        return None, "unexpected_json_type"
    return obj, None


def post_raw_with_bearer(
    uri: str,
    body: str,
    bearer_token: str,
    *,
    cafile: str | None = None,
    cvp_endpoint: str | None = None,
    content_type: str = "application/json",
    max_bytes: int = 2_000_000,
    timeout_sec: float = 60.0,
) -> tuple[str | None, str | None]:
    """POST raw text body with bearer auth."""
    if not uri or not uri.strip():
        return None, "empty_uri"
    if not bearer_token:
        return None, "missing_token"

    blocked = _check_uri_allowed(uri, cvp_endpoint)
    if blocked:
        return None, blocked

    req = urllib.request.Request(
        uri.strip(),
        data=body.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": content_type,
            "Accept": "application/json, text/plain, */*",
        },
        method="POST",
    )
    ctx = ssl.create_default_context(cafile=cafile if cafile else None)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout_sec) as resp:
            raw = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        logging.error("POST HTTP error: %s %s", e.code, uri)
        return None, f"http_error:{e.code}"
    except Exception as e:
        logging.error("POST raw error: %s %s", uri, e)
        return None, "uri_post_failed"

    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
    return raw.decode("utf-8", errors="replace"), None


async def post_json_many_with_bearer_async(
    uri: str,
    payloads: list[Any],
    bearer_token: str,
    *,
    cafile: str | None = None,
    cvp_endpoint: str | None = None,
    max_bytes: int = 2_000_000,
    timeout_sec: float = 60.0,
    max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
) -> tuple[list[dict | list | None], str | None]:
    """POST many JSON payloads concurrently using aiohttp."""
    if not uri or not uri.strip():
        return [], "empty_uri"
    if not bearer_token:
        return [], "missing_token"
    if aiohttp is None:
        return [], "aiohttp_not_installed"

    blocked = _check_uri_allowed(uri, cvp_endpoint)
    if blocked:
        return [], blocked

    ssl_ctx = ssl.create_default_context(cafile=cafile if cafile else None)
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    results: list[dict | list | None] = [None] * len(payloads)
    errors: list[str] = []
    sem = asyncio.Semaphore(max(1, max_concurrent))

    async def _one(session: aiohttp.ClientSession, idx: int, payload: Any):
        async with sem:
            try:
                async with session.post(uri.strip(), json=payload, ssl=ssl_ctx) as resp:
                    if resp.status >= 400:
                        errors.append(f"http_error:{resp.status}")
                        return idx, None
                    text = await resp.text()
                    if len(text.encode("utf-8", errors="ignore")) > max_bytes:
                        text = text[:max_bytes]
                    obj = json.loads(text)
                    if isinstance(obj, (dict, list)):
                        return idx, obj
                    return idx, None
            except Exception as e:
                logging.debug("POST async item %s failed: %s", idx, e)
                return idx, None

    try:
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            tasks = [_one(session, i, p) for i, p in enumerate(payloads)]
            done = await asyncio.gather(*tasks, return_exceptions=False)
            for idx, obj in done:
                results[idx] = obj
            if errors:
                return results, errors[-1]
            return results, None
    except Exception as e:
        logging.error("POST async error: %s %s", uri, e)
        return [], "uri_post_failed"
