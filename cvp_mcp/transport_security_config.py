"""MCP streamable HTTP transport security for reverse-proxy deployments."""

from __future__ import annotations

import os

from mcp.server.transport_security import TransportSecuritySettings

_LOCALHOST_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _host_allowlist_entries(entry: str) -> list[str]:
    """Return Host header values MCP should accept for this configured name."""
    entry = entry.strip()
    if not entry:
        return []

    if entry.endswith(":*"):
        base = entry[:-2]
        return list(dict.fromkeys([base, entry]))

    if ":" in entry and not entry.startswith("["):
        return [entry]

    return list(dict.fromkeys([entry, f"{entry}:*"]))


def build_transport_security() -> TransportSecuritySettings | None:
    """
    Configure MCP DNS rebinding protection for how this server is deployed.

    Behind Caddy (or another TLS/auth proxy), clients send the public Host header.
    The MCP SDK defaults to localhost-only hosts when FastMCP is created with
    host=127.0.0.1, which rejects ``cloudvision-mcp.example.com``.

    Set ``CLOUDVISION_MCP_PUBLIC_HOST`` (written by deploy/install.sh) or
    ``CVP_MCP_ALLOWED_HOSTS`` (comma-separated) to allow those hosts.
    Set ``CVP_MCP_DISABLE_DNS_REBINDING=1`` only when a trusted proxy handles access.
    """
    if _truthy(os.environ.get("CVP_MCP_DISABLE_DNS_REBINDING")):
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    allowed: list[str] = list(_LOCALHOST_HOSTS)

    public = (os.environ.get("CLOUDVISION_MCP_PUBLIC_HOST") or "").strip()
    if public:
        allowed.extend(_host_allowlist_entries(public))

    extra = (os.environ.get("CVP_MCP_ALLOWED_HOSTS") or "").strip()
    for entry in extra.split(","):
        allowed.extend(_host_allowlist_entries(entry))

    if public or extra:
        deduped = list(dict.fromkeys(allowed))
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=deduped,
        )

    return None
