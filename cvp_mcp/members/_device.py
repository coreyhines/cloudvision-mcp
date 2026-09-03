"""Shared device-resolution helpers for grouped-tool members."""

from __future__ import annotations

from cvp_mcp.grpc.device_resolve import summarize_inventory_candidates
from cvp_mcp.grpc.envelope import tool_envelope


def device_not_found_envelope(
    device_id: str,
    data_source: str,
    warnings: list[str] | None = None,
    candidates: list | None = None,
) -> dict:
    """Return a consistent envelope for failed or ambiguous device resolution."""
    device_input = (device_id or "").strip()
    result_warnings = list(warnings or [])
    ambiguous = "device_ambiguous" in result_warnings
    primary = "device_ambiguous" if ambiguous else "device_not_found"
    if primary not in result_warnings:
        result_warnings.insert(0, primary)
    candidate_rows = summarize_inventory_candidates(candidates)
    hint = (
        "Multiple inventory devices match this shorthand. Pick one serial_number "
        "from candidates and re-call with device_id=<serial_number>."
        if ambiguous and candidate_rows
        else (
            "No device matched. Run inventory.search or inventory.list first, "
            "then pass device_id as the "
            "CloudVision serial_number "
            "(not a model name like 720xp)."
        )
    )
    if candidate_rows and not ambiguous:
        hint = (
            "No exact device match. Partial inventory matches are listed in "
            "candidates — re-call with device_id=<serial_number>."
        )
    obj: dict = {
        "device_id_input": device_input,
        "hint": hint,
        "next_step": (
            'inventory(action="search", query=...) -> '
            'topology(action="lldp", device_id=<serial_number>)'
        ),
    }
    if candidate_rows:
        obj["candidates"] = candidate_rows
    return tool_envelope(
        device_id=device_input or None,
        data_source=data_source,
        coverage="none",
        items=[],
        warnings=result_warnings,
        obj=obj,
    )


def attach_device_resolution(
    result: dict,
    device_id_input: str,
    serial: str,
    resolution_warnings: list[str],
) -> dict:
    """Annotate a device result with its canonical serial and resolution details."""
    if not isinstance(result, dict):
        return result
    device_input = (device_id_input or "").strip()
    result["device_id"] = serial
    if device_input and device_input != serial:
        obj = dict(result.get("object") or {})
        obj["device_id_input"] = device_input
        obj["device_id_resolved"] = serial
        result["object"] = obj
    if resolution_warnings:
        result["warnings"] = list(result.get("warnings") or []) + resolution_warnings
    return result
