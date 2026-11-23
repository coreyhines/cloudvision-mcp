"""Tests for Campus Fabric SVI compare-and-set (sha-guarded upsert)."""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from cvp_mcp.grpc import studio_campus_svi_inputs as campus
from cvp_mcp.grpc.inputs_digest import inputs_sha256
from cvp_mcp.write_access import WRITES_ENV

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "inputs_campus_fabric_svis_minimal.json"
)
FIXTURE = json.loads(FIXTURE_PATH.read_text())
PRE_DOC = FIXTURE["inputs"]
PRE_SHA = inputs_sha256(PRE_DOC)

STUDIO_ID = "studio-avd-campus-fabric"
WORKSPACE = "ws-mcp-campus-svi-test-20261018"
DATADICT = {"cvtoken": "container-token", "cvp": "cvp.example.com", "cert": None}

VLAN9_ENTRY = {
    "id": 9,
    "name": "ipv6-lab",
    "enabled": "Yes",
    "routed": True,
    "vrf": "default",
    "multicast": {},
    "nodes": [],
    "devices": [
        {
            "enabled": None,
            "ipVirtualRouterSubnet": None,
            "tagQuery": {"tags": {"query": "Access-Pod:1"}},
        }
    ],
    "dhcpHelpers": [],
    "eosCli": (
        "interface Vlan9\n"
        "   description ipv6-lab\n"
        "   ipv6 enable\n"
        "   ipv6 address 2601:441:8483:b509::254/64"
    ),
    "ipVirtualRouterSubnet": None,
}


@pytest.fixture(autouse=True)
def _writes_on(monkeypatch):
    monkeypatch.setenv(WRITES_ENV, "1")


def _row(workspace_id: str, document: dict) -> dict:
    return {
        "key": {"studioId": STUDIO_ID, "workspaceId": workspace_id, "path": {}},
        "inputs": json.dumps(document),
    }


def _workspace_value(state="WORKSPACE_STATE_PENDING"):
    return {
        "value": {
            "key": {"workspaceId": WORKSPACE},
            "displayName": "campus svi test",
            "state": state,
        },
        "time": "2026-10-18T20:00:00Z",
    }


def _studio_env(*, immutable=None, from_package=None):
    return {
        "coverage": "full",
        "object": {
            "studio_id": STUDIO_ID,
            "immutable": immutable,
            "from_package": from_package,
        },
        "warnings": [],
    }


@contextmanager
def _mocked(*, workspace=None, studio=None, rows=None, nd_warnings=(), nd_err=None):
    if workspace is None:
        ws_result = (_workspace_value(), None)
    elif workspace == ("missing",):
        ws_result = (None, "http_error:404")
    else:
        ws_result = (workspace, None)

    if studio is None:
        studio_kwargs = {"return_value": _studio_env()}
    else:
        studio_kwargs = {"return_value": studio}

    stream = [_row("", PRE_DOC)] if rows is None else list(rows)
    nd_result = (None if nd_err else stream, nd_err, list(nd_warnings))

    with (
        patch(
            "cvp_mcp.grpc.studios.get_json_with_bearer", return_value=ws_result
        ) as get_ws,
        patch("cvp_mcp.grpc.studio_crud.get_cvp_studio", **studio_kwargs) as get_studio,
        patch(
            "cvp_mcp.grpc.studios_write.get_ndjson_all_values_with_bearer",
            return_value=nd_result,
        ) as get_inputs,
        patch("urllib.request.urlopen") as urlopen,
    ):
        resp = urlopen.return_value.__enter__.return_value
        resp.read.return_value = json.dumps(
            {"value": {}, "time": "2026-10-18T20:05:00Z"}
        ).encode()
        yield {
            "workspace_get": get_ws,
            "studio_get": get_studio,
            "inputs_get": get_inputs,
            "urlopen": urlopen,
        }


def _obj(env):
    return env["object"]


def _code(env):
    return _obj(env)["error"]["code"]


def _call(
    operations,
    *,
    expected=PRE_SHA,
    confirm=False,
    token=None,
    workspace_id=WORKSPACE,
):
    return campus.set_cvp_campus_svi_inputs(
        DATADICT,
        workspace_id,
        expected,
        operations,
        confirm=confirm,
        preview_token_value=token,
    )


def _upsert_ops(entry=None):
    return [{"op": "upsert_svi", "entry": copy.deepcopy(entry or VLAN9_ENTRY)}]


def test_preview_upsert_adds_svi_and_stays_in_scope():
    with _mocked() as mocks:
        env = _call(_upsert_ops())
    assert env["coverage"] == "full"
    fields = _obj(env)
    assert fields["outcome"] == "preview"
    assert fields["entries_added"] == ["svi:9"]
    assert fields["changed_leaves"] >= 1
    assert mocks["urlopen"].call_count == 0
    assert "preview_token" in fields

    after = json.loads(fields["request_body"]["inputs"])
    ids = [
        s["id"]
        for s in after["campusServices"][0]["inputs"]["campusServicesGroup"][
            "campusPodsServices"
        ][0]["inputs"]["services"]["svis"]
    ]
    assert 9 in ids
    assert 2 in ids


def test_confirm_posts_root_inputs():
    with _mocked() as mocks:
        preview = _call(_upsert_ops())
        token = _obj(preview)["preview_token"]
        env = _call(_upsert_ops(), confirm=True, token=token)
    assert _obj(env)["outcome"] == "accepted"
    assert mocks["urlopen"].call_count == 1
    body = json.loads(mocks["urlopen"].call_args[0][0].data.decode())
    assert body["key"]["studioId"] == STUDIO_ID
    assert body["key"]["path"] == {"values": []}
    assert body["key"]["workspaceId"] == WORKSPACE


def test_digest_mismatch_refuses_without_post():
    with _mocked() as mocks:
        env = _call(_upsert_ops(), expected="0" * 64)
    assert _code(env) == "inputs_digest_mismatch"
    assert mocks["urlopen"].call_count == 0


def test_remove_missing_refuses():
    with _mocked() as mocks:
        env = _call([{"op": "remove_svi", "id": 99}])
    assert _code(env) == "campus_svi_entry_not_found"
    assert mocks["urlopen"].call_count == 0


def test_bad_entry_schema_refuses():
    bad = copy.deepcopy(VLAN9_ENTRY)
    bad["enabled"] = "maybe"
    with _mocked() as mocks:
        env = _call(_upsert_ops(bad))
    assert _code(env) == "campus_svi_operation_invalid"
    assert mocks["urlopen"].call_count == 0


def test_disruptive_eos_cli_refuses():
    bad = copy.deepcopy(VLAN9_ENTRY)
    bad["eosCli"] = "interface Vlan9\n   shutdown"
    with _mocked() as mocks:
        env = _call(_upsert_ops(bad))
    assert _code(env) == "disruptive_content_forbidden"
    assert mocks["urlopen"].call_count == 0


def test_replace_existing_svi():
    updated = copy.deepcopy(
        PRE_DOC["campusServices"][0]["inputs"]["campusServicesGroup"][
            "campusPodsServices"
        ][0]["inputs"]["services"]["svis"][0]
    )
    updated["name"] = "wired-lan-renamed"
    with _mocked() as mocks:
        env = _call([{"op": "upsert_svi", "entry": updated}])
    assert _obj(env)["entries_replaced"] == ["svi:2"]
    assert mocks["urlopen"].call_count == 0
