"""Unit tests for the shared Inputs digest. No HTTP."""

import json

import pytest

from cvp_mcp.grpc.inputs_digest import canonical_json, inputs_sha256


def test_digest_ignores_key_order_and_wire_whitespace():
    a = json.loads('{"rules": [{"name": "r1", "action": "drop"}], "policies": []}')
    b = json.loads(
        '{ "policies" : [ ],\n "rules" : [ { "action":"drop","name":"r1" } ] }'
    )
    assert inputs_sha256(a) == inputs_sha256(b)
    assert len(inputs_sha256(a)) == 64


def test_digest_changes_with_content():
    a = {"rules": [{"name": "r1"}]}
    b = {"rules": [{"name": "r2"}]}
    assert inputs_sha256(a) != inputs_sha256(b)


def test_list_order_is_significant():
    a = {"policyRules": ["a", "b"]}
    b = {"policyRules": ["b", "a"]}
    assert inputs_sha256(a) != inputs_sha256(b)


@pytest.mark.parametrize("value", ["{}", None, 3, True, "not json"])
def test_non_container_has_no_digest(value):
    assert inputs_sha256(value) is None


def test_canonical_json_is_compact_and_sorted():
    assert canonical_json({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'
