from __future__ import annotations

import asyncio

from cvp_mcp.grpc import config


def test_run_async_in_sync_context_with_running_loop():
    async def inner():
        return 42

    async def outer():
        return config._run_async_in_sync_context(inner())

    assert asyncio.run(outer()) == 42


def test_cvp_https_base_normalizes_host_and_scheme():
    assert config._cvp_https_base("10.0.0.1:443") == "https://10.0.0.1"
    assert (
        config._cvp_https_base("https://cvp.example.com") == "https://cvp.example.com"
    )
    assert (
        config._cvp_https_base("http://cvp.example.com:443/path")
        == "https://cvp.example.com"
    )


def test_extract_running_config_text_finds_nested_value():
    text = "!\nhostname 720xp-48\ninterface Ethernet1\n" + ("x" * 60)
    payload = {"value": {"config": {"value": text}}}
    assert config._extract_running_config_text(payload) == text


def test_fetch_running_config_from_compliance_rest(monkeypatch):
    text = "!\nhostname 720xp-48\nip routing\n" + ("y" * 60)
    seen = {"calls": 0}

    async def fake_get_config(session, url, device, timestamp):
        seen["calls"] += 1
        assert device == "HBG254804R6"
        assert isinstance(timestamp, int)
        return text

    monkeypatch.setattr(config, "async_get_config", fake_get_config)
    datadict = {"cvp": "cvp.example.com:443", "cvtoken": "token", "cert": None}
    out, err = config._fetch_running_config_from_compliance_rest(
        datadict, ["HBG254804R6"]
    )
    assert err is None
    assert out == text
    assert seen["calls"] == 1


def test_inventory_lookup_device_by_hostname(monkeypatch):
    async def fake_resolve_device_facts(session, inventory_url, target):
        assert target == "720xp-48"
        return {"serial_number": "HBG254804R6", "hostname": "720xp-48"}

    monkeypatch.setattr(config, "resolve_device_facts", fake_resolve_device_facts)
    datadict = {"cvp": "cvp.example.com:443", "cvtoken": "token", "cert": None}
    facts, err = config._inventory_lookup_device(datadict, "720xp-48")
    assert err is None
    assert facts["serial_number"] == "HBG254804R6"
    assert facts["hostname"] == "720xp-48"


def test_inventory_lookup_device_by_serial(monkeypatch):
    async def fake_resolve_device_facts(session, inventory_url, target):
        assert target == "HBG254804R6"
        return {"serial_number": "HBG254804R6", "hostname": "720xp-48"}

    monkeypatch.setattr(config, "resolve_device_facts", fake_resolve_device_facts)
    datadict = {"cvp": "cvp.example.com:443", "cvtoken": "token", "cert": None}
    facts, err = config._inventory_lookup_device(datadict, "HBG254804R6")
    assert err is None
    assert facts["serial_number"] == "HBG254804R6"
    assert facts["hostname"] == "720xp-48"
