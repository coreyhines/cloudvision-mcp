"""Minimal async flow for inventory + running-config retrieval."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp


def _as_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        inner = value.get("value")
        if isinstance(inner, str):
            return inner
    return ""


def _serial_from_inventory_node(node: dict[str, Any]) -> str:
    """Best-effort serial / device_id from REST inventory shapes (snake_case + camelCase + nested key)."""
    direct = (
        _as_str(node.get("device_id"))
        or _as_str(node.get("serial_number"))
        or _as_str(node.get("serialNumber"))
        or _as_str(node.get("deviceId"))
    )
    if direct:
        return direct
    key = node.get("key")
    if isinstance(key, dict):
        return (
            _as_str(key.get("device_id"))
            or _as_str(key.get("deviceId"))
            or _as_str(key.get("serial_number"))
        )
    return ""


def _hostname_from_inventory_node(node: dict[str, Any]) -> str:
    h = _as_str(node.get("hostname")) or _as_str(node.get("hostName"))
    if h:
        return h
    fqdn = _as_str(node.get("fqdn"))
    if not fqdn:
        return ""
    return fqdn.split(".")[0] if "." in fqdn else fqdn


def _iter_dicts(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_dicts(v)


def _extract_config_from_response(obj: Any) -> str | None:
    if isinstance(obj, dict):
        cfg = obj.get("config")
        if isinstance(cfg, str) and cfg.strip():
            return cfg
        if isinstance(cfg, dict):
            v = cfg.get("value")
            if isinstance(v, str) and v.strip():
                return v
        for v in obj.values():
            hit = _extract_config_from_response(v)
            if hit:
                return hit
    if isinstance(obj, list):
        for item in obj:
            hit = _extract_config_from_response(item)
            if hit:
                return hit
    return None


def _decode_json_maybe_multi(raw_text: str) -> Any:
    """
    Decode JSON that may contain multiple top-level documents.

    CloudVision service endpoints sometimes return NDJSON-ish or concatenated
    JSON objects instead of a single document.
    """
    text = (raw_text or "").strip()
    if not text:
        return {}
    if text.startswith(")]}'"):
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1 :].strip()

    # Fast path: normal single-document JSON.
    try:
        return json.loads(text)
    except Exception:
        pass

    # Multi-document fallback: parse sequential JSON values.
    decoder = json.JSONDecoder()
    idx = 0
    docs: list[Any] = []
    length = len(text)
    while idx < length:
        while idx < length and text[idx].isspace():
            idx += 1
        if idx >= length:
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except Exception:
            # NDJSON fallback: first line that is valid JSON wins.
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    docs.append(json.loads(line))
                    continue
                except Exception:
                    continue
            break
        docs.append(obj)
        idx = end

    if not docs:
        return {}
    if len(docs) == 1:
        return docs[0]
    return docs


async def get_inventory(
    session: aiohttp.ClientSession,
    url: str,
) -> list[dict[str, Any]]:
    async with session.get(url) as resp:
        resp.raise_for_status()
        raw = await resp.text()
        data = _decode_json_maybe_multi(raw)

    rows: list[dict[str, Any]] = []
    for node in _iter_dicts(data):
        if not isinstance(node, dict):
            continue
        serial = _serial_from_inventory_node(node)
        hostname = _hostname_from_inventory_node(node)
        if not serial and not hostname:
            continue
        rows.append(
            {
                "serial_number": serial,
                "hostname": hostname,
                "system_mac": _as_str(
                    node.get("system_mac")
                    or node.get("system_mac_address")
                    or node.get("systemMacAddress")
                ),
                "management_ip": _as_str(
                    node.get("ip_address")
                    or node.get("management_ip")
                    or node.get("primary_management_ip")
                    or node.get("primaryManagementIP")
                ),
            }
        )
    return rows


def _rfc3339_utc_from_ns(timestamp_ns: int) -> str:
    """Format epoch nanoseconds as RFC3339 for Go/protobuf JSON timestamp parsing."""
    ns = int(timestamp_ns)
    sec, remainder = divmod(ns, 1_000_000_000)
    dt = datetime.fromtimestamp(sec, tz=UTC)
    if remainder == 0:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    frac = f"{remainder:09d}".rstrip("0")
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{frac}Z"


_GET_CONFIG_RETRY_STATUSES = frozenset({502, 503, 504})
_GET_CONFIG_MAX_ATTEMPTS = 3


async def get_config(
    session: aiohttp.ClientSession,
    url: str,
    device: str,
    timestamp: int,
) -> tuple[str | None, str | None]:
    """POST GetConfig; retries on gateway overload / upstream timeouts (502/503/504)."""
    last_err: str | None = None
    for attempt in range(_GET_CONFIG_MAX_ATTEMPTS):
        ts = now_ns() if attempt else timestamp
        payload: dict[str, Any] = {
            "request": {
                "device_id": device,
                "timestamp": _rfc3339_utc_from_ns(ts),
                "type": "RUNNING_CONFIG",
            }
        }
        try:
            timeout = aiohttp.ClientTimeout(total=180.0)
            async with session.post(url, json=payload, timeout=timeout) as resp:
                raw = await resp.text()
                if resp.status >= 400:
                    preview = (raw or "")[:240].replace("\n", "\\n")
                    err = f"{resp.status}:{preview}" if preview else str(resp.status)
                    last_err = err
                    if (
                        resp.status in _GET_CONFIG_RETRY_STATUSES
                        and attempt < _GET_CONFIG_MAX_ATTEMPTS - 1
                    ):
                        await asyncio.sleep(2**attempt)
                        continue
                    return None, err
                data = _decode_json_maybe_multi(raw)
                cfg = _extract_config_from_response(data)
                if cfg:
                    return cfg, None
                return None, "no_config_in_response"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            if attempt < _GET_CONFIG_MAX_ATTEMPTS - 1:
                await asyncio.sleep(2**attempt)
                continue
            return None, last_err
    return None, last_err or "no_config_in_response"


async def save_config(
    session: aiohttp.ClientSession,
    url: str,
    device: str,
    timestamp: int,
    output_dir: str,
) -> str | None:
    config_text, _ = await get_config(session, url, device, timestamp)
    if not config_text:
        return None
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    filename = f"{device}-{timestamp}.cfg"
    path = Path(output_dir) / filename
    path.write_text(config_text, encoding="utf-8")
    return str(path)


def _inventory_target_matches(target_l: str, serial: str, hostname: str) -> bool:
    if not target_l:
        return False
    s = (serial or "").strip().lower()
    h = (hostname or "").strip().lower()
    if target_l == s or target_l == h:
        return True
    if h and (h == target_l or h.startswith(f"{target_l}.")):
        return True
    return False


async def resolve_device_facts(
    session: aiohttp.ClientSession, inventory_url: str, target: str
) -> dict[str, str]:
    target_l = (target or "").strip().lower()
    if not target_l:
        return {}
    rows = await get_inventory(session, inventory_url)
    for row in rows:
        serial = row.get("serial_number") or ""
        hostname = row.get("hostname") or ""
        if _inventory_target_matches(target_l, serial, hostname):
            return {k: v for k, v in row.items() if v}
    return {}


def now_ns() -> int:
    return int(datetime.now(UTC).timestamp() * 1_000_000_000)
