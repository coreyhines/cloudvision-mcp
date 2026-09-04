"""CloudVision event member callables."""

from __future__ import annotations

import grpc

from cvp_mcp.env import env_datadict_from_os
from cvp_mcp.errors import client_error
from cvp_mcp.grouped_tool import MemberSpec
from cvp_mcp.grpc.events import grpc_get_cvp_events, grpc_search_cvp_events
from cvp_mcp.grpc.utils import createConnection


def events_list(
    severity: str | None = None,
    event_type: str | None = None,
    device_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int | None = 100,
) -> dict:
    """List events with optional structured filters."""
    datadict = env_datadict_from_os()
    try:
        conn_creds = createConnection(datadict)
        with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
            return grpc_get_cvp_events(
                channel,
                severity=severity,
                event_type=event_type,
                device_id=device_id,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )
    except Exception as exc:
        return client_error("events_failed", log_exc=exc, context="get_cvp_events")


def events_search(
    query: str,
    severity: str | None = None,
    event_type: str | None = None,
    device_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int | None = 50,
) -> dict:
    """Search event text after applying optional structured filters."""
    datadict = env_datadict_from_os()
    try:
        conn_creds = createConnection(datadict)
        with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
            return grpc_search_cvp_events(
                channel,
                query,
                severity=severity,
                event_type=event_type,
                device_id=device_id,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )
    except Exception as exc:
        return client_error(
            "search_events_failed", log_exc=exc, context="search_cvp_events"
        )


def _event_filter_properties(default_limit: int) -> dict[str, dict]:
    return {
        "severity": {"type": "string", "description": "Event severity filter."},
        "event_type": {"type": "string", "description": "Event type filter."},
        "device_id": {"type": "string", "description": "Device substring filter."},
        "start_time": {"type": "string", "description": "ISO start timestamp."},
        "end_time": {"type": "string", "description": "ISO end timestamp."},
        "limit": {
            "type": "integer",
            "default": default_limit,
            "description": "Maximum events to return.",
        },
    }


def members() -> dict[str, MemberSpec]:
    """Return event member specifications keyed by action."""
    return {
        "list": MemberSpec(
            action="list",
            description="List events with structured filters.",
            required=[],
            properties=_event_filter_properties(100),
            call=events_list,
        ),
        "search": MemberSpec(
            action="search",
            description="Search event title, description, and type.",
            required=["query"],
            properties={
                "query": {"type": "string", "description": "Event search text."},
                **_event_filter_properties(50),
            },
            call=events_search,
            rate_limit_key="events.search",
        ),
    }
