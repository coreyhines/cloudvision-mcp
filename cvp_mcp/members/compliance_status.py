"""Unavailable compliance status member stubs."""

from cvp_mcp.grpc.envelope import tool_envelope


def config_status(device_id: str) -> dict:
    """Return the tenant access limitation for config compliance status."""
    return tool_envelope(
        data_source="resource_api:configstatus.v1.summary",
        coverage="none",
        items=[],
        warnings=["configstatus_forbidden"],
        obj={
            "device_id_input": device_id,
            "hint": "Resource API Summary returned 403 on this tenant",
        },
    )


def image_status(device_id: str) -> dict:
    """Return the tenant access limitation for image compliance status."""
    return tool_envelope(
        data_source="resource_api:imagestatus.v1.summary",
        coverage="none",
        items=[],
        warnings=["imagestatus_forbidden"],
        obj={
            "device_id_input": device_id,
            "hint": "Resource API Summary returned 403 on this tenant",
        },
    )
