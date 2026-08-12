# cvp_mcp/grpc/endpoint_seed.py
from __future__ import annotations

import re
from typing import Any

_MAC_HEX = re.compile(r"[^0-9a-fA-F]")


def normalize_endpoint_search_key(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    compact = _MAC_HEX.sub("", s)
    if len(compact) == 12 and all(c in "0123456789abcdefABCDEF" for c in compact):
        compact = compact.lower()
        return ":".join(compact[i : i + 2] for i in range(0, 12, 2))
    return s.lower()


def _add(bucket: list[str], seen: set[str], raw: Any) -> None:
    if raw is None:
        return
    if isinstance(raw, (list, tuple)):
        for item in raw:
            _add(bucket, seen, item)
        return
    key = normalize_endpoint_search_key(str(raw))
    if not key or key in seen:
        return
    seen.add(key)
    bucket.append(key)


def extract_endpoint_search_keys(lldp_rows: list[dict]) -> list[str]:
    ips: list[str] = []
    macs: list[str] = []
    names: list[str] = []
    seen: set[str] = set()
    for row in lldp_rows or []:
        if not isinstance(row, dict):
            continue
        for field in (
            "management_addresses",
            "management_address",
            "mgmt_addr",
            "remote_management_addresses",
            "remote_management_address",
        ):
            _add(ips, seen, row.get(field))
        for field in (
            "remote_chassis_id",
            "chassis_id",
            "chassis_id_str",
            "eth_addr",
            "remote_eth_addr",
        ):
            _add(macs, seen, row.get(field))
        for field in (
            "system_name",
            "system_name_str",
            "remote_system_name",
        ):
            _add(names, seen, row.get(field))
    return ips + macs + names
