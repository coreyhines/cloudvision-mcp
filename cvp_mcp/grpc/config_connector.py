"""Connector (device + analytics) fallback when configstatus Resource API is unavailable."""

from __future__ import annotations

import logging
import re
from typing import Any

from cloudvision.Connector.codec import Wildcard
from cloudvision.Connector.grpc_client import GRPCClient

from cvp_mcp.env import normalize_api_token
from cvp_mcp.grpc.connector import get, get_device_path, serialize_cloudvision_data


def _grpc_addr(datadict: dict[str, Any]) -> str:
    cvp = (datadict.get("cvp") or "").strip()
    if cvp and ":" not in cvp:
        cvp = f"{cvp}:443"
    return cvp


def _looks_like_eos_running_config(text: str) -> bool:
    if not text or len(text) < 40:
        return False
    t = text.lstrip()
    if "!" in text[:500] or t.startswith("!"):
        return True
    return bool(
        re.search(
            r"(?m)^\s*(hostname|interface |ip routing|spanning-tree |router |vlan )",
            text,
        )
    )


def _extract_config_strings(
    obj: Any, *, max_depth: int = 14, _depth: int = 0
) -> list[str]:
    """Collect string leaves that plausibly hold EOS config text."""
    if _depth > max_depth:
        return []
    out: list[str] = []
    if isinstance(obj, str):
        if _looks_like_eos_running_config(obj):
            out.append(obj)
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in (
                "runningconfig",
                "running_config",
                "startupconfig",
                "startup_config",
                "config",
                "configtext",
                "contents",
                "body",
                "text",
                "cli",
                "buffer",
            ):
                if isinstance(v, str) and _looks_like_eos_running_config(v):
                    out.append(v)
            out.extend(
                _extract_config_strings(v, max_depth=max_depth, _depth=_depth + 1)
            )
        return out
    if isinstance(obj, (list, tuple)):
        for item in obj:
            out.extend(
                _extract_config_strings(item, max_depth=max_depth, _depth=_depth + 1)
            )
        return out
    return out


def _best_config_candidate(strings: list[str]) -> str | None:
    if not strings:
        return None
    return max(strings, key=len)


def _unwrap_link_ptr(val: Any) -> str | None:
    """Normalize CloudVision / protobuf-like link fields to a UUID string or None."""
    if val is None:
        return None
    if isinstance(val, str):
        return val.strip() or None
    if isinstance(val, dict):
        inner = val.get("value")
        if inner is not None and isinstance(inner, str):
            return inner.strip() or None
        if inner is not None:
            s = str(inner).strip()
            return s or None
    s = str(val).strip()
    return s or None


def _reconstruct_running_config_lines(raw: Any) -> str | None:
    """
    Rebuild running-config text from Telemetry-style ``Config/running/lines``.

    Each key is a line id (e.g. UUID); each value is a Map with ``text``,
    ``next``, and ``previous`` forming a doubly linked list.
    """
    if not isinstance(raw, dict) or len(raw) < 2:
        return None

    nodes: dict[str, dict[str, Any]] = {}
    for k, v in raw.items():
        if not isinstance(v, dict) or "text" not in v:
            continue
        text = v.get("text")
        if not isinstance(text, str):
            continue
        nodes[str(k)] = v

    if len(nodes) < 2:
        return None

    def prev_of(node: dict[str, Any]) -> str | None:
        return _unwrap_link_ptr(node.get("previous"))

    def next_of(node: dict[str, Any]) -> str | None:
        return _unwrap_link_ptr(node.get("next"))

    heads = [
        kid for kid in nodes if (p := prev_of(nodes[kid])) is None or p not in nodes
    ]
    if not heads:
        return None

    start = heads[0]
    if len(heads) > 1:
        roots = [h for h in heads if prev_of(nodes[h]) is None]
        if len(roots) == 1:
            start = roots[0]

    lines: list[str] = []
    seen: set[str] = set()
    cur: str | None = start
    while cur and cur in nodes and cur not in seen:
        seen.add(cur)
        t = nodes[cur].get("text")
        if isinstance(t, str):
            lines.append(t)
        nxt = next_of(nodes[cur])
        if not nxt or nxt not in nodes:
            break
        cur = nxt

    text = "\n".join(lines)
    if len(text) < 40:
        return None
    return text


def _process_raw_for_config(raw: Any, label: str) -> list[str]:
    """Apply ``/Config/running/lines`` reconstruction, then legacy string heuristics."""
    out: list[str] = []
    if isinstance(raw, dict):
        linked = _reconstruct_running_config_lines(raw)
        if linked:
            logging.debug(
                "config fallback %s: linked running/lines (%s chars)",
                label,
                len(linked),
            )
            out.append(linked)
    out.extend(_extract_config_strings(raw))
    return out


def connector_fetch_running_config_text(
    datadict: dict[str, Any],
    device_id: str,
) -> tuple[str | None, list[str], list[str]]:
    """
    Try Sysdb/Smash/analytics paths for a running-config-like blob.

    Returns (text_or_none, path_labels_tried, warnings).
    """
    warnings: list[str] = []
    tried: list[str] = []
    token = normalize_api_token(datadict.get("cvtoken"))
    device_id = (device_id or "").strip()
    if not device_id:
        return None, tried, ["missing_device_id"]

    device_paths: list[tuple[str, list]] = [
        # Telemetry Browser: /Config/running/lines (linked list of line maps, not one string)
        (
            "device:Config/running/lines",
            [device_id, "Config", "running", "lines"],
        ),
        ("device:Sysdb/config", [device_id, "Sysdb", "config", Wildcard()]),
        ("device:Sysdb/archive", [device_id, "Sysdb", "archive", Wildcard()]),
        ("device:Sysdb/boot", [device_id, "Sysdb", "boot", Wildcard()]),
        ("device:Sysdb/devicemgr", [device_id, "Sysdb", "devicemgr", Wildcard()]),
        (
            "device:Sysdb/agents",
            [device_id, "Sysdb", "agents", Wildcard()],
        ),
        ("device:Smash/cli", [device_id, "Smash", "cli", Wildcard()]),
        (
            "device:Smash/management",
            [device_id, "Smash", "management", Wildcard()],
        ),
        ("device:Smash/nvram", [device_id, "Smash", "nvram", Wildcard()]),
    ]

    analytics_paths: list[tuple[str, list]] = [
        (
            "analytics:Devices/.../versioned-data/Device",
            ["Devices", device_id, "versioned-data", "Device"],
        ),
        (
            "analytics:Devices/.../versioned-data/eos",
            ["Devices", device_id, "versioned-data", "eos"],
        ),
        (
            "analytics:Devices/.../TPSA",
            ["Devices", device_id, "TPSA", Wildcard()],
        ),
    ]

    collected: list[str] = []

    try:
        with GRPCClient(grpcAddr=_grpc_addr(datadict), tokenValue=token) as client:
            for label, path in device_paths:
                tried.append(label)
                try:
                    raw = serialize_cloudvision_data(
                        get_device_path(client, device_id, path)
                    )
                    found = _process_raw_for_config(raw, label)
                    if found:
                        collected.extend(found)
                        logging.debug(
                            "config fallback %s: %s candidate(s)", label, len(found)
                        )
                except Exception as e:
                    logging.debug("config fallback path %s: %s", label, e)

            for label, path in analytics_paths:
                tried.append(label)
                try:
                    raw = serialize_cloudvision_data(
                        get(client, "analytics", path, dtype="device")
                    )
                    found = _process_raw_for_config(raw, label)
                    if found:
                        collected.extend(found)
                        logging.debug(
                            "config fallback %s: %s candidate(s)", label, len(found)
                        )
                except Exception as e:
                    logging.debug("config fallback path %s: %s", label, e)

    except Exception as e:
        logging.error("config connector client: %s", e)
        warnings.append(f"connector_client:{e}")
        return None, tried, warnings

    best = _best_config_candidate(collected)
    if not best:
        warnings.append("connector_no_running_config_blob_found")
    return best, tried, warnings
