"""Device configuration via configstatus Resource API + optional URI fetch."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import re
from collections.abc import Coroutine
from typing import Any, TypeVar

import aiohttp
import grpc
from arista.configstatus.v1 import models as cm
from arista.configstatus.v1 import services as cs
from google.protobuf import wrappers_pb2 as wrappers

from cvp_mcp.grpc.config_async_flow import (
    get_config as async_get_config,
)
from cvp_mcp.grpc.config_async_flow import (
    now_ns,
    resolve_device_facts,
)
from cvp_mcp.grpc.config_connector import (
    _looks_like_eos_running_config,
    connector_fetch_running_config_text,
)
from cvp_mcp.grpc.envelope import tool_envelope
from cvp_mcp.grpc.inventory import grpc_one_inventory_serial
from cvp_mcp.grpc.uri_fetch import (
    fetch_uri_with_bearer,
)
from cvp_mcp.grpc.utils import RPC_TIMEOUT, serialize_arista_protobuf

_T = TypeVar("_T")

_MAX_RUNNING_CONFIG_CHARS = 1_500_000


def _run_async_in_sync_context(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run ``coro`` from sync code when an event loop may already be running (e.g. MCP)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(asyncio.run, coro)
        return fut.result()


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
    ids = [d.strip() for d in serials if d and d.strip()]
    if not ids:
        return None, "missing_serial"
    cafile = datadict.get("cert")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    timestamp = now_ns()

    async def _run() -> tuple[str | None, str | None]:
        timeout = aiohttp.ClientTimeout(total=60.0)
        ssl_ctx = None
        if cafile:
            import ssl

            ssl_ctx = ssl.create_default_context(cafile=cafile)
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(
            headers=headers, timeout=timeout, connector=connector
        ) as session:
            for serial in ids:
                try:
                    text, err = await async_get_config(session, url, serial, timestamp)
                except Exception as e:  # noqa: BLE001
                    return None, str(e)
                if text and _looks_like_eos_running_config(text):
                    return text, None
                if err:
                    return None, err
            return None, "no_config_in_response"

    return _run_async_in_sync_context(_run())


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

    inventory_url = f"{base}/api/resources/inventory/v1/Device/all"
    cafile = datadict.get("cert")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async def _run() -> tuple[dict[str, str], str | None]:
        timeout = aiohttp.ClientTimeout(total=60.0)
        ssl_ctx = None
        if cafile:
            import ssl

            ssl_ctx = ssl.create_default_context(cafile=cafile)
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(
            headers=headers, timeout=timeout, connector=connector
        ) as session:
            facts = await resolve_device_facts(session, inventory_url, query_target)
            return (
                {**{"device_id_input": query_target}, **facts} if facts else {},
                None if facts else "not_found",
            )

    return _run_async_in_sync_context(_run())


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

    attempted_sources: list[str] = []
    if (
        summary_obj
        or designed_uri
        or running_uri
        or any(
            w.startswith("summary_fetch_failed")
            or w.startswith("configuration_uri_fetch_failed")
            for w in warnings
        )
    ):
        attempted_sources.append("resource_api:configstatus.v1")
    if include_running_config:
        attempted_sources.append("service_api:compliancecheck.getconfig")
        if running_config_source == "connector":
            attempted_sources.append("connector:sysdb_smash_analytics")

    # Prefer actual winning source; otherwise report attempted path(s) explicitly.
    if running_config_source == "resource_uri":
        data_source = "resource_api:configstatus.v1"
    elif running_config_source == "compliance_rest":
        data_source = "service_api:compliancecheck.getconfig"
    elif running_config_source == "connector":
        data_source = "connector:sysdb_smash_analytics"
    else:
        data_source = "+".join(dict.fromkeys(attempted_sources)) or "unknown"

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
