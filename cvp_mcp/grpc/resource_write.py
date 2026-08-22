"""Allowlisted POST/DELETE helper for CloudVision Resource API config writes.

Every refusal happens **before** any HTTP request is built or sent. Callers get
``(object, None)`` on success or ``(None, "<machine_readable_error>")``.

See ``docs/studios-phase2-spec.md`` ("HTTP helper (all write slices)").
"""

from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from cvp_mcp.grpc.uri_allowlist import is_uri_host_allowed
from cvp_mcp.grpc.uri_fetch import post_json_with_bearer

# Exact resource paths this helper may write. No query string, no prefixes.
POST_PATH_ALLOWLIST: frozenset[str] = frozenset(
    {
        "/api/resources/workspace/v1/WorkspaceConfig",
        "/api/resources/studio/v1/InputsConfig",
        "/api/resources/studio/v1/AssignedTagsConfig",
        "/api/resources/studio/v1/StudioConfig",
    }
)

DELETE_PATH_ALLOWLIST: frozenset[str] = frozenset(
    {
        "/api/resources/workspace/v1/WorkspaceConfig",
    }
)

# Only values observed on this tenant. Do not add guessed enum names.
REQUEST_START_BUILD = "REQUEST_START_BUILD"
REQUEST_SUBMIT = "REQUEST_SUBMIT"
ALLOWED_REQUESTS: frozenset[str] = frozenset({REQUEST_START_BUILD, REQUEST_SUBMIT})

# Envelope keys that could schedule or auto-start work outside the MCP flow.
_DENIED_ENVELOPE_KEYS: frozenset[str] = frozenset({"start", "schedule"})

# Denylist applies to workspace/studio envelopes only; InputsConfig carries a
# JSON *string* named ``inputs`` that legitimately contains those words.
_DENYLIST_PATHS: frozenset[str] = frozenset(
    {
        "/api/resources/workspace/v1/WorkspaceConfig",
        "/api/resources/studio/v1/StudioConfig",
    }
)

_QUERY_INJECTION_CHARS: tuple[str, ...] = ("?", "&", "#")

_MAX_BYTES = 2_000_000
_TIMEOUT_SEC = 60.0


def _normalize_base(base_url: str | None) -> str:
    return (base_url or "").strip().rstrip("/")


def _submit_allowed() -> bool:
    """True only when ``cvp_mcp.write_access.submit_enabled`` exists and is on.

    Bucket 0 may not be merged yet; a missing module means submit stays off.
    """
    try:
        from cvp_mcp.write_access import submit_enabled  # noqa: PLC0415
    except Exception:
        logging.info("resource_write: submit gate unavailable; treating as disabled")
        return False
    try:
        return bool(submit_enabled() if callable(submit_enabled) else submit_enabled)
    except Exception:
        logging.info("resource_write: submit gate raised; treating as disabled")
        return False


def _check_request_field(body: dict[str, Any]) -> str | None:
    """Validate the top-level ``request`` enum, including the submit gate."""
    for field in ("request", "Request"):
        if field not in body:
            continue
        value = body[field]
        if not isinstance(value, str) or value not in ALLOWED_REQUESTS:
            logging.error("resource_write: request value not allowed")
            return "request_not_allowed"
        if value == REQUEST_SUBMIT and not _submit_allowed():
            return "submit_disabled"
    return None


def _check_denied_keys(path: str, body: dict[str, Any]) -> str | None:
    """Reject ``start`` / ``schedule`` on the envelope (never inside ``inputs``)."""
    if path not in _DENYLIST_PATHS:
        return None
    scopes: list[dict[str, Any]] = [body]
    for field in ("requestParams", "request_params"):
        params = body.get(field)
        if isinstance(params, dict):
            scopes.append(params)
    for scope in scopes:
        for key in scope:
            if isinstance(key, str) and key.strip().lower() in _DENIED_ENVELOPE_KEYS:
                logging.error("resource_write: denied envelope key %s", key)
                return f"forbidden_key:{key.strip().lower()}"
    return None


def _workspace_id_from_body(body: dict[str, Any]) -> str:
    key = body.get("key")
    if not isinstance(key, dict):
        return ""
    for field in ("workspaceId", "workspace_id"):
        value = key.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def post_resource_config(
    base_url: str,
    path: str,
    body: dict[str, Any],
    token: str,
    *,
    cafile: str | None = None,
    cvp_endpoint: str | None = None,
) -> tuple[dict | None, str | None]:
    """POST a Resource API config body after every allowlist check passes.

    ``path`` must be an exact allowlisted path with no query string. Returns the
    parsed JSON object, or ``(None, error)`` with no HTTP performed on refusal.
    """
    base = _normalize_base(base_url)
    if not base:
        return None, "missing_base_url"
    if not (token or "").strip():
        return None, "missing_token"

    if path not in POST_PATH_ALLOWLIST:
        logging.error("resource_write: POST path not allowed: %s", path)
        return None, "path_not_allowed"
    if not isinstance(body, dict):
        return None, "invalid_body"

    err = _check_request_field(body)
    if err:
        return None, err

    err = _check_denied_keys(path, body)
    if err:
        return None, err

    workspace_id = _workspace_id_from_body(body)
    if not workspace_id:
        logging.error("resource_write: POST refused, empty workspace id: %s", path)
        return None, "workspace_id_required"

    uri = f"{base}{path}"
    if not is_uri_host_allowed(uri, cvp_endpoint):
        logging.error("resource_write: POST host not allowlisted")
        return None, "uri_host_not_allowed"

    obj, err = post_json_with_bearer(
        uri,
        body,
        token,
        cafile=cafile,
        cvp_endpoint=cvp_endpoint,
        max_bytes=_MAX_BYTES,
        timeout_sec=_TIMEOUT_SEC,
    )
    if err:
        logging.info(
            "resource_write: POST %s workspace=%s -> %s", path, workspace_id, err
        )
        return None, err
    if not isinstance(obj, dict):
        return None, "unexpected_json_type"
    logging.info("resource_write: POST %s workspace=%s -> ok", path, workspace_id)
    return obj, None


def delete_resource_config(
    base_url: str,
    path: str,
    params: dict[str, Any],
    token: str,
    *,
    cafile: str | None = None,
    cvp_endpoint: str | None = None,
) -> tuple[dict | None, str | None]:
    """DELETE a Resource API config keyed by URL-encoded ``params``.

    ``path`` is matched exactly against the delete allowlist (no query string);
    the query is built here so callers cannot smuggle extra parameters.
    """
    base = _normalize_base(base_url)
    if not base:
        return None, "missing_base_url"
    if not (token or "").strip():
        return None, "missing_token"

    if path not in DELETE_PATH_ALLOWLIST:
        logging.error("resource_write: DELETE path not allowed: %s", path)
        return None, "path_not_allowed"
    if not isinstance(params, dict) or not params:
        return None, "invalid_params"

    encoded: dict[str, str] = {}
    for name, value in params.items():
        text = "" if value is None else str(value)
        if any(char in text for char in _QUERY_INJECTION_CHARS):
            logging.error("resource_write: DELETE param %s has query separators", name)
            return None, "invalid_workspace_id"
        encoded[str(name)] = text

    workspace_id = ""
    for field in ("key.workspaceId", "key.workspace_id"):
        if encoded.get(field, "").strip():
            workspace_id = encoded[field].strip()
            break
    if not workspace_id:
        logging.error("resource_write: DELETE refused, empty workspace id")
        return None, "workspace_id_required"

    uri = f"{base}{path}?{urllib.parse.urlencode(encoded)}"
    if not is_uri_host_allowed(uri, cvp_endpoint):
        logging.error("resource_write: DELETE host not allowlisted")
        return None, "uri_host_not_allowed"

    req = urllib.request.Request(
        uri,
        headers={
            "Authorization": f"Bearer {token.strip()}",
            "Accept": "application/json",
        },
        method="DELETE",
    )
    ctx = ssl.create_default_context(cafile=cafile if cafile else None)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=_TIMEOUT_SEC) as resp:
            raw = resp.read(_MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        logging.error("resource_write: DELETE HTTP error %s", e.code)
        return None, f"http_error:{e.code}"
    except Exception as e:
        logging.error("resource_write: DELETE failed: %s", e)
        return None, "resource_delete_failed"

    if len(raw) > _MAX_BYTES:
        raw = raw[:_MAX_BYTES]
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        logging.info("resource_write: DELETE %s workspace=%s -> ok", path, workspace_id)
        return {}, None
    try:
        obj = json.loads(text)
    except Exception:
        return None, "invalid_json_response"
    if not isinstance(obj, dict):
        return None, "unexpected_json_type"
    logging.info("resource_write: DELETE %s workspace=%s -> ok", path, workspace_id)
    return obj, None
