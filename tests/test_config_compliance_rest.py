from __future__ import annotations

from cvp_mcp.grpc import config


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
    seen = {"bodies": []}

    def fake_post_raw(uri, body, bearer_token, **kwargs):
        seen["bodies"].append(body)
        return text, None

    monkeypatch.setattr(config, "post_raw_with_bearer", fake_post_raw)
    datadict = {"cvp": "cvp.example.com:443", "cvtoken": "token", "cert": None}
    out, err = config._fetch_running_config_from_compliance_rest(
        datadict, ["HBG254804R6"]
    )
    assert err is None
    assert out == text
    assert seen["bodies"]
    assert "HBG254804R6" in seen["bodies"][0]


def test_inventory_lookup_device_by_hostname(monkeypatch):
    payload = {
        "value": [
            {
                "key": {"device_id": {"value": "HBG254804R6"}},
                "hostname": {"value": "720xp-48"},
            }
        ]
    }

    def fake_get_json(uri, bearer_token, **kwargs):
        return payload, None

    monkeypatch.setattr(config, "get_json_with_bearer", fake_get_json)
    datadict = {"cvp": "cvp.example.com:443", "cvtoken": "token", "cert": None}
    facts, err = config._inventory_lookup_device(datadict, "720xp-48")
    assert err is None
    assert facts["serial_number"] == "HBG254804R6"
    assert facts["hostname"] == "720xp-48"


def test_inventory_lookup_device_by_serial(monkeypatch):
    payload = {
        "value": [
            {
                "device_id": "HBG254804R6",
                "hostname": "720xp-48",
            }
        ]
    }

    def fake_get_json(uri, bearer_token, **kwargs):
        return payload, None

    monkeypatch.setattr(config, "get_json_with_bearer", fake_get_json)
    datadict = {"cvp": "cvp.example.com:443", "cvtoken": "token", "cert": None}
    facts, err = config._inventory_lookup_device(datadict, "HBG254804R6")
    assert err is None
    assert facts["serial_number"] == "HBG254804R6"
    assert facts["hostname"] == "720xp-48"
