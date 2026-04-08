from __future__ import annotations

import asyncio

from cvp_mcp.grpc import config_async_flow as caf
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


def test_decode_json_maybe_multi_with_xssi_prefix():
    obj = _decode_json_maybe_multi(')]}\'\n{"config":"cfg"}\n')
    assert isinstance(obj, dict)
    assert obj["config"] == "cfg"


def test_get_config_payload_probe_sequence():
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

        def post(self, url, json):  # noqa: A002
            self.calls += 1
            if self.calls == 1:
                return _Resp(400, "bad payload 1")
            if self.calls == 2:
                return _Resp(200, '[{"config":"!\\nhostname x\\n"}]')
            return _Resp(400, "unexpected")

    cfg, err = asyncio.run(caf.get_config(_Sess(), "https://x", "dev1", 1))
    assert err is None
    assert isinstance(cfg, str)
    assert "hostname x" in cfg
