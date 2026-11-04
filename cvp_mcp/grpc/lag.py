"""Port-channel membership via CloudVision Connector (device dataset).

Two Sysdb paths, neither of which carries configuration text, so this works
with a service account that is not allowed to read running configs:

- ``Sysdb/lag/input/config/cli/phyIntf/<Ethernet>`` -- configured membership.
  The ``lag`` field is a path pointer whose last element is the Port-Channel
  name; ``mode`` is the LACP mode (active/passive/on).
- ``Sysdb/lag/input/interface/lag/intfStatus/<Port-Channel>/member`` -- live
  (bundled) members; the update keys are the member port names.

Together they answer the question EndpointLocation leaves open: a MAC is
learned on ``Port-Channel8`` -- which physical port is that?
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from cloudvision.Connector.codec import Wildcard
from cloudvision.Connector.grpc_client import GRPCClient, create_query

from cvp_mcp.env import normalize_api_token
from cvp_mcp.grpc.connector import get_device_path_keyed, serialize_cloudvision_data
from cvp_mcp.grpc.envelope import tool_envelope

DATA_SOURCE = "connector:device:Sysdb/lag"
_PO_RE = re.compile(r"(Port-Channel\d+)")


def _cvp_addr(datadict: dict[str, Any]) -> str:
    cvp = (datadict.get("cvp") or "").strip()
    if cvp and ":" not in cvp:
        cvp = f"{cvp}:443"
    return cvp


def port_channel_name(ptr: Any) -> str | None:
    """Port-Channel name from a Sysdb path pointer.

    The Connector decodes pointers as ``codec.Path`` (keys in ``_keys``,
    ``repr`` is the key list); tests and REST responses hand us plain lists
    or ``{"ptr": [...]}`` dicts. Accept all of them.
    """
    if ptr is None:
        return None
    keys = getattr(ptr, "_keys", None)
    if keys is None and isinstance(ptr, Mapping):
        keys = ptr.get("ptr")
    if keys is None and isinstance(ptr, (list, tuple)):
        keys = ptr
    if keys:
        last = str(keys[-1])
        return last if _PO_RE.fullmatch(last) else None
    m = _PO_RE.search(str(ptr))
    return m.group(1) if m else None


def _enum_name(value: Any) -> str | None:
    """LACP mode: an enum dict ({"Name": "lacpModeActive", "Value": 1}) or a
    string; the EOS enum prefix is dropped so callers see "active"/"passive"/"on"."""
    if isinstance(value, Mapping):
        value = value.get("Name") or value.get("name")
    if value is None:
        return None
    name = str(value)
    if name.startswith("lacpMode"):
        name = name[len("lacpMode") :].lower() or name
    return name


def _natural_key(name: str) -> tuple[Any, ...]:
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name))


def parse_lag_membership(
    phy_cfg: Mapping[str, Any], live_members: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Rows per Port-Channel from the configured and live membership maps.

    ``phy_cfg`` is ``{<Ethernet>: {"lag": <ptr>, "mode": <enum>, ...}}``;
    ``live_members`` is ``{<Port-Channel>: {<Ethernet>: ...}}``.
    """
    rows: dict[str, dict[str, Any]] = {}

    def row(po: str) -> dict[str, Any]:
        return rows.setdefault(
            po,
            {
                "port_channel": po,
                "configured_members": [],
                "active_members": [],
                "lacp_mode": None,
            },
        )

    for port, attrs in phy_cfg.items():
        if not isinstance(attrs, Mapping):
            continue
        po = port_channel_name(attrs.get("lag"))
        if not po:
            continue
        r = row(po)
        r["configured_members"].append(str(port))
        mode = _enum_name(attrs.get("mode"))
        if mode and not r["lacp_mode"]:
            r["lacp_mode"] = mode

    for po, members in live_members.items():
        po_name = port_channel_name([po]) or str(po)
        if not _PO_RE.fullmatch(po_name):
            continue
        r = row(po_name)
        if isinstance(members, Mapping):
            r["active_members"].extend(str(m) for m in members)

    out = []
    for po in sorted(rows, key=_natural_key):
        r = rows[po]
        r["configured_members"].sort(key=_natural_key)
        r["active_members"] = sorted(set(r["active_members"]), key=_natural_key)
        out.append(r)
    return out


def _live_members_by_port_channel(client: Any, device_id: str) -> dict[str, dict]:
    """``{<Port-Channel>: {<Ethernet>: ...}}`` from ``.../intfStatus/*/member``.

    Every notification's last path element is the literal ``member``, so
    ``get_device_path_keyed`` (which keys by the last element) would collapse
    all Port-Channels onto one entry and keep only the last. Key by the
    element before it -- the Port-Channel -- instead.
    """
    path = [
        "Sysdb",
        "lag",
        "input",
        "interface",
        "lag",
        "intfStatus",
        Wildcard(),
        "member",
    ]
    out: dict[str, dict] = {}
    query = [create_query([(path, [])], device_id, "device")]
    for batch in client.get(query):
        for notif in batch["notifications"]:
            elements = notif.get("path_elements") or []
            if len(elements) < 2:
                continue
            po = port_channel_name([elements[-2]])
            if not po:
                continue
            updates = serialize_cloudvision_data(notif.get("updates") or {})
            if isinstance(updates, Mapping):
                out.setdefault(po, {}).update({str(k): v for k, v in updates.items()})
    return out


def _connector_lag_maps(datadict: dict[str, Any], device_id: str) -> tuple[dict, dict]:
    token = normalize_api_token(datadict.get("cvtoken"))
    cfg_path = ["Sysdb", "lag", "input", "config", "cli", "phyIntf", Wildcard()]
    with GRPCClient(grpcAddr=_cvp_addr(datadict), tokenValue=token) as client:
        phy_cfg = get_device_path_keyed(client, device_id, cfg_path)
        live = _live_members_by_port_channel(client, device_id)
    return phy_cfg, live


def grpc_get_lag_members(
    datadict: dict[str, Any], device_id: str, port_channel: str | None = None
) -> dict[str, Any]:
    """Port-Channel -> member ports for one device, optionally one Port-Channel."""
    device_id = (device_id or "").strip()
    wanted = (port_channel or "").strip()
    if wanted:
        # Accept "Po8", "port-channel8", "8".
        m = re.fullmatch(r"(?i)(?:port-?channel|po)?\s*(\d+)", wanted)
        if m:
            wanted = f"Port-Channel{m.group(1)}"
    if not device_id:
        return tool_envelope(
            device_id=None,
            data_source=DATA_SOURCE,
            coverage="none",
            items=[],
            warnings=["missing_device_id"],
        )
    try:
        phy_cfg, live = _connector_lag_maps(datadict, device_id)
        items = parse_lag_membership(phy_cfg, live)
        warnings: list[str] = []
        if wanted:
            items = [r for r in items if r["port_channel"] == wanted]
            if not items:
                warnings.append("port_channel_not_found")
        if not items and not warnings:
            warnings.append("no_lag_data_returned")
        obj: dict[str, Any] = {"port_channel_filter": wanted or None}
        if not live:
            warnings.append("live_member_status_unavailable")
        return tool_envelope(
            device_id=device_id,
            data_source=DATA_SOURCE,
            coverage="full" if items else "none",
            items=items,
            obj=obj,
            warnings=warnings,
        )
    except (
        Exception
    ) as e:  # noqa: BLE001 - every member returns an envelope, never raises
        logging.error("get_lag_members %s: %s", device_id, e)
        return tool_envelope(
            device_id=device_id,
            data_source=DATA_SOURCE,
            coverage="none",
            items=[],
            warnings=[str(e)],
        )
