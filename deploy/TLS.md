# TLS for CloudVision MCP (quadlet + Caddy)

Streamable HTTP from FastMCP is **plain HTTP** on port 8000 inside the pod. Production access uses **HTTPS on 443** via Caddy in the same pod, plus **client authentication** (Basic Auth or `forward_auth`).

## Host certificate layout

Wildcard PEM files on the host (default):

**`/opt/containerdata/certs/wild/`**

| File | Role |
| --- | --- |
| `fullchain.pem` | Server certificate + intermediates |
| `privkey.pem` | Private key |

Mounted read-only into the Caddy container at `/opt/certs/wild`.

Override with `CLOUDVISION_MCP_TLS_CERTS` at install time.

## DNS

Point your public hostname at the pod IP (macvlan / static pod IP):

| Name | Type | Value |
| --- | --- | --- |
| `cloudvision-mcp.example.com` | A / AAAA | Pod IP (e.g. `10.0.10.18`) |

Set `CLOUDVISION_MCP_PUBLIC_HOST` to this FQDN during install.

## MCP client endpoint

```text
https://<CLOUDVISION_MCP_PUBLIC_HOST>/mcp
```

Trust depends on your certificate (wildcard SAN or public CA).

## Basic Auth

Install with `CLOUDVISION_MCP_AUTH_MODE=basic`. Caddy validates credentials before proxying to `127.0.0.1:8000`.

Generate a password hash without the full installer:

```bash
podman run --rm docker.io/library/caddy:2-alpine caddy hash-password --plaintext 'your-password'
```

Store the hash in `environment` as `CLOUDVISION_MCP_BASIC_AUTH_HASH` and re-run `deploy/install.sh` to refresh the Caddyfile.

## forward_auth (OAuth2-proxy / Authelia)

Set `CLOUDVISION_MCP_AUTH_MODE=forward_auth` and `CLOUDVISION_MCP_FORWARD_AUTH_URL` to your auth service verify endpoint. Run that auth service on the pod network or localhost if co-located.

## Operations

- Rotate TLS: replace PEMs under the cert directory, then `systemctl restart cloudvision-mcp-caddy.service`
- Change auth: update `environment` + re-run `deploy/install.sh`
- Do not expose port 8000 outside the pod; only 443 (and 80 redirect) should face clients
