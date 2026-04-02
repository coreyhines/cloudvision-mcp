"""
Resolve hostname/FQDN strings to IP before CVP gRPC calls when the probe key
or endpoint search expects an IP (mirrors OPNsense MCP resolve-then-query flow).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Final

_MAC_PARTS: Final = 6


def _looks_like_mac(value: str) -> bool:
    q = value.strip().lower().replace("-", ":")
    parts = q.split(":")
    if len(parts) != _MAC_PARTS:
        return False
    try:
        return all(len(p) == 2 and 0 <= int(p, 16) <= 255 for p in parts)
    except ValueError:
        return False


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return False


def resolve_endpoint_query(value: str) -> str:
    """
    If ``value`` is not already an IP or MAC, resolve DNS and return the first
    IPv4 (else first IPv6). On failure or ambiguous input, return ``value`` unchanged.
    """
    raw = (value or "").strip()
    if not raw or _is_ip(raw) or _looks_like_mac(raw):
        return raw

    try:
        infos = socket.getaddrinfo(raw, None, type=socket.SOCK_STREAM)
    except socket.gaierror as err:
        logging.warning("DNS resolution skipped for %r: %s", raw, err)
        return raw

    v4: list[str] = []
    v6: list[str] = []
    for _fam, _type, _proto, _canon, sockaddr in infos:
        if not sockaddr:
            continue
        ip_str = sockaddr[0]
        if _is_ip(ip_str):
            addr = ipaddress.ip_address(ip_str)
            if isinstance(addr, ipaddress.IPv4Address):
                v4.append(ip_str)
            else:
                v6.append(ip_str)

    if v4:
        chosen = v4[0]
        logging.info("Resolved endpoint query %r -> %s (A)", raw, chosen)
        return chosen
    if v6:
        chosen = v6[0]
        logging.info("Resolved endpoint query %r -> %s (AAAA)", raw, chosen)
        return chosen

    return raw
