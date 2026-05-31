# Deploying CloudVision MCP (Streamable HTTP + Caddy auth/TLS)

Centralized Podman quadlet deployment with **Caddy** as TLS terminator and **client auth proxy** (Basic Auth by default).

## Fast path

On the host (as root), from a clone:

```bash
podman login hub.example.com   # if your registry requires auth
sudo bash deploy/install.sh
```

The installer **builds** the image from `deploy/Containerfile` and **pushes** it to the registry you specify, then writes quadlets pointing at that image.

Prompts include:

- **Container registry/image** (required, e.g. `hub.example.com/cloudvision_mcp`)
- **Image tag** — auto-incremented from the registry when unset (e.g. `1.49` → `1.50`); also pushes `:latest`
- Install root (default `/opt/containerdata/cloudvision-mcp`)
- Public HTTPS hostname
- Auth mode: `basic` (default), `forward_auth`, or `none`
- Pod network / static IP (optional)

Re-run with `--skip-image` to refresh quadlets/Caddyfile only (no build/push).

Non-interactive example:

```bash
sudo env \
  CLOUDVISION_MCP_IMAGE_REPO=hub.freeblizz.com/cloudvision_mcp \
  CLOUDVISION_MCP_INSTALL_ROOT=/opt/containerdata/cloudvision-mcp \
  CLOUDVISION_MCP_PUBLIC_HOST=cloudvision-mcp.freeblizz.com \
  CLOUDVISION_MCP_AUTH_MODE=basic \
  CLOUDVISION_MCP_BASIC_AUTH_USER=mcp \
  CLOUDVISION_MCP_BASIC_AUTH_HASH='$(podman run --rm caddy:2-alpine caddy hash-password --plaintext "your-password")' \
  CLOUDVISION_MCP_NETWORK=net-10 \
  CLOUDVISION_MCP_IP=10.0.10.18 \
  bash deploy/install.sh
```

## Layout after install

| Path | Purpose |
| --- | --- |
| `$INSTALL_ROOT/environment` | `CVP`, `CVPTOKEN`, quadlet/auth settings (`chmod 600`) |
| `$INSTALL_ROOT/Caddyfile` | Rendered TLS + auth + reverse_proxy config |
| `$INSTALL_ROOT/cert.pem` | Optional on-prem CVP TLS cert (`CERT=cert.pem`) |
| `/etc/containers/systemd/cloudvision-mcp/` | Pod + app + Caddy quadlets |

## Client URL

```text
https://<CLOUDVISION_MCP_PUBLIC_HOST>/mcp
```

With Basic Auth, configure the MCP client to send the same credentials (e.g. `Authorization: Basic …`).

## Auth modes

| Mode | Caddy behavior |
| --- | --- |
| `basic` | `basicauth` before reverse_proxy (recommended default) |
| `forward_auth` | Delegates to OAuth2-proxy / Authelia at `CLOUDVISION_MCP_FORWARD_AUTH_URL` |
| `none` | TLS only — not recommended for WAN |

## Quadlet units

| File | systemd unit |
| --- | --- |
| `cloudvision-mcp.pod` | `cloudvision-mcp-pod.service` |
| `cloudvision-mcp-app.container` | `cloudvision-mcp-app.service` |
| `cloudvision-mcp-caddy.container` | `cloudvision-mcp-caddy.service` |

Requires Podman **4.7+** (quadlets in subdirectories under `/etc/containers/systemd/`).

## Uninstall

```bash
sudo bash deploy/uninstall.sh
```

Stops services and removes quadlet files; does **not** delete `$INSTALL_ROOT`.

See also [TLS.md](TLS.md).
