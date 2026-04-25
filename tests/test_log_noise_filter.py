"""Tests for log noise suppression in Streamable HTTP mode."""

from __future__ import annotations

import logging

from cloudvision_mcp import _is_noise_record


def _record(name: str, message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_filters_client_disconnect_noise() -> None:
    rec = _record("mcp.server.streamable_http", "Error handling POST request")
    assert _is_noise_record(rec) is True


def test_filters_known_404_probe_noise() -> None:
    rec = _record(
        "uvicorn.access",
        'INFO: 127.0.0.1:50000 - "GET /.well-known/oauth-protected-resource HTTP/1.1" 404 Not Found',
    )
    assert _is_noise_record(rec) is True


def test_keeps_real_error_logs() -> None:
    rec = _record(
        "cvp_mcp.grpc.lldp", "lldp connector: device=SN1 path failed: timeout"
    )
    assert _is_noise_record(rec) is False
