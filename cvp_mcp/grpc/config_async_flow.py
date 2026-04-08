"""Minimal async flow for inventory + running-config retrieval."""

from __future__ import annotations

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


async def get_inventory(
    session: aiohttp.ClientSession,
    url: str,
) -> list[dict[str, Any]]:
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json(content_type=None)

    rows: list[dict[str, Any]] = []
    for node in _iter_dicts(data):
        serial = _as_str(node.get("device_id") or node.get("serial_number"))
        hostname = _as_str(node.get("hostname"))
        if not serial and not hostname:
            continue
        rows.append(
            {
                "serial_number": serial,
                "hostname": hostname,
                "system_mac": _as_str(
                    node.get("system_mac") or node.get("system_mac_address")
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


async def get_config(
    session: aiohttp.ClientSession,
    url: str,
    device: str,
    timestamp: int,
) -> str | None:
    payload = {
        "request": {
            "device_id": device,
            "timestamp": timestamp,
            "type": "RUNNING_CONFIG",
        }
    }
    async with session.post(url, json=payload) as resp:
        resp.raise_for_status()
        data = await resp.json(content_type=None)
    return _extract_config_from_response(data)


async def save_config(
    session: aiohttp.ClientSession,
    url: str,
    device: str,
    timestamp: int,
    output_dir: str,
) -> str | None:
    config_text = await get_config(session, url, device, timestamp)
    if not config_text:
        return None
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    filename = f"{device}-{timestamp}.cfg"
    path = Path(output_dir) / filename
    path.write_text(config_text, encoding="utf-8")
    return str(path)


async def resolve_device_facts(
    session: aiohttp.ClientSession, inventory_url: str, target: str
) -> dict[str, str]:
    target_l = (target or "").strip().lower()
    if not target_l:
        return {}
    rows = await get_inventory(session, inventory_url)
    for row in rows:
        serial = (row.get("serial_number") or "").lower()
        hostname = (row.get("hostname") or "").lower()
        if target_l in {serial, hostname}:
            return {k: v for k, v in row.items() if v}
    return {}


def now_ns() -> int:
    return int(datetime.now(UTC).timestamp() * 1_000_000_000)
