"""CloudVision Events (arista.event.v1) — structured list and text search."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

import grpc
from arista.event.v1 import models as em
from arista.event.v1 import services as es
from arista.time import models as tm
from google.protobuf.timestamp_pb2 import Timestamp

from cvp_mcp.grpc.envelope import tool_envelope
from cvp_mcp.grpc.utils import RPC_TIMEOUT, serialize_arista_protobuf


def _cap_limit(limit: int | None, default: int, hard_max: int) -> int:
    if limit is None or limit <= 0:
        n = default
    else:
        n = limit
    return min(n, hard_max)


def _parse_severity(name: str | None) -> int | None:
    if not name or not str(name).strip():
        return None
    s = str(name).strip().upper()
    if s.startswith("EVENT_SEVERITY_"):
        try:
            return em.EventSeverity.Value(s)
        except ValueError:
            return None
    aliases = {
        "INFO": em.EVENT_SEVERITY_INFO,
        "WARNING": em.EVENT_SEVERITY_WARNING,
        "ERROR": em.EVENT_SEVERITY_ERROR,
        "CRITICAL": em.EVENT_SEVERITY_CRITICAL,
        "DEBUG": em.EVENT_SEVERITY_DEBUG,
    }
    return aliases.get(s)


def _time_bounds(start_time: str | None, end_time: str | None) -> tm.TimeBounds | None:
    if not start_time and not end_time:
        return None
    bounds = tm.TimeBounds()

    def _parse_iso(s: str) -> Timestamp:
        ts = Timestamp()
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        ts.FromDatetime(dt)
        return ts

    try:
        if start_time:
            bounds.start.CopyFrom(_parse_iso(start_time))
        if end_time:
            bounds.end.CopyFrom(_parse_iso(end_time))
    except Exception as e:
        logging.warning("event time bounds parse: %s", e)
        return None
    return bounds


def _str_field(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, dict) and "value" in v:
        return str(v.get("value", ""))
    return str(v)


def _normalize_event_dict(ev: dict[str, Any]) -> dict[str, Any]:
    key = ev.get("key") or {}
    ts = key.get("timestamp") if isinstance(key, dict) else None
    ack = ev.get("ack") or {}
    return {
        "key": _str_field(key.get("key")) if isinstance(key, dict) else "",
        "timestamp": ts,
        "severity": ev.get("severity"),
        "title": _str_field(ev.get("title")),
        "description": _str_field(ev.get("description")),
        "event_type": _str_field(ev.get("event_type")),
        "components": ev.get("components"),
        "ack": ack if isinstance(ack, dict) else {},
        "last_updated_time": ev.get("last_updated_time"),
        "rule_id": _str_field(ev.get("rule_id")),
    }


def _event_blob_for_search(ev: dict[str, Any]) -> str:
    n = _normalize_event_dict(ev)
    parts = [
        n["title"],
        n["description"],
        n["event_type"],
        n["key"] or "",
        str(ev.get("metadata", "")),
        str(ev.get("components", "")),
    ]
    return "\n".join(parts).lower()


def _event_matches_device(ev: dict[str, Any], device_id: str) -> bool:
    if not device_id:
        return True
    return device_id.lower() in _event_blob_for_search(ev)


def _stream_events(
    channel: grpc.Channel,
    *,
    partial_event: em.Event,
    time_bounds: tm.TimeBounds | None,
    max_read: int,
) -> list[dict[str, Any]]:
    stub = es.EventServiceStub(channel)
    req = es.EventStreamRequest()
    if partial_event and partial_event.ListFields():
        req.partial_eq_filter.append(partial_event)
    if time_bounds is not None:
        req.time.CopyFrom(time_bounds)
    out: list[dict[str, Any]] = []
    for resp in stub.GetAll(req, timeout=RPC_TIMEOUT):
        if not resp.HasField("value"):
            continue
        ev = serialize_arista_protobuf(resp.value)
        out.append(ev if isinstance(ev, dict) else {"raw": ev})
        if len(out) >= max_read:
            break
    return out


def grpc_get_cvp_events(
    channel: grpc.Channel,
    *,
    severity: str | None = None,
    event_type: str | None = None,
    device_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int | None = 100,
) -> dict[str, Any]:
    warnings: list[str] = []
    lim = _cap_limit(limit, default=100, hard_max=500)
    dev = (device_id or "").strip()
    ev_partial = em.Event()
    sev = _parse_severity(severity)
    if severity and sev is None:
        warnings.append(f"unrecognized_severity:{severity}")
    if sev is not None:
        ev_partial.severity = sev
    if sev is None and severity:
        # avoid sending empty partial; invalid severity already warned
        pass
    if event_type and str(event_type).strip():
        ev_partial.event_type.value = str(event_type).strip()

    bounds = _time_bounds(start_time, end_time)
    try:
        raw_list = _stream_events(
            channel,
            partial_event=ev_partial,
            time_bounds=bounds,
            max_read=lim * 4 if dev else lim,
        )
    except Exception as e:
        logging.error("get_cvp_events: %s", e)
        return tool_envelope(
            device_id=dev or None,
            data_source="resource_api:event.v1",
            coverage="none",
            items=[],
            warnings=[str(e)],
        )

    filtered = [e for e in raw_list if _event_matches_device(e, dev)]
    filtered = filtered[:lim]
    items = [_normalize_event_dict(e) for e in filtered]
    cov = "full" if items else "partial"
    if dev and len(raw_list) >= lim * 4:
        warnings.append("device_filter_may_need_narrower_time_or_filters")
    return tool_envelope(
        device_id=dev or None,
        data_source="resource_api:event.v1",
        coverage=cov,
        items=items,
        warnings=warnings,
    )


def grpc_search_cvp_events(
    channel: grpc.Channel,
    query: str,
    *,
    severity: str | None = None,
    event_type: str | None = None,
    device_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int | None = 50,
) -> dict[str, Any]:
    warnings: list[str] = []
    lim = _cap_limit(limit, default=50, hard_max=200)
    q = (query or "").strip().lower()
    if not q:
        return tool_envelope(
            device_id=(device_id or "").strip() or None,
            data_source="resource_api:event.v1+client_search",
            coverage="none",
            items=[],
            warnings=["empty_query"],
        )

    ev_partial = em.Event()
    sev = _parse_severity(severity)
    if severity and sev is None:
        warnings.append(f"unrecognized_severity:{severity}")
    if sev is not None:
        ev_partial.severity = sev
    if event_type and str(event_type).strip():
        ev_partial.event_type.value = str(event_type).strip()

    bounds = _time_bounds(start_time, end_time)
    dev = (device_id or "").strip()
    try:
        raw_list = _stream_events(
            channel,
            partial_event=ev_partial,
            time_bounds=bounds,
            max_read=max(lim * 50, 500),
        )
    except Exception as e:
        logging.error("search_cvp_events: %s", e)
        return tool_envelope(
            device_id=dev or None,
            data_source="resource_api:event.v1+client_search",
            coverage="none",
            items=[],
            warnings=[str(e)],
        )

    tokens = [t for t in re.split(r"\s+", q) if t]
    matched: list[dict[str, Any]] = []
    for ev in raw_list:
        if dev and not _event_matches_device(ev, dev):
            continue
        blob = _event_blob_for_search(ev)
        if tokens and not all(t in blob for t in tokens):
            if q not in blob:
                continue
        fields: list[str] = []
        n = _normalize_event_dict(ev)
        for label, val in (
            ("title", n["title"].lower()),
            ("description", n["description"].lower()),
            ("event_type", n["event_type"].lower()),
        ):
            if val and q in val:
                fields.append(label)
        matched.append({**n, "matched_fields": fields})
        if len(matched) >= lim:
            break

    if len(raw_list) >= max(lim * 50, 500):
        warnings.append("scan_limit_reached_increase_time_filters")

    return tool_envelope(
        device_id=dev or None,
        data_source="resource_api:event.v1+client_search",
        coverage="full" if matched else "partial",
        items=matched,
        warnings=warnings,
    )
