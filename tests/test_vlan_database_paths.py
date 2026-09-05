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
