"""Resolve user device identifiers to CloudVision serial numbers."""

from __future__ import annotations

import logging
from typing import Any

import grpc

from cvp_mcp.grpc.config import _inventory_lookup_device
from cvp_mcp.grpc.inventory import (
    grpc_all_inventory,
    grpc_one_device_by_hostname,
    grpc_one_inventory_serial,
)
from cvp_mcp.grpc.models import SwitchInfo
from cvp_mcp.grpc.utils import createConnection

ResolvedVia = str  # serial | hostname | rest_inventory | inventory_scan


def _normalize_query(value: str) -> str:
    return (value or "").strip()


def _hostname_candidates(query: str) -> list[str]:
    """Hostname and short-name variants (FQDN -> first label)."""
    q = _normalize_query(query)
    if not q:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for candidate in (q, q.split(".")[0] if "." in q else ""):
        c = (candidate or "").strip()
        if not c:
            continue
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _mac_variants(value: str) -> set[str]:
    raw = _normalize_query(value).lower()
    if not raw:
        return set()
    compact = raw.replace(":", "").replace("-", "").replace(".", "")
    variants = {raw, compact}
    if len(compact) == 12 and all(c in "0123456789abcdef" for c in compact):
        colon = ":".join(compact[i : i + 2] for i in range(0, 12, 2))
        variants.add(colon)
    return variants


def _device_matches_query(device: SwitchInfo, query_l: str) -> bool:
    if not query_l:
        return False
    serial = (device.get("serial_number") or "").strip().lower()
    hostname = (device.get("hostname") or "").strip().lower()
    fqdn = (device.get("fqdn") or "").strip().lower()
    mac = (device.get("system_mac") or "").strip().lower()
    if query_l in {serial, hostname, fqdn}:
        return True
    if mac and query_l in _mac_variants(mac):
        return True
    if hostname and (hostname == query_l or hostname.startswith(f"{query_l}.")):
        return True
    if fqdn and (
        fqdn == query_l
        or fqdn.startswith(f"{query_l}.")
        or query_l.startswith(f"{hostname}.")
    ):
        return True
    return False


def _switch_from_rest_facts(facts: dict[str, str]) -> SwitchInfo | None:
    serial = (facts.get("serial_number") or "").strip()
    if not serial:
        return None
    return SwitchInfo(
        hostname=str(facts.get("hostname") or ""),
        model=str(facts.get("model") or ""),
        serial_number=serial,
        system_mac=str(facts.get("system_mac") or ""),
        version=str(facts.get("version") or facts.get("software_version") or ""),
        streaming_status=str(facts.get("streaming_status") or ""),
        device_type=str(facts.get("device_type") or ""),
        hardware_revision=str(facts.get("hardware_revision") or ""),
        fqdn=str(facts.get("fqdn") or ""),
        domain_name=str(facts.get("domain_name") or ""),
    )


def _resolve_on_channel(
    channel: grpc.Channel,
    datadict: dict[str, Any],
    query: str,
) -> tuple[str | None, SwitchInfo | None, ResolvedVia | None, list[str]]:
    warnings: list[str] = []
    query_l = query.lower()

    try:
        by_serial = grpc_one_inventory_serial(channel, query)
    except grpc.RpcError:
        by_serial = None
        warnings.append("inventory_getone_failed")
    if isinstance(by_serial, dict) and (by_serial.get("serial_number") or "").strip():
        serial = str(by_serial["serial_number"]).strip()
        return serial, by_serial, "serial", warnings

    for host_key in _hostname_candidates(query):
        try:
            by_host = grpc_one_device_by_hostname(channel, host_key)
        except Exception as err:
            logging.debug("inventory by hostname %r: %s", host_key, err)
            by_host = None
        if isinstance(by_host, dict) and (by_host.get("serial_number") or "").strip():
            serial = str(by_host["serial_number"]).strip()
            return serial, by_host, "hostname", warnings

    rest_facts, rest_err = _inventory_lookup_device(datadict, query)
    if rest_facts and (rest_facts.get("serial_number") or "").strip():
        serial = str(rest_facts["serial_number"]).strip()
        switch = _switch_from_rest_facts(rest_facts)
        if switch is None:
            try:
                switch = grpc_one_inventory_serial(channel, serial)
            except grpc.RpcError:
                switch = None
        return (
            serial,
            switch if isinstance(switch, dict) else None,
            "rest_inventory",
            warnings,
        )
    if rest_err and rest_err not in ("not_found", "missing_token", "missing_cvp"):
        warnings.append(f"inventory_rest_lookup:{rest_err}")

    try:
        active, inactive = grpc_all_inventory(channel, exclude_access_points=False)
    except Exception as err:
        logging.debug("inventory scan for %r: %s", query, err)
        warnings.append("inventory_scan_failed")
        return None, None, None, warnings

    for device in active + inactive:
        if not isinstance(device, dict):
            continue
        if _device_matches_query(device, query_l):
            serial = str(device.get("serial_number") or "").strip()
            if serial:
                return serial, device, "inventory_scan", warnings

    return None, None, None, warnings


def resolve_device_to_serial(
    datadict: dict[str, Any],
    device_id: str,
    *,
    channel: grpc.Channel | None = None,
) -> tuple[str | None, SwitchInfo | None, list[str]]:
    """
    Resolve ``device_id`` (serial, hostname, FQDN, or system MAC) to the canonical
    CloudVision serial number used by Connector and Resource API device keys.

    Returns ``(serial_number, device_info, warnings)``. When no device matches,
    ``serial_number`` is ``None``.
    """
    query = _normalize_query(device_id)
    if not query:
        return None, None, ["missing_device_id"]

    if channel is not None:
        serial, info, _via, warnings = _resolve_on_channel(channel, datadict, query)
        return serial, info, warnings

    cvp = (datadict.get("cvp") or "").strip()
    if not cvp:
        return None, None, ["missing_cvp"]
    conn_creds = createConnection(datadict)
    with grpc.secure_channel(cvp, conn_creds) as ch:
        serial, info, _via, warnings = _resolve_on_channel(ch, datadict, query)
    return serial, info, warnings


def resolution_metadata(
    device_id_input: str,
    serial: str,
    *,
    resolved_via: ResolvedVia | None = None,
) -> dict[str, str]:
    """Optional envelope fields describing input -> serial resolution."""
    meta: dict[str, str] = {
        "device_id_input": _normalize_query(device_id_input),
        "device_id_resolved": serial,
    }
    if resolved_via:
        meta["resolved_via"] = resolved_via
    return meta
