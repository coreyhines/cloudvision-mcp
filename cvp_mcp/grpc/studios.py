"""Phase 1 Studios / workspace / designed-config Resource API + compliance reads."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from urllib.parse import quote, urlencode

from cvp_mcp.grpc.config import (
    _cvp_https_base,
    _inventory_lookup_device,
    _run_async_in_sync_context,
)
from cvp_mcp.grpc.config_async_flow import (
    _extract_config_from_response,
    extract_designed_sources,
    get_config_payload,
    now_ns,
    studio_keys_from_sources,
)
from cvp_mcp.grpc.envelope import tool_envelope
from cvp_mcp.grpc.uri_fetch import (
    get_json_with_bearer,
    get_ndjson_all_values_with_bearer,
)

_MAINLINE_WORKSPACE_ID = ""
# Live Studio/all on this CVaaS tenant is ~75 MiB (2026-08-21); 32 MiB truncated
# before mainline (workspaceId="") rows appeared. Keep headroom under 128 MiB.
_NDJSON_MAX_BYTES = 96_000_000
_TEMPLATE_BODY_WARN_BYTES = 100_000


def _token_base(datadict: dict[str, Any]) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    token = (datadict.get("cvtoken") or "").strip()
    base = _cvp_https_base(str(datadict.get("cvp") or ""))
    if not token:
        warnings.append("missing_token")
    if not base:
        warnings.append("missing_cvp")
    return token, base, warnings


def _as_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        inner = value.get("value")
        if isinstance(inner, str):
            return inner
    return ""


def _unwrap_resource_message(obj: Any) -> dict[str, Any] | None:
    """Normalize keyed Resource API JSON to a message with ``value``."""
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("value"), dict):
        return obj
    result = obj.get("result")
    if isinstance(result, dict) and isinstance(result.get("value"), dict):
        return result
    return None


def _studio_key_fields(value: dict[str, Any]) -> tuple[str, str]:
    key = value.get("key")
    if isinstance(key, dict):
        return (
            _as_str(key.get("studioId") or key.get("studio_id")),
            _as_str(key.get("workspaceId") or key.get("workspace_id")),
        )
    return "", ""


def _template_type(value: dict[str, Any]) -> str | None:
    tmpl = value.get("template")
    if isinstance(tmpl, dict):
        t = tmpl.get("type") or tmpl.get("templateType") or tmpl.get("template_type")
        return _as_str(t) or None
    if isinstance(tmpl, str) and tmpl.strip():
        return "string"
    return None


def _mako_source(template: Any) -> str:
    if isinstance(template, str):
        return template
    if isinstance(template, dict):
        for k in ("body", "source", "value", "template", "mako"):
            v = template.get(k)
            if isinstance(v, str):
                return v
            if isinstance(v, dict):
                inner = v.get("value")
                if isinstance(inner, str):
                    return inner
    return ""


def _input_schema_field_names(schema: Any) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            name = obj.get("name") or obj.get("id")
            if isinstance(name, str) and name and name not in seen:
                seen.add(name)
                names.append(name)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(schema)
    return names


def _summarize_studio(value: dict[str, Any]) -> dict[str, Any]:
    studio_id, workspace_id = _studio_key_fields(value)
    return {
        "studio_id": studio_id,
        "workspace_id": workspace_id,
        "display_name": _as_str(value.get("displayName") or value.get("display_name")),
        "description": _as_str(value.get("description")),
        "created_by": _as_str(value.get("createdBy") or value.get("created_by")),
        "last_modified_at": _as_str(
            value.get("lastModifiedAt") or value.get("last_modified_at")
        ),
        "template_type": _template_type(value),
        "immutable": value.get("immutable"),
        "from_package": value.get("fromPackage") or value.get("from_package"),
        "in_use": value.get("inUse") or value.get("in_use"),
    }


def get_cvp_studios(datadict: dict[str, Any]) -> dict[str, Any]:
    """List studios from Studio/all (no template bodies)."""
    token, base, warnings = _token_base(datadict)
    if "missing_token" in warnings or "missing_cvp" in warnings:
        return tool_envelope(
            data_source="resource_api:studio.v1",
            coverage="none",
            items=[],
            warnings=warnings,
        )
    uri = f"{base}/api/resources/studio/v1/Studio/all"
    values, err, nd_warns = get_ndjson_all_values_with_bearer(
        uri,
        token,
        cafile=datadict.get("cert"),
        cvp_endpoint=str(datadict.get("cvp") or ""),
        max_bytes=_NDJSON_MAX_BYTES,
    )
    warnings.extend(nd_warns)
    if err:
        warnings.append(err)
        return tool_envelope(
            data_source="resource_api:studio.v1",
            coverage="none",
            items=[],
            warnings=warnings,
        )
    items = [_summarize_studio(v) for v in (values or [])]
    return tool_envelope(
        data_source="resource_api:studio.v1",
        coverage="full" if items else "none",
        items=items,
        warnings=warnings,
    )


def get_cvp_studio(
    datadict: dict[str, Any],
    studio_id: str,
    workspace_id: str | None = None,
    *,
    body: bool = False,
) -> dict[str, Any]:
    """Keyed Studio GET (mainline workspace_id defaults to \"\")."""
    warnings: list[str] = []
    sid = (studio_id or "").strip()
    wid = _MAINLINE_WORKSPACE_ID if workspace_id is None else str(workspace_id)
    if not sid:
        return tool_envelope(
            data_source="resource_api:studio.v1",
            coverage="none",
            obj={},
            warnings=["missing_studio_id"],
        )
    token, base, cred_warns = _token_base(datadict)
    warnings.extend(cred_warns)
    if cred_warns:
        return tool_envelope(
            data_source="resource_api:studio.v1",
            coverage="none",
            obj={},
            warnings=warnings,
        )
    # Empty workspaceId must remain in the query string as key.workspaceId=
    q = urlencode({"key.studioId": sid, "key.workspaceId": wid})
    uri = f"{base}/api/resources/studio/v1/Studio?{q}"
    obj, err = get_json_with_bearer(
        uri,
        token,
        cafile=datadict.get("cert"),
        cvp_endpoint=str(datadict.get("cvp") or ""),
        max_bytes=_NDJSON_MAX_BYTES,
    )
    if err:
        warnings.append(err)
        return tool_envelope(
            data_source="resource_api:studio.v1",
            coverage="none",
            obj={},
            warnings=warnings,
        )
    msg = _unwrap_resource_message(obj)
    value = msg.get("value") if msg else None
    if not isinstance(value, dict):
        warnings.append("missing_value")
        return tool_envelope(
            data_source="resource_api:studio.v1",
            coverage="none",
            obj={},
            warnings=warnings,
        )
    summary = _summarize_studio(value)
    schema = value.get("inputSchema") or value.get("input_schema")
    summary["input_schema_fields"] = _input_schema_field_names(schema)
    mako = _mako_source(value.get("template"))
    raw_bytes = mako.encode("utf-8")
    summary["template_bytes"] = len(raw_bytes)
    summary["template_sha256"] = (
        hashlib.sha256(raw_bytes).hexdigest() if raw_bytes else None
    )
    if body:
        summary["template"] = value.get("template")
        if len(raw_bytes) > _TEMPLATE_BODY_WARN_BYTES:
            warnings.append(f"template_body_large:{len(raw_bytes)}")
    return tool_envelope(
        data_source="resource_api:studio.v1",
        coverage="full",
        obj=summary,
        warnings=warnings,
    )


def _parse_inputs_field(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def get_cvp_studio_inputs(
    datadict: dict[str, Any],
    studio_id: str,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Current studio inputs via Inputs/all client filter."""
    warnings: list[str] = []
    sid = (studio_id or "").strip()
    wid = _MAINLINE_WORKSPACE_ID if workspace_id is None else str(workspace_id)
    if not sid:
        return tool_envelope(
            data_source="resource_api:studio.v1.inputs",
            coverage="none",
            items=[],
            warnings=["missing_studio_id"],
        )
    token, base, cred_warns = _token_base(datadict)
    warnings.extend(cred_warns)
    if cred_warns:
        return tool_envelope(
            data_source="resource_api:studio.v1.inputs",
            coverage="none",
            items=[],
            warnings=warnings,
        )
    uri = f"{base}/api/resources/studio/v1/Inputs/all"
    values, err, nd_warns = get_ndjson_all_values_with_bearer(
        uri,
        token,
        cafile=datadict.get("cert"),
        cvp_endpoint=str(datadict.get("cvp") or ""),
        max_bytes=_NDJSON_MAX_BYTES,
    )
    warnings.extend(nd_warns)
    if err:
        warnings.append(err)
        return tool_envelope(
            data_source="resource_api:studio.v1.inputs",
            coverage="none",
            items=[],
            warnings=warnings,
        )
    items: list[dict[str, Any]] = []
    for value in values or []:
        key = value.get("key") if isinstance(value.get("key"), dict) else {}
        row_sid = _as_str(key.get("studioId") or key.get("studio_id"))
        row_wid = _as_str(key.get("workspaceId") or key.get("workspace_id"))
        if row_sid != sid or row_wid != wid:
            continue
        path = key.get("path") if isinstance(key.get("path"), dict) else {}
        path_values = path.get("values")
        if path_values is None:
            path_values = []
        elif not isinstance(path_values, list):
            path_values = [path_values]
        items.append(
            {
                "studio_id": row_sid,
                "workspace_id": row_wid,
                "path_values": path_values,
                "inputs": _parse_inputs_field(value.get("inputs")),
            }
        )
    return tool_envelope(
        data_source="resource_api:studio.v1.inputs",
        coverage="full" if items else "none",
        items=items,
        warnings=warnings,
    )


def _walk_string_hits(
    obj: Any,
    pattern: str,
    *,
    path: str = "$",
    under_template: bool = False,
    under_input_schema: bool = False,
    include_input_schema: bool,
    hits: list[dict[str, Any]],
    max_hits: int,
) -> None:
    if len(hits) >= max_hits:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if len(hits) >= max_hits:
                return
            child_path = f"{path}.{k}"
            next_under_template = under_template or k in (
                "template",
                "body",
                "source",
                "mako",
            )
            next_under_schema = under_input_schema or k in (
                "inputSchema",
                "input_schema",
            )
            if next_under_schema and not include_input_schema:
                continue
            if isinstance(v, str):
                if pattern in v:
                    snippet = v if len(v) <= 200 else v[:200] + "…"
                    hits.append(
                        {
                            "json_path": child_path,
                            "snippet": snippet,
                            "in_template": bool(
                                next_under_template and not next_under_schema
                            ),
                        }
                    )
            else:
                _walk_string_hits(
                    v,
                    pattern,
                    path=child_path,
                    under_template=next_under_template,
                    under_input_schema=next_under_schema,
                    include_input_schema=include_input_schema,
                    hits=hits,
                    max_hits=max_hits,
                )
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if len(hits) >= max_hits:
                return
            _walk_string_hits(
                item,
                pattern,
                path=f"{path}[{i}]",
                under_template=under_template,
                under_input_schema=under_input_schema,
                include_input_schema=include_input_schema,
                hits=hits,
                max_hits=max_hits,
            )


def search_cvp_studio_templates(
    datadict: dict[str, Any],
    pattern: str,
    *,
    include_input_schema: bool = True,
    max_hits: int = 100,
) -> dict[str, Any]:
    """Substring search over Studio/all parsed values (not StudioConfig/all)."""
    warnings: list[str] = []
    pat = pattern or ""
    if not pat:
        return tool_envelope(
            data_source="resource_api:studio.v1",
            coverage="none",
            items=[],
            warnings=["missing_pattern"],
        )
    if max_hits < 1:
        max_hits = 1
    token, base, cred_warns = _token_base(datadict)
    warnings.extend(cred_warns)
    if cred_warns:
        return tool_envelope(
            data_source="resource_api:studio.v1",
            coverage="none",
            items=[],
            warnings=warnings,
        )
    uri = f"{base}/api/resources/studio/v1/Studio/all"
    values, err, nd_warns = get_ndjson_all_values_with_bearer(
        uri,
        token,
        cafile=datadict.get("cert"),
        cvp_endpoint=str(datadict.get("cvp") or ""),
        max_bytes=_NDJSON_MAX_BYTES,
    )
    warnings.extend(nd_warns)
    if err:
        warnings.append(err)
        return tool_envelope(
            data_source="resource_api:studio.v1",
            coverage="none",
            items=[],
            warnings=warnings,
        )
    items: list[dict[str, Any]] = []
    for value in values or []:
        if len(items) >= max_hits:
            warnings.append("max_hits_reached")
            break
        local: list[dict[str, Any]] = []
        _walk_string_hits(
            value,
            pat,
            include_input_schema=include_input_schema,
            hits=local,
            max_hits=max_hits - len(items),
        )
        if not local:
            continue
        studio_id, workspace_id = _studio_key_fields(value)
        display = _as_str(value.get("displayName") or value.get("display_name"))
        for hit in local:
            items.append(
                {
                    "studio_id": studio_id,
                    "workspace_id": workspace_id,
                    "display_name": display,
                    **hit,
                }
            )
            if len(items) >= max_hits:
                break
    return tool_envelope(
        data_source="resource_api:studio.v1",
        coverage="full" if items else "none",
        items=items,
        warnings=warnings,
    )


def _summarize_workspace(value: dict[str, Any]) -> dict[str, Any]:
    key = value.get("key") if isinstance(value.get("key"), dict) else {}
    wid = _as_str(key.get("workspaceId") or key.get("workspace_id"))
    cc = value.get("ccIds") or value.get("cc_ids") or {}
    responses = value.get("responses")
    response_ids: list[str] = []
    if isinstance(responses, dict):
        vals = responses.get("values")
        if isinstance(vals, dict):
            response_ids = list(vals.keys())
    return {
        "workspace_id": wid,
        "display_name": _as_str(value.get("displayName") or value.get("display_name")),
        "state": _as_str(value.get("state")),
        "cc_ids": cc,
        "last_build_id": _as_str(
            value.get("lastBuildId") or value.get("last_build_id")
        ),
        "response_ids": response_ids,
        "last_modified_at": _as_str(
            value.get("lastModifiedAt") or value.get("last_modified_at")
        ),
    }


def get_cvp_workspaces(datadict: dict[str, Any]) -> dict[str, Any]:
    token, base, warnings = _token_base(datadict)
    if warnings:
        return tool_envelope(
            data_source="resource_api:workspace.v1",
            coverage="none",
            items=[],
            warnings=warnings,
        )
    uri = f"{base}/api/resources/workspace/v1/Workspace/all"
    values, err, nd_warns = get_ndjson_all_values_with_bearer(
        uri,
        token,
        cafile=datadict.get("cert"),
        cvp_endpoint=str(datadict.get("cvp") or ""),
        max_bytes=_NDJSON_MAX_BYTES,
    )
    warnings.extend(nd_warns)
    if err:
        warnings.append(err)
        return tool_envelope(
            data_source="resource_api:workspace.v1",
            coverage="none",
            items=[],
            warnings=warnings,
        )
    items = [_summarize_workspace(v) for v in (values or [])]
    return tool_envelope(
        data_source="resource_api:workspace.v1",
        coverage="full" if items else "none",
        items=items,
        warnings=warnings,
    )


def get_cvp_workspace(datadict: dict[str, Any], workspace_id: str) -> dict[str, Any]:
    warnings: list[str] = []
    wid = (workspace_id or "").strip()
    if not wid:
        return tool_envelope(
            data_source="resource_api:workspace.v1",
            coverage="none",
            obj={},
            warnings=["missing_workspace_id"],
        )
    token, base, cred_warns = _token_base(datadict)
    warnings.extend(cred_warns)
    if cred_warns:
        return tool_envelope(
            data_source="resource_api:workspace.v1",
            coverage="none",
            obj={},
            warnings=warnings,
        )
    uri = (
        f"{base}/api/resources/workspace/v1/Workspace?"
        f"key.workspaceId={quote(wid, safe='')}"
    )
    obj, err = get_json_with_bearer(
        uri,
        token,
        cafile=datadict.get("cert"),
        cvp_endpoint=str(datadict.get("cvp") or ""),
    )
    if err:
        warnings.append(err)
        return tool_envelope(
            data_source="resource_api:workspace.v1",
            coverage="none",
            obj={},
            warnings=warnings,
        )
    msg = _unwrap_resource_message(obj)
    value = msg.get("value") if msg else None
    if not isinstance(value, dict):
        warnings.append("missing_value")
        return tool_envelope(
            data_source="resource_api:workspace.v1",
            coverage="none",
            obj={},
            warnings=warnings,
        )
    summary = _summarize_workspace(value)
    summary["responses"] = value.get("responses")
    summary["needs_build"] = value.get("needsBuild") or value.get("needs_build")
    summary["needs_rebase"] = value.get("needsRebase") or value.get("needs_rebase")
    return tool_envelope(
        data_source="resource_api:workspace.v1",
        coverage="full",
        obj=summary,
        warnings=warnings,
    )


def get_cvp_workspace_build(
    datadict: dict[str, Any], workspace_id: str, build_id: str
) -> dict[str, Any]:
    warnings: list[str] = []
    wid = (workspace_id or "").strip()
    bid = (build_id or "").strip()
    if not wid or not bid:
        return tool_envelope(
            data_source="resource_api:workspace.v1",
            coverage="none",
            obj={},
            warnings=["missing_workspace_id_or_build_id"],
        )
    token, base, cred_warns = _token_base(datadict)
    warnings.extend(cred_warns)
    if cred_warns:
        return tool_envelope(
            data_source="resource_api:workspace.v1",
            coverage="none",
            obj={},
            warnings=warnings,
        )
    q = urlencode({"key.workspaceId": wid, "key.buildId": bid})
    uri = f"{base}/api/resources/workspace/v1/WorkspaceBuild?{q}"
    obj, err = get_json_with_bearer(
        uri,
        token,
        cafile=datadict.get("cert"),
        cvp_endpoint=str(datadict.get("cvp") or ""),
    )
    if err:
        warnings.append(err)
        return tool_envelope(
            data_source="resource_api:workspace.v1",
            coverage="none",
            obj={},
            warnings=warnings,
        )
    msg = _unwrap_resource_message(obj)
    value = msg.get("value") if msg else None
    if not isinstance(value, dict):
        warnings.append("missing_value")
        return tool_envelope(
            data_source="resource_api:workspace.v1",
            coverage="none",
            obj={},
            warnings=warnings,
        )
    key = value.get("key") if isinstance(value.get("key"), dict) else {}
    out = {
        "workspace_id": _as_str(key.get("workspaceId") or key.get("workspace_id")),
        "build_id": _as_str(key.get("buildId") or key.get("build_id")),
        "state": _as_str(value.get("state")),
        "error": _as_str(value.get("error")),
        "built_by": _as_str(value.get("builtBy") or value.get("built_by")),
        "time": _as_str(msg.get("time") if msg else None),
    }
    return tool_envelope(
        data_source="resource_api:workspace.v1",
        coverage="full",
        obj=out,
        warnings=warnings,
    )


def get_cvp_designed_config(datadict: dict[str, Any], device_id: str) -> dict[str, Any]:
    """Designed config provenance via compliance GetConfig DESIGNED_CONFIG."""
    warnings: list[str] = []
    target = (device_id or "").strip()
    if not target:
        return tool_envelope(
            device_id=None,
            data_source="service_api:compliancecheck.getconfig",
            coverage="none",
            obj={},
            warnings=["missing_device_id"],
        )
    token, base, cred_warns = _token_base(datadict)
    warnings.extend(cred_warns)
    if cred_warns:
        return tool_envelope(
            device_id=target,
            data_source="service_api:compliancecheck.getconfig",
            coverage="none",
            obj={},
            warnings=warnings,
        )

    serial = target
    facts, inv_err = _inventory_lookup_device(datadict, target)
    if facts.get("serial_number"):
        serial = facts["serial_number"]
    elif inv_err and inv_err != "not_found":
        warnings.append(f"inventory_rest_lookup:{inv_err}")
    elif inv_err == "not_found":
        warnings.append("inventory_not_found_using_input_as_device_id")

    url = f"{base}/api/v3/services/compliancecheck.Compliance/GetConfig"
    cafile = datadict.get("cert")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    timestamp = now_ns()

    async def _run() -> tuple[Any | None, str | None]:
        import ssl

        import aiohttp

        timeout = aiohttp.ClientTimeout(total=180.0)
        ssl_ctx = None
        if cafile:
            ssl_ctx = ssl.create_default_context(cafile=cafile)
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(
            headers=headers, timeout=timeout, connector=connector
        ) as session:
            return await get_config_payload(
                session,
                url,
                serial,
                timestamp,
                config_type="DESIGNED_CONFIG",
            )

    try:
        data, err = _run_async_in_sync_context(_run())
    except Exception as e:  # noqa: BLE001
        logging.debug("designed config fetch failed: %s", e)
        warnings.append(str(e))
        return tool_envelope(
            device_id=serial,
            data_source="service_api:compliancecheck.getconfig",
            coverage="none",
            obj={},
            warnings=warnings,
        )
    if err:
        warnings.append(err)
        return tool_envelope(
            device_id=serial,
            data_source="service_api:compliancecheck.getconfig",
            coverage="none",
            obj={},
            warnings=warnings,
        )
    sources = extract_designed_sources(data)
    studio_keys = studio_keys_from_sources(sources)
    designed_text = _extract_config_from_response(data)
    if not sources:
        warnings.append("missing_sources")
    if not designed_text:
        warnings.append("missing_designed_config_text")
    coverage = "full"
    if not sources and not designed_text:
        coverage = "none"
    elif not sources or not designed_text:
        coverage = "partial"
    return tool_envelope(
        device_id=serial,
        data_source="service_api:compliancecheck.getconfig",
        coverage=coverage,
        obj={
            "sources": sources,
            "studio_keys": studio_keys,
            "designed_config_text": designed_text,
        },
        warnings=warnings,
    )
