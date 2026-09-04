"""Clover flow-data member callables."""

from __future__ import annotations

import logging

from cvp_mcp.env import env_datadict_from_os
from cvp_mcp.grouped_tool import MemberSpec
from cvp_mcp.grpc.device_resolve import (
    resolve_device_to_serial,
    summarize_inventory_candidates,
)
from cvp_mcp.grpc.flow import conn_get_flow_data


def flow_get(
    device_id: str | None = None,
    flow_index: int | None = None,
) -> dict:
    """Return Clover flow records, optionally filtered to one device."""
    datadict = env_datadict_from_os()
    logging.info("CVP Get Flow Data: device=%s flow_index=%s", device_id, flow_index)
    filter_serial = device_id
    resolution: dict[str, str] = {}
    if filter_serial:
        serial, _info, warnings, candidates = resolve_device_to_serial(
            datadict, filter_serial
        )
        if not serial:
            result: dict = {
                "error": (
                    "device_ambiguous"
                    if "device_ambiguous" in warnings
                    else "device_not_found"
                ),
                "device_id_input": filter_serial.strip(),
                "warnings": warnings,
                "flows": [],
            }
            rows = summarize_inventory_candidates(candidates)
            if rows:
                result["candidates"] = rows
            return result
        if serial != filter_serial.strip():
            resolution = {
                "device_id_input": filter_serial.strip(),
                "device_id_resolved": serial,
            }
        filter_serial = serial
    flows = conn_get_flow_data(datadict, filter_serial, flow_index)
    result = {"flows": flows}
    result.update(resolution)
    return result


def members() -> dict[str, MemberSpec]:
    """Return flow member specifications keyed by action."""
    return {
        "get": MemberSpec(
            action="get",
            description="Get Clover flow records with optional device filtering.",
            required=[],
            properties={
                "device_id": {
                    "type": "string",
                    "description": "Optional device identifier.",
                },
                "flow_index": {
                    "type": "integer",
                    "description": "Optional Clover flow path index.",
                },
            },
            call=flow_get,
        )
    }
