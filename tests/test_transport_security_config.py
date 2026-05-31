"""Tests for MCP transport security behind reverse proxies."""

from cvp_mcp.transport_security_config import build_transport_security


def test_build_transport_security_allows_public_host(monkeypatch):
    monkeypatch.delenv("CVP_MCP_DISABLE_DNS_REBINDING", raising=False)
    monkeypatch.setenv("CLOUDVISION_MCP_PUBLIC_HOST", "cloudvision-mcp.freeblizz.com")
    settings = build_transport_security()
    assert settings is not None
    assert settings.enable_dns_rebinding_protection is True
    assert "cloudvision-mcp.freeblizz.com" in settings.allowed_hosts
    assert "cloudvision-mcp.freeblizz.com:*" in settings.allowed_hosts
    assert "127.0.0.1:*" in settings.allowed_hosts


def test_build_transport_security_disable_via_env(monkeypatch):
    monkeypatch.setenv("CVP_MCP_DISABLE_DNS_REBINDING", "1")
    settings = build_transport_security()
    assert settings is not None
    assert settings.enable_dns_rebinding_protection is False


def test_build_transport_security_default_none_without_public_host(monkeypatch):
    monkeypatch.delenv("CVP_MCP_DISABLE_DNS_REBINDING", raising=False)
    monkeypatch.delenv("CLOUDVISION_MCP_PUBLIC_HOST", raising=False)
    monkeypatch.delenv("CVP_MCP_ALLOWED_HOSTS", raising=False)
    assert build_transport_security() is None
