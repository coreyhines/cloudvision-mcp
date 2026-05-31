"""Host allowlist for CloudVision Resource API URI fetches (SSRF mitigation)."""

from __future__ import annotations

from urllib.parse import urlparse

from cvp_mcp.env import normalize_cvp_endpoint

# Known CloudVision / CVaaS API host suffixes (lowercase).
_CVP_HOST_SUFFIXES: tuple[str, ...] = (
    ".arista.io",
    ".cloudvisionportal.com",
    ".cv.arista.io",
)


def _cvp_hostname(cvp_endpoint: str | None) -> str:
    if not cvp_endpoint:
        return ""
    normalized = normalize_cvp_endpoint(cvp_endpoint)
    return normalized.split(":")[0].strip().lower()


def is_uri_host_allowed(uri: str, cvp_endpoint: str | None) -> bool:
    """
    Return True when ``uri`` targets the configured CVP host or a known CVaaS domain.

    Blocks bearer-token fetches to arbitrary third-party or internal URLs.
    """
    parsed = urlparse((uri or "").strip())
    if parsed.scheme not in ("https", "http"):
        return False
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return False

    cvp_host = _cvp_hostname(cvp_endpoint)
    if cvp_host and hostname == cvp_host:
        return True

    for suffix in _CVP_HOST_SUFFIXES:
        if hostname.endswith(suffix) or hostname == suffix.lstrip("."):
            return True

    return False
