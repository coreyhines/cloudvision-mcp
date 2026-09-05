"""VLAN database lives under vlan/status/vlanStatus, not vlan/*.

Probing 720xp-48: Sysdb/bridging/vlan has a single child "status", so a
wildcard there yielded one bogus row with vlan_id "status". The real ids
(1,2,3,4,5,6,7,10,81) sit under vlan/status/vlanStatus.
"""

from cloudvision.Connector.codec import Wildcard

from cvp_mcp.grpc import interfaces


def test_vlan_database_paths_target_vlanstatus():
    paths = interfaces.vlan_database_paths("SN1")
    rendered = ["/".join(interfaces.render_path(p)) for p in paths]
    assert any(r.endswith("Sysdb/bridging/vlan/status/vlanStatus/*") for r in rendered)
    # The shallow path produced the bogus "status" row.
    assert not any(r.endswith("Sysdb/bridging/vlan/*") for r in rendered)


def test_render_path_shows_wildcards_as_star_not_object_repr():
    rendered = interfaces.render_path(["Sysdb", "bridging", Wildcard()])
    assert rendered == ["Sysdb", "bridging", "*"]
    assert "cloudvision.Connector" not in "/".join(rendered)


from cvp_mcp.grpc.sysdb_parse import parse_vlan_database  # noqa: E402


def test_vlan_database_paths_include_config_tree_for_names():
    """status/vlanStatus carries only a numeric name; configuredName is in config."""
    rendered = [
        "/".join(interfaces.render_path(p))
        for p in interfaces.vlan_database_paths("SN1")
    ]
    assert any(r.endswith("Sysdb/bridging/config/vlanConfig/*") for r in rendered)


def test_parse_prefers_configured_name_over_numeric_name():
    rows = parse_vlan_database(
        {
            "2": {
                "name": "2",
                "configuredName": "wired-lan",
                "adminState": {"Name": "active", "Value": 1},
            }
        }
    )
    assert rows[0]["vlan_id"] == "2"
    assert rows[0]["name"] == "wired-lan"
    assert rows[0]["status"] == "active"


def test_parse_falls_back_to_name_when_no_configured_name():
    rows = parse_vlan_database({"7": {"name": "camera"}})
    assert rows[0]["name"] == "camera"


def test_parse_does_not_report_the_id_as_the_name():
    """name == vlan_id is the symptom that started this; treat it as unnamed."""
    rows = parse_vlan_database({"10": {"name": "10"}})
    assert rows[0]["name"] == ""
