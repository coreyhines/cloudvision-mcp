"""Tests for the Phase 2.3 MSS Service root Inputs CAS (bucket M).

HTTP is mocked at the boundary, the same way as the 2.0 / 2.1 write tests: the
workspace GET through ``studios.get_json_with_bearer``, the studio GET through
``studio_crud.get_cvp_studio``, the Inputs/all read through
``studios_write.get_ndjson_all_values_with_bearer`` and the mutating POST
through ``urllib.request.urlopen``. Every refusal asserts no POST was made.

The fixture is the live **post-change** mainline root row captured 2026-09-02
(after the rogue-DHCP change controls). ``_pre_document`` strips those entries
back out so the worked example can be replayed against the "before" state and
proven to reproduce the fixture byte-for-byte (after canonicalisation).
"""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from cvp_mcp.grpc import studio_mss_inputs as mss
from cvp_mcp.grpc.inputs_digest import inputs_sha256
from cvp_mcp.write_access import WRITES_ENV

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "inputs_mss_service_root_2026-09-02.json"
)
FIXTURE = json.loads(FIXTURE_PATH.read_text())
POST_DOC = FIXTURE["inputs"]
POST_SHA = inputs_sha256(POST_DOC)

STUDIO_ID = "studio-mss-service"
WORKSPACE = "ws-mcp-mss-test-20260902-abcd1234"
DATADICT = {"cvtoken": "container-token", "cvp": "cvp.example.com", "cert": None}

PDU_GROUP = "pdu4-trendnet"
PDU_SERVICES = ("dhcp-server-replies", "dhcp-server-port", "dns-server-port")
PDU_RULES = ("drop-dhcp-from-pdu4", "drop-dhcp-to-pdu4", "drop-dns-to-pdu4")


@pytest.fixture(autouse=True)
def _writes_on(monkeypatch):
    monkeypatch.setenv(WRITES_ENV, "1")


# --- documents --------------------------------------------------------------


def _pre_document() -> dict:
    """The fixture with the 2026-09-02 change removed."""
    doc = copy.deepcopy(POST_DOC)
    doc["staticGroups"] = [g for g in doc["staticGroups"] if g["name"] != PDU_GROUP]
    doc["services"] = [s for s in doc["services"] if s["name"] not in PDU_SERVICES]
    doc["rules"] = [r for r in doc["rules"] if r["name"] not in PDU_RULES]
    doc["policies"][0]["policyRules"] = ["monitor"]
    return doc


PRE_DOC = _pre_document()
PRE_SHA = inputs_sha256(PRE_DOC)


def _entry(collection: str, name: str, document: dict = POST_DOC) -> dict:
    for item in document[collection]:
        if item["name"] == name:
            return copy.deepcopy(item)
    raise KeyError(f"{collection}:{name}")


def _worked_example_ops() -> list[dict]:
    """Spec §D.7, regenerated from the fixture (entries exactly as stored)."""
    ops = [
        {
            "op": "upsert",
            "collection": "staticGroups",
            "entry": _entry("staticGroups", PDU_GROUP),
        }
    ]
    ops += [
        {"op": "upsert", "collection": "services", "entry": _entry("services", n)}
        for n in PDU_SERVICES
    ]
    ops += [
        {"op": "upsert", "collection": "rules", "entry": _entry("rules", n)}
        for n in PDU_RULES
    ]
    ops.append(
        {
            "op": "set_policy_rules",
            "policy": "POL1",
            "policy_rules": [*PDU_RULES, "monitor"],
        }
    )
    return ops


def _rule(
    name="t-rule",
    action="drop",
    sources=("<any>",),
    destinations=(PDU_GROUP,),
    services=("dhcp-server-port",),
    **extra,
):
    rule = {
        "name": name,
        "action": action,
        "sources": list(sources),
        "destinations": list(destinations),
        "services": list(services),
        "packet": "any",
        "direction": True,
        "monitorName": "ztx-7230",
    }
    rule.update(extra)
    return rule


def _group(name="t-group", members=("10.0.3.9/32",)):
    return {"name": name, "membership": {"members": list(members)}}


def _service(
    name="t-svc", protocol="udp", sourceports="all", destinationports="123", **extra
):
    config = {
        "protocol": protocol,
        "sourceports": sourceports,
        "destinationports": destinationports,
    }
    config.update(extra)
    return {"name": name, "protocols": "TCP/UDP", "configurations": [config]}


# --- mocks ------------------------------------------------------------------


def _row(workspace_id: str, document: dict) -> dict:
    return {
        "key": {"studioId": STUDIO_ID, "workspaceId": workspace_id, "path": {}},
        "inputs": json.dumps(document),
    }


def _workspace_value(state="WORKSPACE_STATE_PENDING"):
    return {
        "value": {
            "key": {"workspaceId": WORKSPACE},
            "displayName": "mss test",
            "state": state,
        },
        "time": "2026-09-02T20:00:00Z",
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


STUDIO_404 = {"coverage": "none", "object": None, "warnings": ["http_error:404"]}
STUDIO_500 = {"coverage": "none", "object": None, "warnings": ["http_error:500"]}


@contextmanager
def _mocked(*, workspace=None, studio=None, rows=None, nd_warnings=(), nd_err=None):
    """``rows`` is the Inputs/all stream (default: mainline = post-change fixture)."""
    if workspace is None:
        ws_result = (_workspace_value(), None)
    elif workspace == ("missing",):
        ws_result = (None, "http_error:404")
    elif isinstance(workspace, tuple) and workspace[0] == "error":
        ws_result = (None, workspace[1])
    else:
        ws_result = (workspace, None)

    if studio is None:
        studio_kwargs = {"return_value": _studio_env()}
    elif isinstance(studio, list):
        studio_kwargs = {"side_effect": studio}
    else:
        studio_kwargs = {"return_value": studio}

    stream = [_row("", POST_DOC)] if rows is None else list(rows)
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
            {"value": {}, "time": "2026-09-02T20:05:00Z"}
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


def _details(env):
    return _obj(env)["error"]["details"]


def _posted_body(urlopen):
    return json.loads(urlopen.call_args[0][0].data.decode())


def _call(
    operations, *, expected=POST_SHA, confirm=False, token=None, workspace_id=WORKSPACE
):
    return mss.set_cvp_mss_policy_inputs(
        DATADICT,
        workspace_id,
        expected,
        operations,
        confirm=confirm,
        preview_token_value=token,
    )


def _refused_no_http(env, code, mocks):
    assert _obj(env)["outcome"] == "refused", env
    assert _code(env) == code, env
    assert env["coverage"] == "none"
    mocks["urlopen"].assert_not_called()


# --- the worked example round-trips the live change --------------------------


def test_worked_example_reproduces_post_change_fixture():
    with _mocked(rows=[_row("", PRE_DOC)]) as mocks:
        env = _call(_worked_example_ops(), expected=PRE_SHA)
    obj = _obj(env)
    assert obj["outcome"] == "preview", env
    assert obj["dry_run"] is True
    assert obj["before_sha256"] == PRE_SHA
    assert obj["after_sha256"] == POST_SHA, "ops must reproduce what the UI wrote"
    assert obj["operations_applied"] == 8
    assert obj["changed_leaves"] == 4
    assert obj["changed_leaf_paths"] == [
        "$.policies[0].policyRules",
        "$.rules",
        "$.services",
        "$.staticGroups",
    ]
    assert len(obj["entries_added"]) == 7
    assert obj["entries_replaced"] == ["policies:POL1"]
    assert obj["entries_removed"] == []
    assert env["warnings"] == []
    assert obj["posted_at_root"] is True
    assert obj["disruptive"] is False
    assert obj["request_body"]["key"] == {
        "studioId": STUDIO_ID,
        "workspaceId": WORKSPACE,
        "path": {"values": []},
    }
    assert json.loads(obj["request_body"]["inputs"]) == POST_DOC
    assert obj["preview_token"]
    mocks["urlopen"].assert_not_called()


def test_fixture_hidden_mappers_do_not_reference_the_change():
    """Spec §D.0 stop condition, pinned so a re-capture cannot silently flip it."""
    hidden = {k: v for k, v in POST_DOC.items() if k.startswith("hidden")}
    text = json.dumps(hidden)
    assert PDU_GROUP not in text
    assert not any(name in text for name in PDU_RULES + PDU_SERVICES)


# --- gates before HTTP --------------------------------------------------------


def test_writes_disabled_refuses_before_any_get(monkeypatch):
    monkeypatch.delenv(WRITES_ENV, raising=False)
    with _mocked() as mocks:
        env = _call(_worked_example_ops())
    _refused_no_http(env, "writes_disabled", mocks)
    mocks["workspace_get"].assert_not_called()
    mocks["inputs_get"].assert_not_called()


@pytest.mark.parametrize(
    ("workspace_id", "code"),
    [
        ("", "workspace_id_required"),
        ("builtin-x", "builtin_workspace_forbidden"),
        ("ws-other", "invalid_workspace_id"),
    ],
)
def test_workspace_id_rules(workspace_id, code):
    with _mocked() as mocks:
        env = _call(_worked_example_ops(), workspace_id=workspace_id)
    _refused_no_http(env, code, mocks)
    mocks["workspace_get"].assert_not_called()


@pytest.mark.parametrize(
    "expected", [None, "", "abc", "A" * 64, POST_SHA[:63], POST_SHA.upper()]
)
def test_malformed_digest_refuses_before_http(expected):
    with _mocked() as mocks:
        env = _call(_worked_example_ops(), expected=expected)
    _refused_no_http(env, "expected_inputs_sha256_required", mocks)
    mocks["workspace_get"].assert_not_called()


@pytest.mark.parametrize("operations", [None, [], "upsert", {}])
def test_empty_operations_required(operations):
    with _mocked() as mocks:
        env = _call(operations)
    _refused_no_http(env, "mss_operations_required", mocks)
    mocks["workspace_get"].assert_not_called()


def test_too_many_operations():
    ops = [
        {"op": "upsert", "collection": "staticGroups", "entry": _group(f"g{i}")}
        for i in range(21)
    ]
    with _mocked() as mocks:
        env = _call(ops)
    _refused_no_http(env, "mss_operations_too_many", mocks)
    assert _details(env)["count"] == 21


def test_collection_not_allowed():
    ops = [{"op": "upsert", "collection": "securityDomains", "entry": {"name": "x"}}]
    with _mocked() as mocks:
        env = _call(ops)
    _refused_no_http(env, "mss_collection_not_allowed", mocks)
    mocks["workspace_get"].assert_not_called()


def test_unknown_op_and_missing_fields_are_invalid_with_path():
    with _mocked() as mocks:
        env = _call([{"op": "replace", "collection": "rules", "entry": _rule()}])
    _refused_no_http(env, "mss_operation_invalid", mocks)
    assert _details(env)["path"] == "operations[0].op"

    with _mocked() as mocks:
        env = _call([{"op": "upsert", "collection": "rules"}])
    _refused_no_http(env, "mss_operation_invalid", mocks)
    assert _details(env)["path"] == "operations[0].entry"


def test_lint_runs_on_operations_before_http():
    ops = [
        {
            "op": "upsert",
            "collection": "policies",
            "entry": {
                "name": "POL1",
                "description": "please shutdown it",
                "policyRules": ["monitor"],
            },
        }
    ]
    with _mocked() as mocks:
        env = _call(ops)
    _refused_no_http(env, "disruptive_content_forbidden", mocks)
    assert "shutdown" in _details(env)["matched"]
    mocks["workspace_get"].assert_not_called()


def test_lint_ignores_pre_existing_text_in_untouched_leaves():
    doctored = copy.deepcopy(POST_DOC)
    doctored["acceptedGroups"][0]["name"] = "AGNI-CH-shutdown-lab"
    benign = [{"op": "upsert", "collection": "staticGroups", "entry": _group()}]
    with _mocked(rows=[_row("", doctored)]) as mocks:
        env = _call(benign, expected=inputs_sha256(doctored))
    assert _obj(env)["outcome"] == "preview", env
    mocks["urlopen"].assert_not_called()


# --- entry schema -------------------------------------------------------------


@pytest.mark.parametrize(
    ("collection", "entry", "path_suffix"),
    [
        (
            "staticGroups",
            {**_group(), "staticExceptionList": []},
            ".entry.staticExceptionList",
        ),
        (
            "staticGroups",
            _group(members=("10.0.3.9/33",)),
            ".entry.membership.members[0]",
        ),
        (
            "staticGroups",
            _group(members=("0.0.0.0/0",)),
            ".entry.membership.members[0]",
        ),
        ("staticGroups", _group(members=("::/0",)), ".entry.membership.members[0]"),
        ("staticGroups", _group(members=()), ".entry.membership.members"),
        ("staticGroups", _group(name="<any>"), ".entry.name"),
        ("staticGroups", _group(name=" spaced"), ".entry.name"),
        (
            "services",
            _service(destinationports="70000"),
            ".entry.configurations[0].destinationports",
        ),
        (
            "services",
            _service(sourceports="9-3"),
            ".entry.configurations[0].sourceports",
        ),
        ("services", _service(protocol="gre"), ".entry.configurations[0].protocol"),
        ("services", _service(icmpTypes="300"), ".entry.configurations[0].icmpTypes"),
        ("services", {**_service(), "protocols": "GRE"}, ".entry.protocols"),
        ("services", {**_service(), "extra": 1}, ".entry.extra"),
        ("rules", _rule(action="deny"), ".entry.action"),
        ("rules", _rule(direction="both"), ".entry.direction"),
        ("rules", _rule(packet="ip"), ".entry.packet"),
        ("rules", _rule(sources=()), ".entry.sources"),
        ("rules", {**_rule(), "description": "x"}, ".entry.description"),
        (
            "policies",
            {"name": "POL1", "policyRules": ["monitor"], "vrf": "x"},
            ".entry.vrf",
        ),
        (
            "policies",
            {"name": "POL1", "policyRules": ["monitor", "monitor"]},
            ".entry.policyRules",
        ),
        ("policies", {"name": "POL1", "policyRules": []}, ".entry.policyRules"),
    ],
)
def test_entry_schema_violations_refuse_with_path(collection, entry, path_suffix):
    with _mocked() as mocks:
        env = _call([{"op": "upsert", "collection": collection, "entry": entry}])
    _refused_no_http(env, "mss_operation_invalid", mocks)
    assert _details(env)["path"] == f"operations[0]{path_suffix}", _details(env)
    mocks["workspace_get"].assert_not_called()


def test_port_grammar_accepts_ranges_and_lists():
    ops = [
        {
            "op": "upsert",
            "collection": "services",
            "entry": _service(destinationports="53,5353,6000-6010"),
        }
    ]
    with _mocked() as mocks:
        env = _call(ops)
    assert _obj(env)["outcome"] == "preview", env
    mocks["urlopen"].assert_not_called()


# --- preflight ------------------------------------------------------------------


def test_workspace_missing_and_errors():
    with _mocked(workspace=("missing",)) as mocks:
        env = _call(_worked_example_ops())
    _refused_no_http(env, "workspace_not_found", mocks)
    mocks["inputs_get"].assert_not_called()

    with _mocked(workspace=("error", "http_error:503")) as mocks:
        env = _call(_worked_example_ops())
    _refused_no_http(env, "workspace_read_failed", mocks)

    with _mocked(
        workspace=_workspace_value(state="WORKSPACE_STATE_SUBMITTED")
    ) as mocks:
        env = _call(_worked_example_ops())
    _refused_no_http(env, "workspace_not_pending", mocks)

    with _mocked(workspace=_workspace_value(state="")) as mocks:
        env = _call(_worked_example_ops())
    _refused_no_http(env, "workspace_state_unknown", mocks)


def test_studio_overlay_then_mainline_404_only():
    with _mocked(studio=[STUDIO_404, _studio_env()]) as mocks:
        env = _call([{"op": "upsert", "collection": "staticGroups", "entry": _group()}])
    assert _obj(env)["outcome"] == "preview", env
    assert mocks["studio_get"].call_count == 2

    with _mocked(studio=[STUDIO_500, _studio_env()]) as mocks:
        env = _call([{"op": "upsert", "collection": "staticGroups", "entry": _group()}])
    _refused_no_http(env, "preflight_failed", mocks)
    assert mocks["studio_get"].call_count == 1, "non-404 must not fall through"
    mocks["inputs_get"].assert_not_called()

    with _mocked(studio=[STUDIO_404, STUDIO_404]) as mocks:
        env = _call([{"op": "upsert", "collection": "staticGroups", "entry": _group()}])
    _refused_no_http(env, "preflight_failed", mocks)


@pytest.mark.parametrize(
    ("flag", "code"),
    [("immutable", "studio_immutable"), ("from_package", "studio_from_package")],
)
def test_studio_flags_refuse(flag, code):
    with _mocked(studio=_studio_env(**{flag: True})) as mocks:
        env = _call(_worked_example_ops())
    _refused_no_http(env, code, mocks)
    mocks["inputs_get"].assert_not_called()


@pytest.mark.parametrize(
    "warning", ["truncated_to_96000000", "ndjson_skip_invalid_line:3"]
)
def test_incomplete_inputs_stream_fails_closed(warning):
    with _mocked(nd_warnings=[warning]) as mocks:
        env = _call(_worked_example_ops())
    _refused_no_http(env, "preflight_failed", mocks)


def test_inputs_read_error_fails_closed():
    with _mocked(nd_err="http_error:502") as mocks:
        env = _call(_worked_example_ops())
    _refused_no_http(env, "preflight_failed", mocks)


def test_missing_root_row_is_unresolved():
    with _mocked(rows=[]) as mocks:
        env = _call(_worked_example_ops())
    _refused_no_http(env, "inputs_path_unresolved", mocks)


# --- digest CAS ---------------------------------------------------------------


def test_digest_mismatch_reports_current_and_source():
    with _mocked(rows=[_row("", POST_DOC)]) as mocks:
        env = _call(_worked_example_ops(), expected=PRE_SHA)
    _refused_no_http(env, "inputs_digest_mismatch", mocks)
    assert _details(env)["current_inputs_sha256"] == POST_SHA
    assert _details(env)["inputs_source_workspace_id"] == ""


def test_overlay_row_preferred_over_mainline():
    with _mocked(rows=[_row("", POST_DOC), _row(WORKSPACE, PRE_DOC)]) as mocks:
        env = _call(_worked_example_ops(), expected=PRE_SHA)
    obj = _obj(env)
    assert obj["outcome"] == "preview", env
    assert obj["inputs_source_workspace_id"] == WORKSPACE
    assert obj["before_sha256"] == PRE_SHA
    mocks["urlopen"].assert_not_called()


def test_other_draft_overlay_is_ignored():
    with _mocked(
        rows=[_row("", POST_DOC), _row("ws-mcp-someone-else", PRE_DOC)]
    ) as mocks:
        env = _call(_worked_example_ops(), expected=PRE_SHA)
    _refused_no_http(env, "inputs_digest_mismatch", mocks)


# --- apply / result --------------------------------------------------------------


def test_upsert_replace_yields_nested_paths():
    ops = [
        {
            "op": "upsert",
            "collection": "staticGroups",
            "entry": _group("trogdor", ("10.0.8.81/32",)),
        }
    ]
    with _mocked() as mocks:
        env = _call(ops)
    obj = _obj(env)
    assert obj["outcome"] == "preview", env
    assert obj["entries_replaced"] == ["staticGroups:trogdor"]
    assert obj["changed_leaf_paths"] == ["$.staticGroups[0].membership.members[0]"]
    mocks["urlopen"].assert_not_called()


def test_remove_unreferenced_group_and_service():
    ops = [
        {"op": "remove", "collection": "staticGroups", "name": "laptops"},
        {"op": "remove", "collection": "services", "name": "rtsp-554"},
    ]
    with _mocked() as mocks:
        env = _call(ops)
    obj = _obj(env)
    assert obj["outcome"] == "preview", env
    assert obj["entries_removed"] == ["staticGroups:laptops", "services:rtsp-554"]
    assert sorted(obj["changed_leaf_paths"]) == ["$.services", "$.staticGroups"]
    mocks["urlopen"].assert_not_called()


def test_remove_missing_entry():
    with _mocked() as mocks:
        env = _call([{"op": "remove", "collection": "rules", "name": "nope"}])
    _refused_no_http(env, "mss_entry_not_found", mocks)


def test_remove_referenced_group_names_the_rule():
    with _mocked() as mocks:
        env = _call([{"op": "remove", "collection": "staticGroups", "name": PDU_GROUP}])
    _refused_no_http(env, "mss_reference_unresolved", mocks)
    referrers = {u["referrer"] for u in _details(env)["unresolved"]}
    assert "rules:drop-dhcp-from-pdu4" in referrers


def test_remove_rule_still_in_policy_names_the_policy():
    with _mocked() as mocks:
        env = _call(
            [{"op": "remove", "collection": "rules", "name": "drop-dns-to-pdu4"}]
        )
    _refused_no_http(env, "mss_reference_unresolved", mocks)
    assert _details(env)["unresolved"][0]["referrer"] == "policies:POL1"


def test_rule_referencing_unknown_service():
    with _mocked() as mocks:
        env = _call(
            [
                {
                    "op": "upsert",
                    "collection": "rules",
                    "entry": _rule(services=("nope",)),
                }
            ]
        )
    _refused_no_http(env, "mss_reference_unresolved", mocks)
    assert _details(env)["unresolved"][0] == {
        "referrer": "rules:t-rule",
        "field": "services",
        "missing": "nope",
    }


def test_rule_may_reference_agni_group():
    ops = [
        {
            "op": "upsert",
            "collection": "rules",
            "entry": _rule(sources=("AGNI-CH-printers",)),
        }
    ]
    with _mocked() as mocks:
        env = _call(ops)
    assert _obj(env)["outcome"] == "preview", env
    mocks["urlopen"].assert_not_called()


def test_static_group_may_not_collide_with_agni_group():
    ops = [
        {
            "op": "upsert",
            "collection": "staticGroups",
            "entry": _group("AGNI-CH-printers"),
        }
    ]
    with _mocked() as mocks:
        env = _call(ops)
    _refused_no_http(env, "mss_operation_invalid", mocks)
    assert _details(env)["path"] == "operations[0].entry.name"


def test_unknown_monitor_name_is_invalid():
    with _mocked() as mocks:
        env = _call(
            [
                {
                    "op": "upsert",
                    "collection": "rules",
                    "entry": _rule(monitorName="ghost"),
                }
            ]
        )
    _refused_no_http(env, "mss_operation_invalid", mocks)
    assert _details(env)["monitorName"] == "ghost"


def test_policies_upsert_existing_ok_new_refused_remove_refused():
    with _mocked() as mocks:
        env = _call(
            [
                {
                    "op": "upsert",
                    "collection": "policies",
                    "entry": {
                        "name": "POL1",
                        "description": "dhcp guard",
                        "policyRules": ["monitor"],
                    },
                }
            ]
        )
    obj = _obj(env)
    assert obj["outcome"] == "preview", env
    assert obj["entries_replaced"] == ["policies:POL1"]
    mocks["urlopen"].assert_not_called()

    with _mocked() as mocks:
        env = _call(
            [
                {
                    "op": "upsert",
                    "collection": "policies",
                    "entry": {"name": "POL2", "policyRules": ["monitor"]},
                }
            ]
        )
    _refused_no_http(env, "mss_entry_not_found", mocks)

    with _mocked() as mocks:
        env = _call([{"op": "remove", "collection": "policies", "name": "POL1"}])
    _refused_no_http(env, "mss_operation_invalid", mocks)
    mocks["workspace_get"].assert_not_called()


def test_set_policy_rules_errors():
    with _mocked() as mocks:
        env = _call(
            [
                {
                    "op": "set_policy_rules",
                    "policy": "POL1",
                    "policy_rules": ["monitor", "ghost"],
                }
            ]
        )
    _refused_no_http(env, "mss_reference_unresolved", mocks)

    with _mocked() as mocks:
        env = _call(
            [
                {
                    "op": "set_policy_rules",
                    "policy": "POL1",
                    "policy_rules": ["monitor", "monitor"],
                }
            ]
        )
    _refused_no_http(env, "mss_operation_invalid", mocks)

    with _mocked() as mocks:
        env = _call([{"op": "set_policy_rules", "policy": "POL1", "policy_rules": []}])
    _refused_no_http(env, "mss_operation_invalid", mocks)

    with _mocked() as mocks:
        env = _call(
            [{"op": "set_policy_rules", "policy": "POL9", "policy_rules": ["monitor"]}]
        )
    _refused_no_http(env, "mss_entry_not_found", mocks)


def test_drop_all_any_is_too_broad():
    with _mocked() as mocks:
        env = _call(
            [
                {
                    "op": "upsert",
                    "collection": "rules",
                    "entry": _rule(destinations=("<any>",), services=("<any>",)),
                }
            ]
        )
    _refused_no_http(env, "mss_rule_too_broad", mocks)
    assert _details(env)["rule"] == "t-rule"

    monitor_as_drop = _entry("rules", "monitor")
    monitor_as_drop["action"] = "drop"
    with _mocked() as mocks:
        env = _call([{"op": "upsert", "collection": "rules", "entry": monitor_as_drop}])
    _refused_no_http(env, "mss_rule_too_broad", mocks)


def test_drop_with_any_endpoints_warns_only_for_touched_rule():
    ops = [
        {
            "op": "upsert",
            "collection": "rules",
            "entry": _rule(
                "drop-dns-everywhere",
                destinations=("<any>",),
                services=("dns-server-port",),
            ),
        }
    ]
    with _mocked() as mocks:
        env = _call(ops)
    assert _obj(env)["outcome"] == "preview", env
    assert env["warnings"] == ["mss_rule_broad:drop-dns-everywhere"]
    mocks["urlopen"].assert_not_called()

    # The broad rule now exists on "mainline"; an unrelated edit must not nag.
    doc = copy.deepcopy(POST_DOC)
    doc["rules"].append(
        _rule(
            "drop-dns-everywhere",
            destinations=("<any>",),
            services=("dns-server-port",),
        )
    )
    with _mocked(rows=[_row("", doc)]) as mocks:
        env = _call(
            [{"op": "upsert", "collection": "staticGroups", "entry": _group()}],
            expected=inputs_sha256(doc),
        )
    assert env["warnings"] == []


def test_forward_all_before_drop_warns_shadowed():
    ops = [
        {
            "op": "set_policy_rules",
            "policy": "POL1",
            "policy_rules": ["monitor", *PDU_RULES],
        }
    ]
    with _mocked() as mocks:
        env = _call(ops)
    assert _obj(env)["outcome"] == "preview", env
    assert env["warnings"] == ["mss_rule_shadowed:POL1:monitor"]
    mocks["urlopen"].assert_not_called()


def test_noop_ops_warn_unchanged():
    ops = [
        {
            "op": "upsert",
            "collection": "staticGroups",
            "entry": _entry("staticGroups", "trogdor"),
        }
    ]
    with _mocked() as mocks:
        env = _call(ops)
    obj = _obj(env)
    assert obj["outcome"] == "preview", env
    assert obj["changed_leaves"] == 0
    assert obj["before_sha256"] == obj["after_sha256"] == POST_SHA
    assert "inputs_unchanged" in env["warnings"]
    mocks["urlopen"].assert_not_called()


def test_applier_touching_out_of_scope_key_is_caught(monkeypatch):
    real = mss._apply_operations

    def leaky(document, operations):
        after, summary, error = real(document, operations)
        after["securityDomains"] = []
        return after, summary, error

    monkeypatch.setattr(mss, "_apply_operations", leaky)
    with _mocked() as mocks:
        env = _call([{"op": "upsert", "collection": "staticGroups", "entry": _group()}])
    _refused_no_http(env, "tree_diff_outside_mss_scope", mocks)
    assert _details(env)["outside"] == ["$.securityDomains"]


# --- preview token and confirm -----------------------------------------------------


def test_confirm_requires_matching_token_and_posts_once():
    ops = _worked_example_ops()
    with _mocked(rows=[_row("", PRE_DOC)]) as mocks:
        preview = _call(ops, expected=PRE_SHA)
    token = _obj(preview)["preview_token"]

    with _mocked(rows=[_row("", PRE_DOC)]) as mocks:
        env = _call(ops, expected=PRE_SHA, confirm=True)
    _refused_no_http(env, "preview_required", mocks)

    with _mocked(rows=[_row("", PRE_DOC)]) as mocks:
        env = _call(ops, expected=PRE_SHA, confirm=True, token="deadbeef")
    _refused_no_http(env, "preview_required", mocks)

    with _mocked(rows=[_row("", PRE_DOC)]) as mocks:
        env = _call(ops, expected=PRE_SHA, confirm=True, token=token)
    obj = _obj(env)
    assert obj["outcome"] == "accepted", env
    assert obj["dry_run"] is False
    assert obj["resource_time"] == "2026-09-02T20:05:00Z"
    assert obj["next_action"].startswith("build_cvp_workspace")
    assert mocks["urlopen"].call_count == 1
    body = _posted_body(mocks["urlopen"])
    assert body["key"] == {
        "studioId": STUDIO_ID,
        "workspaceId": WORKSPACE,
        "path": {"values": []},
    }
    assert isinstance(body["inputs"], str)
    posted = json.loads(body["inputs"])
    assert posted == POST_DOC
    assert list(posted.keys()) == list(PRE_DOC.keys()), "wire key order preserved"
    assert (
        mocks["urlopen"]
        .call_args[0][0]
        .full_url.endswith("/api/resources/studio/v1/InputsConfig")
    )


def test_token_replay_with_altered_operations_refused():
    with _mocked(rows=[_row("", PRE_DOC)]):
        preview = _call(_worked_example_ops(), expected=PRE_SHA)
    token = _obj(preview)["preview_token"]
    altered = _worked_example_ops()
    altered[0]["entry"]["membership"]["members"] = ["10.0.3.0/24"]
    with _mocked(rows=[_row("", PRE_DOC)]) as mocks:
        env = _call(altered, expected=PRE_SHA, confirm=True, token=token)
    _refused_no_http(env, "preview_required", mocks)


def test_mainline_changed_between_preview_and_confirm():
    ops = [{"op": "upsert", "collection": "staticGroups", "entry": _group()}]
    with _mocked(rows=[_row("", PRE_DOC)]):
        preview = _call(ops, expected=PRE_SHA)
    token = _obj(preview)["preview_token"]
    with _mocked(rows=[_row("", POST_DOC)]) as mocks:
        env = _call(ops, expected=PRE_SHA, confirm=True, token=token)
    _refused_no_http(env, "inputs_digest_mismatch", mocks)


def test_post_failure_is_resource_write_failed():
    ops = [{"op": "upsert", "collection": "staticGroups", "entry": _group()}]
    with _mocked():
        token = _obj(_call(ops))["preview_token"]
    with (
        _mocked() as mocks,
        patch(
            "cvp_mcp.grpc.studio_mss_inputs.post_resource_config",
            return_value=(None, "http_error:500"),
        ),
    ):
        env = _call(ops, confirm=True, token=token)
    assert _code(env) == "resource_write_failed"
    mocks["urlopen"].assert_not_called()
