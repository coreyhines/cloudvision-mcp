"""Device configuration via configstatus Resource API + optional URI fetch."""

from __future__ import annotations

import asyncio
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
    post_json_many_with_bearer_async,
    post_json_with_bearer,
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
    datadict: dict[str, Any], device_ids: list[str]
) -> tuple[str | None, str | None]:
    token = (datadict.get("cvtoken") or "").strip()
    base = _cvp_https_base(str(datadict.get("cvp") or ""))
    if not token:
        return None, "missing_token"
    if not base:
        return None, "missing_cvp"
    url = f"{base}/api/v3/services/compliancecheck.Compliance/GetConfig"
    cafile = datadict.get("cert")
    ids = [d.strip() for d in device_ids if d and d.strip()]
    if not ids:
        return None, "missing_device_id"

    now = datetime.now(UTC)
    now_ns = int(now.timestamp() * 1_000_000_000)
    now_ms = int(now.timestamp() * 1_000)
    now_iso = now.isoformat().replace("+00:00", "Z")

    payloads: list[dict[str, Any]] = []

    def _with_time_anchors(base_payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            base_payload,
            {**base_payload, "time": {"value": now_ns}},
            {**base_payload, "time": now_ns},
            {**base_payload, "timestamp": {"value": now_ns}},
            {**base_payload, "timestamp": now_ns},
            {
                **base_payload,
                "start_time": {"value": now_ns},
                "end_time": {"value": now_ns},
            },
            {
                **base_payload,
                "start_time": now_ns,
                "end_time": now_ns,
            },
            {
                **base_payload,
                "startTime": {"value": now_ns},
                "endTime": {"value": now_ns},
            },
            {**base_payload, "time_ms": now_ms},
            {**base_payload, "time": {"value": now_iso}},
            {**base_payload, "timestamp": {"value": now_iso}},
        ]

    for did in ids:
        payloads.extend(_with_time_anchors({"key": {"device_id": {"value": did}}}))
        payloads.extend(_with_time_anchors({"key": {"device_id": did}}))
        payloads.extend(_with_time_anchors({"device_id": {"value": did}}))
        payloads.extend(_with_time_anchors({"device_id": did}))

    # Async fan-out first for faster retrieval.
    try:
        objs, async_err = asyncio.run(
            post_json_many_with_bearer_async(
                url,
                payloads,
                token,
                cafile=cafile,
            )
        )
    except RuntimeError:
        objs, async_err = [], "event_loop_running"

    if objs:
        for obj in objs:
            if obj is None:
                continue
            text = _extract_running_config_text(obj)
            if text:
                return text, None
        return None, f"async:{async_err}" if async_err else "no_config_in_response"

    last_err = "unknown_error"
    for payload in payloads:
        obj, err = post_json_with_bearer(url, payload, token, cafile=cafile)
        if err:
            last_err = err
            continue
        text = _extract_running_config_text(obj)
        if text:
            return text, None
        last_err = "no_config_in_response"
    return None, last_err


def _inventory_lookup_device(
    datadict: dict[str, Any], target: str
) -> tuple[str | None, str | None, str | None]:
    """Resolve target to (serial/device_id, hostname, error) via inventory REST."""
    token = (datadict.get("cvtoken") or "").strip()
    base = _cvp_https_base(str(datadict.get("cvp") or ""))
    if not token:
        return None, None, "missing_token"
    if not base:
        return None, None, "missing_cvp"
    query_target = (target or "").strip()
    if not query_target:
        return None, None, "missing_target"

    url = f"{base}/api/resources/inventory/v1/Device/all"
    cafile = datadict.get("cert")
    obj, err = get_json_with_bearer(url, token, cafile=cafile)
    if err:
        return None, None, err

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
        key = node.get("key")
        if isinstance(key, dict):
            serial = serial or _as_str(key.get("device_id"))

        if serial.lower() == q or host.lower() == q:
            return (serial or None), (host or None), None

    return None, None, "not_found"


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

    hostname = ""
    connector_device_id = device_id
    inv_rest_serial, inv_rest_host, inv_rest_err = _inventory_lookup_device(
        datadict, device_id
    )
    if inv_rest_serial:
        connector_device_id = inv_rest_serial
    if inv_rest_host:
        hostname = inv_rest_host
    if inv_rest_err and inv_rest_err not in ("not_found",):
        warnings.append(f"inventory_rest_lookup:{inv_rest_err}")

    try:
        inv = grpc_one_inventory_serial(channel, device_id)
        if isinstance(inv, dict):
            hostname = str(inv.get("hostname") or "") or hostname
            serial = str(inv.get("serial_number") or "").strip()
            if serial:
                connector_device_id = serial
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
                    datadict, [connector_device_id, device_id]
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
