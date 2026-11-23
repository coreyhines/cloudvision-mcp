"""Tests for Streaming Telemetry root Inputs compare-and-set."""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from cvp_mcp.grpc import studio_telemetry_inputs as tel
from cvp_mcp.grpc.inputs_digest import inputs_sha256
from cvp_mcp.write_access import WRITES_ENV

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "inputs_telemetry_config_root_minimal.json"
)
PRE_DOC = json.loads(FIXTURE_PATH.read_text())["inputs"]
PRE_SHA = inputs_sha256(PRE_DOC)

STUDIO_ID = "studio-telemetry-config"
WORKSPACE = "ws-mcp-telemetry-test-20261123"
DATADICT = {"cvtoken": "container-token", "cvp": "cvp.example.com", "cert": None}
TOKEN_PATH = "/tmp/cv-onboarding-token"

ZTX_PROFILE = {
    "profileName": "staging-mgmt-ztx",
    "multi_cluster": False,
    "disableAAAResolver": {"disableAAA": True},
    "cluster_profiles": [
        {
            "cvopt_name": "cvaas",
            "ingestPort": 443,
            "cvopt_cvaddr_group": {"addr1": "apiserver.cv-staging.corp.arista.io"},
            "cvopt_cvaddr_v6_group": {},
        }
    ],
    "dev_collection": [
        {
            "cluster_profile_name": "cvaas",
            "cvopt_auth_token": TOKEN_PATH,
            "cvopt_auth_type": "token-secure",
            "cvopt_vrf": "mgmt",
            "cvopt_proxy": None,
            "cvopt_sourceintf": None,
        }
    ],
    "customFlags": {},
    "select_active_cluster": {},
    "sourceInterfaceAndVrf": {"source_vrf": "mgmt"},
}


def _doc():
    return copy.deepcopy(PRE_DOC)


def _profile(doc, name):
    return next(p for p in doc["onboardingProfiles"] if p["profileName"] == name)


def _row(doc, query):
    return next(r for r in doc["profileAssignment"] if r["tags"]["query"] == query)


def _apply(raw_ops, doc=None):
    ops, err = tel.normalize_operations(raw_ops)
    assert err is None, err
    return tel.apply_operations(doc if doc is not None else _doc(), ops)


# --- fixture ----------------------------------------------------------------


def test_fixture_digest_matches_live_capture():
    assert PRE_SHA == (
        "525a902dacfeab057676b0ee97f39c7f80f50249cb652ef8547ea94618171032"
    )


# --- normalize --------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, [], "x", {}])
def test_operations_required(raw):
    _, err = tel.normalize_operations(raw)
    assert err["code"] == "telemetry_operations_required"


def test_operations_too_many():
    raw = [{"op": "remove_onboarding_profile", "profileName": "x"}] * 21
    _, err = tel.normalize_operations(raw)
    assert err["code"] == "telemetry_operations_too_many"
    assert err["details"]["max"] == 20


@pytest.mark.parametrize(
    "raw, path",
    [
        ({"op": "nope"}, "operations[0].op"),
        (
            {"op": "patch_onboarding_profile", "profileName": "a", "patch": {}},
            "operations[0].patch",
        ),
        (
            {
                "op": "patch_onboarding_profile",
                "profileName": "a",
                "patch": {"source_vrf": "mgmt"},
            },
            "operations[0].patch.source_vrf",
        ),
        (
            {
                "op": "patch_onboarding_profile",
                "profileName": "a",
                "patch": {"ingestPort": 70000},
            },
            "operations[0].patch.ingestPort",
        ),
        (
            {
                "op": "patch_onboarding_profile",
                "profileName": "a",
                "patch": {"disableAAA": "yes"},
            },
            "operations[0].patch.disableAAA",
        ),
        (
            {
                "op": "patch_onboarding_profile",
                "profileName": "a",
                "patch": {"cvopt_auth_token": "/tmp/x\n"},
            },
            "operations[0].patch.cvopt_auth_token",
        ),
        (
            {"op": "set_profile_assignment", "query": "device:X"},
            "operations[0].onboardingProfile",
        ),
        (
            {"op": "remove_onboarding_profile", "profileName": " a"},
            "operations[0].profileName",
        ),
        (
            {"op": "remove_onboarding_profile", "profileName": "a", "extra": 1},
            "operations[0].extra",
        ),
    ],
)
def test_operation_invalid(raw, path):
    _, err = tel.normalize_operations([raw])
    assert err["code"] == "telemetry_operation_invalid"
    assert err["details"]["path"] == path


def test_upsert_profile_validation():
    bad = copy.deepcopy(ZTX_PROFILE)
    bad["dev_collection"][0]["cluster_profile_name"] = "other"
    _, err = tel.normalize_operations(
        [{"op": "upsert_onboarding_profile", "profile": bad}]
    )
    assert err["code"] == "telemetry_operation_invalid"
    assert err["details"]["path"].endswith("dev_collection[0].cluster_profile_name")

    bad = copy.deepcopy(ZTX_PROFILE)
    bad["surprise"] = True
    _, err = tel.normalize_operations(
        [{"op": "upsert_onboarding_profile", "profile": bad}]
    )
    assert err["details"]["path"] == "operations[0].profile.surprise"

    bad = copy.deepcopy(ZTX_PROFILE)
    del bad["cluster_profiles"]
    _, err = tel.normalize_operations(
        [{"op": "upsert_onboarding_profile", "profile": bad}]
    )
    assert err["details"]["path"] == "operations[0].profile.cluster_profiles"


def test_normalize_deep_copies_input():
    raw = [{"op": "upsert_onboarding_profile", "profile": copy.deepcopy(ZTX_PROFILE)}]
    ops, err = tel.normalize_operations(raw)
    assert err is None
    raw[0]["profile"]["profileName"] = "mutated"
    assert ops[0]["profile"]["profileName"] == "staging-mgmt-ztx"


# --- apply ------------------------------------------------------------------


def test_patch_sets_auth_token_path():
    doc = _doc()
    after, summary, err = _apply(
        [
            {
                "op": "patch_onboarding_profile",
                "profileName": "campus-1709",
                "patch": {"cvopt_auth_token": TOKEN_PATH},
            }
        ],
        doc,
    )
    assert err is None
    assert _profile(after, "campus-1709")["dev_collection"][0]["cvopt_auth_token"] == (
        TOKEN_PATH
    )
    assert summary["replaced"] == ["profile:campus-1709"]
    # Input document untouched.
    assert doc == PRE_DOC


def test_patch_all_leaves():
    after, _, err = _apply(
        [
            {
                "op": "patch_onboarding_profile",
                "profileName": "campus-1709",
                "patch": {
                    "cvopt_vrf": "mgmt",
                    "disableAAA": False,
                    "multi_cluster": False,
                    "addr1": "apiserver.example.net",
                    "ingestPort": 9910,
                },
            }
        ]
    )
    assert err is None
    prof = _profile(after, "campus-1709")
    assert prof["dev_collection"][0]["cvopt_vrf"] == "mgmt"
    assert prof["disableAAAResolver"] == {"disableAAA": False}
    assert prof["multi_cluster"] is False
    assert prof["cluster_profiles"][0]["cvopt_cvaddr_group"]["addr1"] == (
        "apiserver.example.net"
    )
    assert prof["cluster_profiles"][0]["ingestPort"] == 9910
    # Top-level multi_cluster and resolver list are untouched.
    assert after["multi_cluster"] is False
    assert after["disableAAAResolver"] == PRE_DOC["disableAAAResolver"]


def test_patch_missing_profile():
    _, _, err = _apply(
        [
            {
                "op": "patch_onboarding_profile",
                "profileName": "nope",
                "patch": {"multi_cluster": True},
            }
        ]
    )
    assert err["code"] == "telemetry_profile_not_found"


def test_patch_ambiguous_without_cluster_name():
    doc = _doc()
    prof = _profile(doc, "campus-1709")
    prof["cluster_profiles"].append(
        {"cvopt_name": "onprem", "cvopt_cvaddr_group": {"addr1": "cv.local"}}
    )
    prof["dev_collection"].append({"cluster_profile_name": "onprem"})
    op = {
        "op": "patch_onboarding_profile",
        "profileName": "campus-1709",
        "patch": {"addr1": "cv2.local"},
    }
    _, _, err = _apply([op], copy.deepcopy(doc))
    assert err["code"] == "telemetry_patch_ambiguous"

    after, _, err = _apply([{**op, "cluster_profile_name": "onprem"}], doc)
    assert err is None
    clusters = _profile(after, "campus-1709")["cluster_profiles"]
    assert clusters[0]["cvopt_cvaddr_group"]["addr1"] == (
        "apiserver.cv-staging.corp.arista.io"
    )
    assert clusters[1]["cvopt_cvaddr_group"]["addr1"] == "cv2.local"


def test_patch_unknown_cluster_name():
    _, _, err = _apply(
        [
            {
                "op": "patch_onboarding_profile",
                "profileName": "campus-1709",
                "cluster_profile_name": "ghost",
                "patch": {"cvopt_vrf": "mgmt"},
            }
        ]
    )
    assert err["code"] == "telemetry_patch_target_missing"


def test_upsert_profile_and_assign_device():
    after, summary, err = _apply(
        [
            {"op": "upsert_onboarding_profile", "profile": ZTX_PROFILE},
            {
                "op": "set_profile_assignment",
                "query": "device:WTW23350044",
                "onboardingProfile": "staging-mgmt-ztx",
                "default_config": True,
            },
        ]
    )
    assert err is None
    assert [p["profileName"] for p in after["onboardingProfiles"]] == [
        "campus-1709",
        "staging-mgmt-ztx",
    ]
    row = _row(after, "device:WTW23350044")
    assert row["inputs"]["profile_assignment_group"] == {
        "default_config": True,
        "onboardingProfile": "staging-mgmt-ztx",
    }
    assert summary["added"] == [
        "profile:staging-mgmt-ztx",
        "assignment:device:WTW23350044",
    ]


def test_upsert_existing_profile_replaces_wholesale():
    replacement = copy.deepcopy(ZTX_PROFILE)
    replacement["profileName"] = "campus-1709"
    after, summary, err = _apply(
        [{"op": "upsert_onboarding_profile", "profile": replacement}]
    )
    assert err is None
    assert _profile(after, "campus-1709") == replacement
    assert len(after["onboardingProfiles"]) == 1
    assert summary["replaced"] == ["profile:campus-1709"]


def test_set_assignment_updates_existing_row_in_place():
    after, summary, err = _apply(
        [
            {
                "op": "set_profile_assignment",
                "query": "Campus:BLZ",
                "onboardingProfile": "campus-1709",
            }
        ]
    )
    assert err is None
    group = _row(after, "Campus:BLZ")["inputs"]["profile_assignment_group"]
    assert group == {"default_config": True, "onboardingProfile": "campus-1709"}
    assert len(after["profileAssignment"]) == 2
    assert summary["replaced"] == ["assignment:Campus:BLZ"]


def test_assignment_to_unknown_profile_refuses():
    _, _, err = _apply(
        [
            {
                "op": "set_profile_assignment",
                "query": "device:X",
                "onboardingProfile": "ghost",
            }
        ]
    )
    assert err["code"] == "telemetry_profile_reference_dangling"
    assert err["details"]["references"] == [
        {"query": "device:X", "onboardingProfile": "ghost"}
    ]


def test_remove_profile_still_assigned_refuses():
    _, _, err = _apply(
        [{"op": "remove_onboarding_profile", "profileName": "campus-1709"}]
    )
    assert err["code"] == "telemetry_profile_reference_dangling"


def test_remove_profile_after_unassign():
    after, summary, err = _apply(
        [
            {
                "op": "set_profile_assignment",
                "query": "Campus:campus-1709",
                "onboardingProfile": None,
            },
            {"op": "remove_onboarding_profile", "profileName": "campus-1709"},
        ]
    )
    assert err is None
    assert after["onboardingProfiles"] == []
    assert summary["removed"] == ["profile:campus-1709"]


def test_remove_missing_profile():
    _, _, err = _apply([{"op": "remove_onboarding_profile", "profileName": "nope"}])
    assert err["code"] == "telemetry_profile_not_found"


def test_preexisting_dangling_reference_does_not_block():
    doc = _doc()
    doc["profileAssignment"].append(
        {
            "inputs": {
                "profile_assignment_group": {
                    "default_config": True,
                    "onboardingProfile": "legacy-gone",
                }
            },
            "tags": {"query": "Campus:old"},
        }
    )
    _, _, err = _apply(
        [
            {
                "op": "patch_onboarding_profile",
                "profileName": "campus-1709",
                "patch": {"multi_cluster": False},
            }
        ],
        doc,
    )
    assert err is None


def test_malformed_document_refuses():
    doc = _doc()
    doc["onboardingProfiles"].append(copy.deepcopy(doc["onboardingProfiles"][0]))
    _, _, err = _apply(
        [{"op": "remove_onboarding_profile", "profileName": "x"}],
        doc,
    )
    assert err["code"] == "telemetry_document_malformed"


# --- CAS wiring (mocked HTTP) -----------------------------------------------


@pytest.fixture
def _writes_on(monkeypatch):
    monkeypatch.setenv(WRITES_ENV, "1")


def _root_row(document: dict) -> dict:
    return {
        "key": {"studioId": STUDIO_ID, "workspaceId": "", "path": {}},
        "inputs": json.dumps(document),
    }


def _workspace_value():
    return {
        "value": {
            "key": {"workspaceId": WORKSPACE},
            "displayName": "telemetry test",
            "state": "WORKSPACE_STATE_PENDING",
        },
        "time": "2026-11-23T20:00:00Z",
    }


@contextmanager
def _mocked(document=None):
    studio_env = {
        "coverage": "full",
        "object": {"studio_id": STUDIO_ID, "immutable": None, "from_package": None},
        "warnings": [],
    }
    stream = [_root_row(PRE_DOC if document is None else document)]
    with (
        patch(
            "cvp_mcp.grpc.studios.get_json_with_bearer",
            return_value=(_workspace_value(), None),
        ),
        patch("cvp_mcp.grpc.studio_crud.get_cvp_studio", return_value=studio_env),
        patch(
            "cvp_mcp.grpc.studios_write.get_ndjson_all_values_with_bearer",
            return_value=(stream, None, []),
        ),
        patch("urllib.request.urlopen") as urlopen,
    ):
        resp = urlopen.return_value.__enter__.return_value
        resp.read.return_value = json.dumps(
            {"value": {}, "time": "2026-11-23T20:05:00Z"}
        ).encode()
        yield urlopen


PATCH_TOKEN_OPS = [
    {
        "op": "patch_onboarding_profile",
        "profileName": "campus-1709",
        "patch": {"cvopt_auth_token": TOKEN_PATH},
    }
]


def _call(operations, *, expected=PRE_SHA, confirm=False, token=None, ws=WORKSPACE):
    return tel.set_cvp_telemetry_inputs(
        DATADICT,
        ws,
        expected,
        copy.deepcopy(operations),
        confirm=confirm,
        preview_token_value=token,
    )


def _code(env):
    return env["object"]["error"]["code"]


def test_writes_disabled_refuses(monkeypatch):
    monkeypatch.delenv(WRITES_ENV, raising=False)
    with _mocked() as urlopen:
        env = _call(PATCH_TOKEN_OPS)
    assert _code(env) == "writes_disabled"
    assert urlopen.call_count == 0


@pytest.mark.usefixtures("_writes_on")
def test_preview_patch_token_path():
    with _mocked() as urlopen:
        env = _call(PATCH_TOKEN_OPS)
    obj = env["object"]
    assert obj["outcome"] == "preview"
    assert obj["posted_at_root"] is True
    assert obj["studio_id"] == STUDIO_ID
    assert obj["operation"] == "set_telemetry_inputs"
    assert obj["before_sha256"] == PRE_SHA
    assert obj["entries_replaced"] == ["profile:campus-1709"]
    assert obj["changed_leaf_paths"] == [
        "$.onboardingProfiles[0].dev_collection[0].cvopt_auth_token"
    ]
    assert "preview_token" in obj
    assert urlopen.call_count == 0
    after = json.loads(obj["request_body"]["inputs"])
    assert (
        after["onboardingProfiles"][0]["dev_collection"][0]["cvopt_auth_token"]
        == TOKEN_PATH
    )


@pytest.mark.usefixtures("_writes_on")
def test_confirm_posts_root_inputs():
    ops = [
        {"op": "upsert_onboarding_profile", "profile": ZTX_PROFILE},
        {
            "op": "set_profile_assignment",
            "query": "device:WTW23350044",
            "onboardingProfile": "staging-mgmt-ztx",
        },
    ]
    with _mocked() as urlopen:
        preview = _call(ops)
        token = preview["object"]["preview_token"]
        env = _call(ops, confirm=True, token=token)
    assert env["object"]["outcome"] == "accepted"
    assert urlopen.call_count == 1
    body = json.loads(urlopen.call_args[0][0].data.decode())
    assert body["key"] == {
        "studioId": STUDIO_ID,
        "workspaceId": WORKSPACE,
        "path": {"values": []},
    }
    posted = json.loads(body["inputs"])
    assert inputs_sha256(posted) == preview["object"]["after_sha256"]
    # Untouched top-level keys round-trip exactly.
    for key in set(PRE_DOC) - set(tel.WRITABLE_TOP_LEVEL_KEYS):
        assert posted[key] == PRE_DOC[key]


@pytest.mark.usefixtures("_writes_on")
def test_confirm_without_token_refuses():
    with _mocked() as urlopen:
        env = _call(PATCH_TOKEN_OPS, confirm=True)
    assert _code(env) == "preview_required"
    assert urlopen.call_count == 0


@pytest.mark.usefixtures("_writes_on")
def test_confirm_token_bound_to_operations():
    other = copy.deepcopy(PATCH_TOKEN_OPS)
    other[0]["patch"]["cvopt_auth_token"] = "/tmp/other-token"
    with _mocked() as urlopen:
        token = _call(PATCH_TOKEN_OPS)["object"]["preview_token"]
        env = _call(other, confirm=True, token=token)
    assert _code(env) == "preview_required"
    assert urlopen.call_count == 0


@pytest.mark.usefixtures("_writes_on")
def test_digest_mismatch_refuses_without_post():
    with _mocked() as urlopen:
        env = _call(PATCH_TOKEN_OPS, expected="0" * 64)
    assert _code(env) == "inputs_digest_mismatch"
    assert urlopen.call_count == 0


@pytest.mark.usefixtures("_writes_on")
@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"ws": "ws-other"}, None),
        ({"expected": "abc"}, "expected_inputs_sha256_required"),
    ],
)
def test_early_refusals_make_no_http(kwargs, code):
    with _mocked() as urlopen:
        env = _call(PATCH_TOKEN_OPS, **kwargs)
    assert env["object"]["outcome"] == "refused"
    if code:
        assert _code(env) == code
    assert urlopen.call_count == 0


@pytest.mark.usefixtures("_writes_on")
def test_invalid_operation_refuses_before_http():
    with _mocked() as urlopen:
        env = _call([{"op": "replace_document", "inputs": {}}])
    assert _code(env) == "telemetry_operation_invalid"
    assert env["object"]["error"]["details"]["studio_id"] == STUDIO_ID
    assert urlopen.call_count == 0


@pytest.mark.usefixtures("_writes_on")
def test_apply_error_surfaces_through_cas():
    with _mocked() as urlopen:
        env = _call([{"op": "remove_onboarding_profile", "profileName": "ghost"}])
    assert _code(env) == "telemetry_profile_not_found"
    assert urlopen.call_count == 0


@pytest.mark.usefixtures("_writes_on")
def test_scope_violation_if_apply_touches_custom_flags():
    def rogue(document, ops):
        after = copy.deepcopy(document)
        after["customFlags"][0]["inputs"]["custom_flags_group"]["cvgnmi"] = False
        return after, {"added": [], "replaced": [], "removed": []}, None

    with _mocked() as urlopen, patch.object(tel, "apply_operations", rogue):
        env = _call(PATCH_TOKEN_OPS)
    assert _code(env) == "tree_diff_outside_telemetry_scope"
    assert env["object"]["error"]["details"]["outside"] == [
        "$.customFlags[0].inputs.custom_flags_group.cvgnmi"
    ]
    assert urlopen.call_count == 0


# --- argv-injection guards --------------------------------------------------


@pytest.mark.parametrize(
    "patch_body, key",
    [
        ({"addr1": "cv.example.net -cvopt evil=1"}, "addr1"),
        ({"addr1": "cv.example.net,other"}, "addr1"),
        ({"cvopt_vrf": "mgmt -taillogs"}, "cvopt_vrf"),
        ({"cvopt_vrf": "a=b"}, "cvopt_vrf"),
        ({"cvopt_auth_token": "relative/token"}, "cvopt_auth_token"),
        ({"cvopt_auth_token": "/tmp/tok -x"}, "cvopt_auth_token"),
    ],
)
def test_patch_rejects_argv_injection(patch_body, key):
    _, err = tel.normalize_operations(
        [
            {
                "op": "patch_onboarding_profile",
                "profileName": "campus-1709",
                "patch": patch_body,
            }
        ]
    )
    assert err["code"] == "telemetry_operation_invalid"
    assert err["details"]["path"] == f"operations[0].patch.{key}"


@pytest.mark.parametrize(
    "mutate, suffix",
    [
        (
            lambda p: p["cluster_profiles"][0]["cvopt_cvaddr_group"].update(
                {"addr1 -x": "h"}
            ),
            "cluster_profiles[0].cvopt_cvaddr_group.addr1 -x",
        ),
        (
            lambda p: p["cluster_profiles"][0]["cvopt_cvaddr_group"].update(
                {"addr2": "h -x"}
            ),
            "cluster_profiles[0].cvopt_cvaddr_group.addr2",
        ),
        (lambda p: p.update({"customFlags": {"cvgnmi": True}}), "customFlags"),
        (
            lambda p: p.update({"select_active_cluster": {"x": "y"}}),
            "select_active_cluster",
        ),
        (
            lambda p: p["dev_collection"][0].update({"cvopt_proxy": "http://a b"}),
            "dev_collection[0].cvopt_proxy",
        ),
        (
            lambda p: p["sourceInterfaceAndVrf"].update({"source_vrf": "m gmt"}),
            "sourceInterfaceAndVrf.source_vrf",
        ),
    ],
)
def test_upsert_rejects_argv_injection(mutate, suffix):
    profile = copy.deepcopy(ZTX_PROFILE)
    mutate(profile)
    _, err = tel.normalize_operations(
        [{"op": "upsert_onboarding_profile", "profile": profile}]
    )
    assert err["code"] == "telemetry_operation_invalid"
    assert err["details"]["path"] == f"operations[0].profile.{suffix}"


def test_live_profile_passes_strict_validation():
    _, err = tel.normalize_operations(
        [
            {
                "op": "upsert_onboarding_profile",
                "profile": copy.deepcopy(PRE_DOC["onboardingProfiles"][0]),
            }
        ]
    )
    assert err is None
