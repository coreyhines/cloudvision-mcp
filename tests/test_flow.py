"""Tests for Clover flow record parsing."""

from cvp_mcp.grpc import flow


def test_safe_int_non_numeric_returns_default():
    assert flow._safe_int("not-a-number", default=0) == 0
    assert flow._safe_int(None, default=7) == 7


def test_clover_record_parses_malformed_ports():
    header = {"srcIP": "1.1.1.1", "dstIP": "2.2.2.2", "srcPort": "bad", "dstPort": ""}
    stat = {"bytes": "x", "packets": None}
    rec = flow._clover_record("0", header, stat)
    assert rec["src_port"] == 0
    assert rec["dst_port"] == 0
    assert rec["bytes_count"] == 0
    assert rec["packet_count"] == 0


def test_clover_record_parses_numeric_ports_and_stats():
    header = {
        "srcIP": "10.0.0.1",
        "dstIP": "10.0.0.2",
        "srcPort": 443,
        "dstPort": 52001,
    }
    stat = {"bytes": 100, "packets": 5, "nodes": [{"device": "SN1"}]}
    rec = flow._clover_record("0", header, stat)
    assert rec["src_port"] == 443
    assert rec["bytes_count"] == 100
    assert rec["device_id"] == "SN1"
