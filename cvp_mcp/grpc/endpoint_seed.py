# cvp_mcp/grpc/endpoint_seed.py
from __future__ import annotations

import logging
import re
from typing import Any

from cvp_mcp.grpc.inventory import grpc_all_inventory
from cvp_mcp.grpc.lldp import grpc_get_lldp_neighbors
from cvp_mcp.grpc.utils import _is_lab_device

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


def _is_eligible_switch(dev: dict[str, Any], *, include_lab_devices: bool) -> bool:
    if dev.get("streaming_status") != "Active":
        return False
    device_type = dev.get("device_type")
    if device_type == "EOS":
        return True
    if _is_lab_device(dev):
        return include_lab_devices
    return False


def _inventory_by_serial(
    active: list[dict[str, Any]], inactive: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for dev in active + inactive:
        serial = (dev.get("serial_number") or "").strip()
        if serial:
            out[serial] = dev
    return out


def _select_switches_to_scan(
    active: list[dict[str, Any]],
    inactive: list[dict[str, Any]],
    *,
    device_serials: list[str] | None,
    include_lab_devices: bool,
) -> list[dict[str, Any]]:
    by_serial = _inventory_by_serial(active, inactive)
    if device_serials is not None:
        selected: list[dict[str, Any]] = []
        for raw in device_serials:
            serial = (raw or "").strip()
            if not serial:
                continue
            dev = by_serial.get(serial)
            if dev is not None:
                if _is_eligible_switch(dev, include_lab_devices=include_lab_devices):
                    selected.append(dev)
            else:
                selected.append({"serial_number": serial, "model": ""})
        return selected
    return [
        dev
        for dev in active
        if _is_eligible_switch(dev, include_lab_devices=include_lab_devices)
    ]


def seed_endpoint_search_keys(
    datadict: dict[str, Any],
    channel,
    *,
    device_serials: list[str] | None = None,
    include_lab_devices: bool = False,
) -> dict[str, Any]:
    warnings: list[str] = []
    lldp_rows: list[dict[str, Any]] = []
    switches_scanned = 0
    lldp_neighbor_rows = 0

    active, inactive = grpc_all_inventory(channel, exclude_access_points=True)
    switches = _select_switches_to_scan(
        active,
        inactive,
        device_serials=device_serials,
        include_lab_devices=include_lab_devices,
    )

    for dev in switches:
        serial = (dev.get("serial_number") or "").strip()
        if not serial:
            continue
        model = str(dev.get("model") or "")
        switches_scanned += 1
        try:
            out = grpc_get_lldp_neighbors(
                datadict,
                serial,
                device_model=model,
            )
        except Exception as exc:
            logging.warning("endpoint seed LLDP failed %s: %s", serial, exc)
            warnings.append(f"lldp_scan_failed:{serial}")
            continue
        if not isinstance(out, dict):
            continue
        for w in out.get("warnings") or []:
            if w and w not in warnings:
                warnings.append(w)
        items = out.get("items") or []
        if isinstance(items, list):
            lldp_rows.extend(row for row in items if isinstance(row, dict))
            lldp_neighbor_rows += len(items)

    if switches_scanned == 0:
        warnings.append("no_switches_to_scan")

    search_keys = extract_endpoint_search_keys(lldp_rows)
    if not search_keys:
        warnings.append("no_lldp_search_keys")

    return {
        "search_keys": search_keys,
        "seed_stats": {
            "switches_scanned": switches_scanned,
            "lldp_neighbor_rows": lldp_neighbor_rows,
            "unique_search_keys": len(search_keys),
        },
        "warnings": warnings,
    }
