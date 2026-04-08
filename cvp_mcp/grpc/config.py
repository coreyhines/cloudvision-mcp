"""Device configuration via configstatus Resource API + optional URI fetch."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

import grpc
from arista.configstatus.v1 import models as cm
from arista.configstatus.v1 import services as cs
from google.protobuf import wrappers_pb2 as wrappers

from cvp_mcp.grpc.config_connector import (
    _looks_like_eos_running_config,
    connector_fetch_running_config_text,
)
from cvp_mcp.grpc.envelope import tool_envelope
from cvp_mcp.grpc.inventory import grpc_one_inventory_serial
from cvp_mcp.grpc.uri_fetch import (
    fetch_uri_with_bearer,
    get_json_with_bearer,
    post_raw_with_bearer,
)
from cvp_mcp.grpc.utils import RPC_TIMEOUT, serialize_arista_protobuf

_MAX_RUNNING_CONFIG_CHARS = 1_500_000


def _cvp_https_base(cvp: str) -> str:
    host = (cvp or "").strip()
    host = re.sub(r"^https?://", "", host)
    host = host.split("/")[0]
    if host.endswith(":443"):
        host = host[:-4]
    return f"https://{host}" if host else ""


def _extract_running_config_text(obj: Any) -> str | None:
    if isinstance(obj, str):
        return obj if _looks_like_eos_running_config(obj) else None
    if isinstance(obj, dict):
        for k in (
            "runningConfig",
            "running_config",
            "config",
            "text",
            "body",
            "contents",
        ):
            v = obj.get(k)
            if isinstance(v, str) and _looks_like_eos_running_config(v):
                return v
            if isinstance(v, dict):
                inner = v.get("value")
                if isinstance(inner, str) and _looks_like_eos_running_config(inner):
                    return inner
        for v in obj.values():
            hit = _extract_running_config_text(v)
            if hit:
                return hit
    if isinstance(obj, list):
        for v in obj:
            hit = _extract_running_config_text(v)
            if hit:
                return hit
    return None


def _fetch_running_config_from_compliance_rest(
    datadict: dict[str, Any], serials: list[str]
) -> tuple[str | None, str | None]:
    token = (datadict.get("cvtoken") or "").strip()
    base = _cvp_https_base(str(datadict.get("cvp") or ""))
    if not token:
        return None, "missing_token"
    if not base:
        return None, "missing_cvp"
    url = f"{base}/api/v3/services/compliancecheck.Compliance/GetConfig"
    cafile = datadict.get("cert")
    ids = [d.strip() for d in serials if d and d.strip()]
    if not ids:
        return None, "missing_serial"

    now = datetime.now(UTC)
    now_ns = int(now.timestamp() * 1_000_000_000)
    raw_bodies: list[tuple[str, str]] = []
    for serial in ids:
        # Service endpoint expects top-level string input.
        canonical_req = {
            "device_id": serial,
            "timestamp": now_ns,
            "type": "RUNNING_CONFIG",
        }
        raw_bodies.extend(
            [
                (serial, "text/plain"),
                (json.dumps(serial), "application/json"),
                (json.dumps(canonical_req), "text/plain"),
            ]
        )

    # Service endpoint expects string input; do not send object payloads.
    last_err = "unknown_error"
    for raw_body, ctype in raw_bodies:
        raw_resp, raw_err = post_raw_with_bearer(
            url,
            raw_body,
            token,
            cafile=cafile,
            content_type=ctype,
        )
        if raw_err:
            last_err = raw_err
            continue
        hit = _extract_running_config_text(raw_resp)
        if hit:
            return hit, None
        if raw_resp and _looks_like_eos_running_config(raw_resp):
            return raw_resp, None
        if raw_resp and "hostname " in raw_resp:
            return raw_resp, None
        last_err = "raw_no_config_in_response"
    return None, last_err


def _inventory_lookup_device(
    datadict: dict[str, Any], target: str
) -> tuple[dict[str, str], str | None]:
    """Resolve target to reusable device facts via inventory REST."""
    token = (datadict.get("cvtoken") or "").strip()
    base = _cvp_https_base(str(datadict.get("cvp") or ""))
    if not token:
        return {}, "missing_token"
    if not base:
        return {}, "missing_cvp"
    query_target = (target or "").strip()
    if not query_target:
        return {}, "missing_target"

    url = f"{base}/api/resources/inventory/v1/Device/all"
    cafile = datadict.get("cert")
    obj, err = get_json_with_bearer(url, token, cafile=cafile)
    if err:
        return {}, err

    def _as_str(x: Any) -> str:
        if isinstance(x, str):
            return x
        if isinstance(x, dict):
            v = x.get("value")
            if isinstance(v, str):
                return v
        return ""

    def _iter_dicts(x: Any):
        if isinstance(x, dict):
            yield x
            for v in x.values():
                yield from _iter_dicts(v)
        elif isinstance(x, list):
            for v in x:
                yield from _iter_dicts(v)

    q = query_target.lower()
    for node in _iter_dicts(obj):
        serial = _as_str(node.get("device_id") or node.get("serial_number"))
        host = _as_str(node.get("hostname"))
        mgmt_ip = _as_str(
            node.get("ip_address")
            or node.get("management_ip")
            or node.get("primary_management_ip")
            or node.get("primaryManagementIP")
        )
        system_mac = _as_str(
            node.get("system_mac_address")
            or node.get("system_mac")
            or node.get("systemMacAddress")
            or node.get("mac_address")
            or node.get("mac")
        )
        key = node.get("key")
        if isinstance(key, dict):
            serial = serial or _as_str(key.get("device_id"))

        if serial.lower() == q or host.lower() == q:
            facts = {
                "device_id_input": query_target,
                "serial_number": serial,
                "hostname": host,
                "management_ip": mgmt_ip,
                "system_mac": system_mac,
            }
            return {k: v for k, v in facts.items() if v}, None

    return {}, "not_found"


def grpc_get_device_config(
    channel: grpc.Channel,
    datadict: dict[str, Any],
    device_id: str,
    *,
    include_running_config: bool = False,
) -> dict[str, Any]:
    """Return config summary URIs + optional running-config body."""
    warnings: list[str] = []
    device_id = (device_id or "").strip()
    if not device_id:
        return tool_envelope(
            device_id=device_id or None,
            data_source="resource_api:configstatus.v1",
            coverage="none",
            obj={},
            warnings=["missing_device_id"],
        )

    token = (datadict.get("cvtoken") or "").strip()
    cafile = datadict.get("cert")

    summary_stub = cs.SummaryServiceStub(channel)
    cfg_stub = cs.ConfigurationServiceStub(channel)

    device_facts: dict[str, str] = {"device_id_input": device_id}
    hostname = ""
    connector_device_id = device_id
    inv_rest_facts, inv_rest_err = _inventory_lookup_device(datadict, device_id)
    if inv_rest_facts:
        device_facts.update(inv_rest_facts)
        hostname = device_facts.get("hostname", "")
        connector_device_id = device_facts.get("serial_number", connector_device_id)
    if inv_rest_err and inv_rest_err not in ("not_found",):
        warnings.append(f"inventory_rest_lookup:{inv_rest_err}")

    try:
        inv = grpc_one_inventory_serial(channel, device_id)
        if isinstance(inv, dict):
            hostname = str(inv.get("hostname") or "") or hostname
            if hostname:
                device_facts["hostname"] = hostname
            serial = str(inv.get("serial_number") or "").strip()
            if serial:
                connector_device_id = serial
                device_facts["serial_number"] = serial
            mgmt_ip = str(
                inv.get("management_ip")
                or inv.get("primary_management_ip")
                or inv.get("ip_address")
                or ""
            ).strip()
            if mgmt_ip:
                device_facts["management_ip"] = mgmt_ip
            system_mac = str(
                inv.get("system_mac") or inv.get("system_mac_address") or ""
            ).strip()
            if system_mac:
                device_facts["system_mac"] = system_mac
    except Exception as e:
        logging.debug("inventory for hostname: %s", e)

    summary_obj: dict[str, Any] = {}
    try:
        sreq = cs.SummaryRequest(
            key=cm.SummaryKey(device_id=wrappers.StringValue(value=device_id))
        )
        sresp = summary_stub.GetOne(sreq, timeout=RPC_TIMEOUT)
        if sresp and sresp.HasField("value"):
            summary_obj = serialize_arista_protobuf(sresp.value.summary)
    except Exception as e:
        logging.error("configsummary GetOne: %s", e)
        warnings.append(f"summary_fetch_failed:{e}")

    designed_uri = ""
    running_uri = ""
    try:
        for cfg_type, attr in (
            (cm.CONFIG_TYPE_DESIGNED_CONFIG, "designed"),
            (cm.CONFIG_TYPE_RUNNING_CONFIG, "running"),
        ):
            creq = cs.ConfigurationRequest(
                key=cm.ConfigKey(
                    device_id=wrappers.StringValue(value=device_id),
                    type=cfg_type,
                )
            )
            cresp = cfg_stub.GetOne(creq, timeout=RPC_TIMEOUT)
            if cresp and cresp.HasField("value") and cresp.value.HasField("uri"):
                u = cresp.value.uri.value
                if attr == "designed":
                    designed_uri = u
                else:
                    running_uri = u
    except Exception as e:
        logging.error("configuration GetOne: %s", e)
        warnings.append(f"configuration_uri_fetch_failed:{e}")

    running_config_text: str | None = None
    running_config_source: str | None = None
    if include_running_config:
        uri = running_uri or summary_obj.get("running_config_uri", "")
        if isinstance(uri, dict):
            uri = uri.get("value", "")
        if uri and token:
            text, err = fetch_uri_with_bearer(str(uri), token, cafile=cafile)
            running_config_text = text
            running_config_source = "resource_uri"
            if err:
                warnings.append(f"running_config_body:{err}")
                running_config_source = None
        elif not uri:
            warnings.append("no_running_config_uri")
        elif not token:
            warnings.append("no_token_for_uri_fetch")

        if include_running_config and (
            not running_config_text or not running_config_text.strip()
        ):
            compliance_text, compliance_err = (
                _fetch_running_config_from_compliance_rest(
                    datadict,
                    [device_facts.get("serial_number", ""), connector_device_id],
                )
            )
            if compliance_text:
                running_config_text = compliance_text
                running_config_source = "compliance_rest"
            elif compliance_err:
                warnings.append(f"compliance_get_config:{compliance_err}")

        if (
            include_running_config
            and (not running_config_text or not running_config_text.strip())
            and datadict.get("cvtoken")
        ):
            fb_text, fb_tried, fb_warn = connector_fetch_running_config_text(
                datadict, connector_device_id
            )
            if fb_text:
                if len(fb_text) > _MAX_RUNNING_CONFIG_CHARS:
                    running_config_text = fb_text[:_MAX_RUNNING_CONFIG_CHARS]
                    warnings.append(
                        f"running_config_truncated_to_{_MAX_RUNNING_CONFIG_CHARS}_chars"
                    )
                else:
                    running_config_text = fb_text
                running_config_source = "connector"
                obj_fb = {
                    "connector_fallback_paths_tried": fb_tried,
                }
            else:
                obj_fb = {"connector_fallback_paths_tried": fb_tried}
            for w in fb_warn:
                if w not in warnings:
                    warnings.append(w)
        else:
            obj_fb = {}
    else:
        obj_fb = {}

    data_source = "resource_api:configstatus.v1"
    if include_running_config and running_config_source == "compliance_rest":
        data_source += "+rest:compliancecheck.getconfig"
    if include_running_config and running_config_source == "connector":
        data_source += "+connector:sysdb_smash_analytics"

    obj = {
        "hostname": hostname,
        "device_id": device_id,
        "device_facts": device_facts,
        "config_summary": summary_obj,
        "designed_config_uri": designed_uri,
        "running_config_uri": running_uri,
        **obj_fb,
    }
    if include_running_config:
        obj["running_config_text"] = running_config_text
        obj["running_config_source"] = running_config_source

    coverage = "full"
    if warnings:
        coverage = "partial"

    return tool_envelope(
        device_id=device_id,
        data_source=data_source,
        coverage=coverage,
        obj=obj,
        warnings=warnings,
    )
