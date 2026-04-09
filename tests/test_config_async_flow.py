from __future__ import annotations

import asyncio

from cvp_mcp.grpc import config_async_flow as caf
from cvp_mcp.grpc.config_async_flow import (
    _decode_json_maybe_multi,
    _rfc3339_utc_from_ns,
)


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


def test_decode_json_maybe_multi_with_xssi_prefix():
    obj = _decode_json_maybe_multi(')]}\'\n{"config":"cfg"}\n')
    assert isinstance(obj, dict)
    assert obj["config"] == "cfg"


def test_get_config_single_payload_path():
    class _Resp:
        def __init__(self, status: int, body: str):
            self.status = status
            self._body = body

        async def text(self):
            return self._body

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Sess:
        def __init__(self):
            self.calls = 0

        def post(self, url, json, timeout=None):  # noqa: A002
            self.calls += 1
            return _Resp(200, '[{"config":"!\\nhostname x\\n"}]')

    sess = _Sess()
    cfg, err = asyncio.run(caf.get_config(sess, "https://x", "dev1", 1))
    assert err is None
    assert isinstance(cfg, str)
    assert "hostname x" in cfg
    assert sess.calls == 1


def test_rfc3339_utc_from_ns():
    assert _rfc3339_utc_from_ns(0) == "1970-01-01T00:00:00Z"
    assert _rfc3339_utc_from_ns(1) == "1970-01-01T00:00:00.000000001Z"


def test_serial_from_inventory_node_camel_case_and_nested_key():
    assert caf._serial_from_inventory_node({"serialNumber": "ABC123"}) == "ABC123"
    assert caf._serial_from_inventory_node({"key": {"device_id": "XYZ789"}}) == "XYZ789"


def test_inventory_target_matches_fqdn_short_hostname():
    assert caf._inventory_target_matches("720xp-48", "", "720xp-48.lab.example.com")
    assert caf._inventory_target_matches("abc", "ABC", "")
