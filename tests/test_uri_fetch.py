"""Tests for URI allowlist and fetch helpers."""

from unittest.mock import patch

from cvp_mcp.grpc import uri_allowlist, uri_fetch


def test_is_uri_host_allowed_cvp_host():
    assert uri_allowlist.is_uri_host_allowed(
        "https://cvp.example.com/api/config/1", "cvp.example.com"
    )


def test_is_uri_host_allowed_arista_suffix():
    assert uri_allowlist.is_uri_host_allowed("https://api.arista.io/v1/resource", None)


def test_is_uri_host_allowed_blocks_unknown():
    assert not uri_allowlist.is_uri_host_allowed(
        "https://evil.example.net/steal", "cvp.example.com"
    )


def test_fetch_uri_with_bearer_blocks_disallowed_host():
    text, err = uri_fetch.fetch_uri_with_bearer(
        "https://internal.corp/config",
        "token",
        cvp_endpoint="cvp.example.com",
    )
    assert text is None
    assert err == "uri_host_not_allowed"


def test_fetch_uri_with_bearer_allows_cvp_host():
    with patch("urllib.request.urlopen") as mock_open:
        mock_resp = mock_open.return_value.__enter__.return_value
        mock_resp.read.return_value = b"config text"
        text, err = uri_fetch.fetch_uri_with_bearer(
            "https://cvp.example.com/config/1",
            "token",
            cvp_endpoint="cvp.example.com",
        )
    assert err is None
    assert text == "config text"


def test_get_ndjson_all_values_last_wins_and_skips_blank():
    ndjson = (
        '{"result":{"value":{"key":{"studioId":"a","workspaceId":""},"displayName":"one"}}}\n'
        "\n"
        '{"result":{"value":{"key":{"studioId":"a","workspaceId":""},"displayName":"two"}}}\n'
        "not-json\n"
        '{"result":{"value":{"key":{"studioId":"b","workspaceId":""},"displayName":"b"}}}\n'
    )
    with patch("urllib.request.urlopen") as mock_open:
        mock_resp = mock_open.return_value.__enter__.return_value
        mock_resp.read.return_value = ndjson.encode()
        values, err, warnings = uri_fetch.get_ndjson_all_values_with_bearer(
            "https://cvp.example.com/api/resources/studio/v1/Studio/all",
            "token",
            cvp_endpoint="cvp.example.com",
        )
    assert err is None
    assert values is not None
    assert len(values) == 2
    assert values[0]["displayName"] == "two"
    assert values[1]["displayName"] == "b"
    assert any(w.startswith("ndjson_skip_invalid_line") for w in warnings)
