"""Device configuration via configstatus Resource API + optional URI fetch."""

from __future__ import annotations

import logging
from typing import Any

import grpc
from arista.configstatus.v1 import models as cm
from arista.configstatus.v1 import services as cs
from google.protobuf import wrappers_pb2 as wrappers

from cvp_mcp.grpc.config_connector import connector_fetch_running_config_text
from cvp_mcp.grpc.envelope import tool_envelope
from cvp_mcp.grpc.inventory import grpc_one_inventory_serial
from cvp_mcp.grpc.uri_fetch import fetch_uri_with_bearer
from cvp_mcp.grpc.utils import RPC_TIMEOUT, serialize_arista_protobuf

_MAX_RUNNING_CONFIG_CHARS = 1_500_000


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
    try:
        inv = grpc_one_inventory_serial(channel, device_id)
        if isinstance(inv, dict):
            hostname = str(inv.get("hostname") or "")
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

        if (
            include_running_config
            and (not running_config_text or not running_config_text.strip())
            and datadict.get("cvtoken")
        ):
            fb_text, fb_tried, fb_warn = connector_fetch_running_config_text(
                datadict, device_id
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
