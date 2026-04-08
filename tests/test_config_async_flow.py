from __future__ import annotations

from cvp_mcp.grpc.config_async_flow import _decode_json_maybe_multi


def test_decode_json_maybe_multi_single_document():
    obj = _decode_json_maybe_multi('{"config":"abc"}')
    assert isinstance(obj, dict)
    assert obj["config"] == "abc"


def test_decode_json_maybe_multi_concatenated_documents():
    obj = _decode_json_maybe_multi('{"a":1}{"config":"running"}')
    assert isinstance(obj, list)
    assert obj[0]["a"] == 1
    assert obj[1]["config"] == "running"


def test_decode_json_maybe_multi_ndjson_lines():
    obj = _decode_json_maybe_multi('{"x":1}\n{"config":"cfg"}\n')
    assert isinstance(obj, list)
    assert obj[1]["config"] == "cfg"
