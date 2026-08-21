"""Unit tests for Phase 1 studios / workspace / designed-config helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from cvp_mcp.grpc.config_async_flow import (
    extract_designed_sources,
    studio_keys_from_sources,
)
from cvp_mcp.grpc.studios import (
    _summarize_studio,
    _walk_string_hits,
    get_cvp_designed_config,
    get_cvp_studio,
    get_cvp_studio_inputs,
    get_cvp_studios,
    get_cvp_workspace,
    get_cvp_workspace_build,
    get_cvp_workspaces,
    search_cvp_studio_templates,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_DATADICT = {"cvp": "cv.example.com:443", "cvtoken": "tok", "cert": None}


def _load(name: str) -> object:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def test_extract_designed_sources_from_fixture() -> None:
    data = _load("designed_config_response_720xp24.json")
    sources = extract_designed_sources(data)
    assert sources
    assert sources[0]["source_type"] == "CONFIG_TYPE_STUDIO"
    keys = studio_keys_from_sources(sources)
    assert "studio-campus-access-interfaces" in keys
    assert keys == list(dict.fromkeys(keys))


def test_summarize_studio_from_fixture() -> None:
    raw = _load("studio_mainline_event_handler.json")
    assert isinstance(raw, dict)
    value = raw["value"]
    summary = _summarize_studio(value)
    assert summary["studio_id"] == "studio-eos-event-handler-pkg"
    assert summary["workspace_id"] == ""
    assert summary["display_name"] == "EOS Event Handler"
    assert summary["template_type"] == "TEMPLATE_TYPE_MAKO"


def test_walk_string_hits_logging_in_schema() -> None:
    raw = _load("studio_mainline_event_handler.json")
    assert isinstance(raw, dict)
    hits: list[dict] = []
    _walk_string_hits(
        raw["value"],
        "logging",
        include_input_schema=True,
        hits=hits,
        max_hits=20,
    )
    assert hits
    assert any(
        "inputSchema" in h["json_path"] or "input_schema" in h["json_path"]
        for h in hits
    )


def test_get_cvp_studios_uses_ndjson_helper() -> None:
    raw = _load("studio_mainline_event_handler.json")
    assert isinstance(raw, dict)
    with patch(
        "cvp_mcp.grpc.studios.get_ndjson_all_values_with_bearer",
        return_value=([raw["value"]], None, []),
    ):
        out = get_cvp_studios(_DATADICT)
    assert out["coverage"] == "full"
    assert out["items"][0]["studio_id"] == "studio-eos-event-handler-pkg"
    assert "template" not in out["items"][0]


def test_get_cvp_studio_mainline_empty_workspace() -> None:
    raw = _load("studio_mainline_event_handler.json")
    with patch(
        "cvp_mcp.grpc.studios.get_json_with_bearer",
        return_value=(raw, None),
    ) as mocked:
        out = get_cvp_studio(_DATADICT, "studio-eos-event-handler-pkg")
    uri = mocked.call_args[0][0]
    assert "key.workspaceId=" in uri
    assert "key.studioId=studio-eos-event-handler-pkg" in uri
    assert out["object"]["display_name"] == "EOS Event Handler"
    assert out["object"]["template_sha256"]
    assert "template" not in out["object"]
    assert out["object"]["input_schema_fields"]


def test_get_cvp_studio_body_true_includes_template() -> None:
    raw = _load("studio_mainline_event_handler.json")
    with patch(
        "cvp_mcp.grpc.studios.get_json_with_bearer",
        return_value=(raw, None),
    ):
        out = get_cvp_studio(_DATADICT, "studio-eos-event-handler-pkg", body=True)
    assert "template" in out["object"]


def test_get_cvp_studio_inputs_filters_all() -> None:
    raw = _load("inputs_mainline_topology_sample.json")
    assert isinstance(raw, dict)
    value = dict(raw["result"]["value"])
    value["inputs"] = json.dumps({"devices": [{"hostname": "720xp-48"}]})
    other = {
        "key": {
            "studioId": "OTHER",
            "workspaceId": "",
            "path": {},
        },
        "inputs": "{}",
    }
    with patch(
        "cvp_mcp.grpc.studios.get_ndjson_all_values_with_bearer",
        return_value=([value, other], None, []),
    ):
        out = get_cvp_studio_inputs(_DATADICT, "TOPOLOGY")
    assert len(out["items"]) == 1
    assert out["items"][0]["studio_id"] == "TOPOLOGY"
    assert out["items"][0]["workspace_id"] == ""
    assert out["items"][0]["path_values"] == []
    assert isinstance(out["items"][0]["inputs"], dict)
    assert out["items"][0]["inputs"]["devices"][0]["hostname"] == "720xp-48"


def test_search_cvp_studio_templates() -> None:
    raw = _load("studio_mainline_event_handler.json")
    assert isinstance(raw, dict)
    with patch(
        "cvp_mcp.grpc.studios.get_ndjson_all_values_with_bearer",
        return_value=([raw["value"]], None, []),
    ):
        out = search_cvp_studio_templates(_DATADICT, "logging", max_hits=5)
    assert out["items"]
    assert out["items"][0]["studio_id"] == "studio-eos-event-handler-pkg"


def test_get_cvp_workspaces() -> None:
    raw = _load("workspace_response_sample.json")
    assert isinstance(raw, dict)
    value = raw["result"]["value"]
    with patch(
        "cvp_mcp.grpc.studios.get_ndjson_all_values_with_bearer",
        return_value=([value], None, []),
    ):
        out = get_cvp_workspaces(_DATADICT)
    assert out["items"][0]["workspace_id"] == "builtin-studios-v0.112-topology"
    assert out["items"][0]["state"] == "WORKSPACE_STATE_SUBMITTED"
    assert (
        "build-3e099483-7eab-4d27-b442-2a5c1fc6d2d1" in out["items"][0]["response_ids"]
    )


def test_get_cvp_workspace_unwraps_result() -> None:
    raw = _load("workspace_response_sample.json")
    with patch(
        "cvp_mcp.grpc.studios.get_json_with_bearer",
        return_value=(raw, None),
    ):
        out = get_cvp_workspace(_DATADICT, "builtin-studios-v0.112-topology")
    assert out["coverage"] == "full"
    assert out["object"]["state"] == "WORKSPACE_STATE_SUBMITTED"
    assert out["object"]["responses"]["values"]


def test_get_cvp_workspace_build() -> None:
    raw = _load("workspace_build_response_sample.json")
    with patch(
        "cvp_mcp.grpc.studios.get_json_with_bearer",
        return_value=(raw, None),
    ):
        out = get_cvp_workspace_build(
            _DATADICT,
            "builtin-studios-v0.112-topology",
            "build-3e099483-7eab-4d27-b442-2a5c1fc6d2d1",
        )
    assert out["object"]["state"] == "BUILD_STATE_SUCCESS"
    assert out["object"]["build_id"].startswith("build-")


def test_get_cvp_designed_config_from_payload() -> None:
    data = _load("designed_config_response_720xp24.json")

    def _fake_run(coro):
        coro.close()
        return data, None

    with (
        patch(
            "cvp_mcp.grpc.studios._inventory_lookup_device",
            return_value=({"serial_number": "HBG254804R6"}, None),
        ),
        patch(
            "cvp_mcp.grpc.studios._run_async_in_sync_context",
            side_effect=_fake_run,
        ),
    ):
        out = get_cvp_designed_config(_DATADICT, "720xp-24")
    assert out["device_id"] == "HBG254804R6"
    assert out["object"]["studio_keys"]
    assert out["coverage"] in ("full", "partial")
    assert out["data_source"] == "service_api:compliancecheck.getconfig"


def test_missing_credentials() -> None:
    out = get_cvp_studios({"cvp": "", "cvtoken": ""})
    assert out["coverage"] == "none"
    assert "missing_token" in out["warnings"] or "missing_cvp" in out["warnings"]
