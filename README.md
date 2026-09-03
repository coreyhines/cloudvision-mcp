# MCP Server for CloudVision

This MCP server can be used to query and interact with Arista CloudVision.

## Security and deployment

### Threat model

This MCP server holds **full CloudVision service-account credentials** (`CVP`, `CVPTOKEN`) in its process environment. Any client that can invoke MCP tools receives the same access as that token (inventory, configs, routes, BGP, topology, etc.).

**Never commit tokens, private keys, or `.env` files.** Gitleaks runs in CI (`secret-scan.yml`) and as a pre-commit / pre-push hook:

```bash
uv sync --dev
pre-commit install --hook-type pre-commit --hook-type pre-push
pre-commit run --all-files
```

| Transport | Default bind | Recommended use |
| --- | --- | --- |
| **stdio** (default) | N/A | Local desktop agents (Claude Desktop, Cursor) on a trusted host |
| **streamable HTTP** | `127.0.0.1:8000` | Same host only, or behind VPN / authenticated reverse proxy |

**Do not** expose streamable HTTP on `0.0.0.0` to untrusted networks without authentication in front of it.

### HTTP hardening flags

| Flag | Default | Notes |
| --- | --- | --- |
| `-t` / `--transport` | `stdio` | Use `http` only when a remote client needs streamable HTTP |
| `--host` | `127.0.0.1` | Use `0.0.0.0` only inside a container on a trusted network, still behind auth |
| `-p` / `--port` | `8000` | Streamable HTTP port |

Example (local HTTP only):

```
uv run --env-file cvp-mcp.env cloudvision_mcp.py --transport http --host 127.0.0.1
```

### Authenticated reverse proxy (recommended for remote HTTP)

Place OAuth2, mTLS, or VPN in front of the MCP HTTP endpoint. The MCP server does not implement client authentication.

Typical patterns:

- **VPN / private network** — publish Podman/Kubernetes service on an internal VLAN only
- **mTLS** — terminate client certificates at nginx/Envoy/Caddy before proxying to `127.0.0.1:8000`
- **OAuth2 proxy** — protect `/mcp` with an identity provider; agents connect through the proxy URL

### Per-tool capability flags

Disable sensitive tools via comma-separated env var (server-side):

```
CVP_MCP_DISABLED_TOOLS=device.config,routing.routes,routing.bgp
```

List a group (for example, `routing`) to disable all of its actions, or a
`group.action` key to disable one action. Disabled tools remain in the catalog
and return `{"error": "tool_disabled", "tool": "<group or group.action>"}`.

### URI fetch allowlist

Config body fetches (`fetch_uri_with_bearer`) only allow HTTPS/HTTP URIs whose host matches the configured `CVP` endpoint or known Arista CloudVision domains (`*.arista.io`, `*.cloudvisionportal.com`). Other hosts are rejected to limit SSRF with the bearer token.

### Container notes

The Dockerfile runs as non-root user `cvpmcp` and starts HTTP on `0.0.0.0` **inside the container** so port mapping works. Treat published ports like any other sensitive service: internal networks + reverse proxy auth for WAN access.

```
podman run -d --name cvp-mcp -p 127.0.0.1:8000:8000 --env-file cvp-mcp.env cloudvision-mcp:latest
```

Binding the host side to `127.0.0.1` prevents LAN-wide exposure when using `-p`.

### Podman quadlet install (production)

For hosts using systemd + Podman quadlets (Caddy TLS + Basic Auth front), see [`deploy/README.md`](deploy/README.md):

```bash
sudo bash deploy/install.sh
```

Quadlets install to `/etc/containers/systemd/cloudvision-mcp/`. Default data root is `/opt/containerdata/cloudvision-mcp` (prompted on install).

## Usage

To run, you can the server via `uv`. Make sure you load your environment variables for `CVP` and `CVPTOKEN` prior to running.

```
  uv run --env-file cvp-mcp.env cloudvision_mcp.py
```

### Alternate Method

To run in a container, build the image first.
```
  podman build -t cloudvision_mcp:latest .
```

Populate an env-file, sample below.

`cvp-mcp.env`
```
  CVP=<cvp_server_address>
  CVPTOKEN=<service_account_api_token>
  CERT=<cert_file_name>
```
**Note** The Cert file is only necessary if you are connecting to an on-prem CVP instance with self-signed certs

Run (HTTP on localhost only — see Security section for remote access):

```
  podman run -d --name cvp-mcp -p 127.0.0.1:8000:8000 --env-file cvp-mcp.env cloudvision-mcp:latest
```

The container serves streamable HTTP on port 8000 (bound inside the container; map only on trusted hosts).

### Remote MCP clients and other agents

Connector tools (LLDP, inventory, topology map, etc.) read **`CVP`** and **`CVPTOKEN` from the environment of the MCP server process** (see `cvp_mcp/env.py`). They are **not** sent by the MCP client. If another agent, IDE, or hosted runner connects to your MCP URL but the **container or host running `cloudvision_mcp.py` lacks those variables**, calls will return empty data or warnings such as `missing_CVP`, `missing_CVPTOKEN`, and `mcp_server_missing_cloudvision_credentials`. Fix by injecting the same env-file or secrets you use locally into that deployment, and ensure the service account token can reach the CVP gRPC API from that network.

## Tools and data sources

Responses for the newer tools use a common envelope: `device_id`, `collected_at`, `data_source`, `coverage`, `warnings`, and either `items` or `object`.

Every MCP tool takes a required `action`; use `action=help` to inspect that
group's parameters and defaults.

| Group tool | Actions |
| --- | --- |
| `inventory` | `get`, `list`, `search` |
| `endpoints` | `get`, `list`, `filter` |
| `device` | `config`, `interfaces`, `vlans`, `ip_interfaces`, `features`, `health` |
| `overlay` | `evpn`, `vxlan` |
| `routing` | `bgp`, `routes` |
| `topology` | `lldp`, `map` |
| `events` | `list`, `search` |
| `flow` | `get` |
| `probes` | `list`, `get` |
| `compliance` | `bugs`, `lifecycle`, `designed_config`, `config_status`, `image_status` |
| `meta` | `probe_apis` |
| `studios` | `list`, `get`, `inputs`, `search_templates`, `list_workspaces`, `get_workspace`, `get_build`, `tags` |

| `group.action` | Purpose | Typical `data_source` |
| --- | --- | --- |
| `meta.probe_apis` | Installed `arista.*.v1` Python API bundles | local package introspection |
| `device.config` | Config summary URIs + optional running-config body | `resource_api:configstatus.v1`; Connector fallback scans Sysdb/Smash and analytics paths |
| `device.interfaces` | Interface admin/oper, speed, MTU, counters | `connector:device:Sysdb/interface` |
| `topology.lldp` | LLDP neighbor rows, including rich remote metadata when present | `connector:device:Sysdb/l2discovery/lldp` |
| `topology.map` | Bounded full-fabric LLDP sweep with JSON, Mermaid, table, or containerlab output | `inventory+connector:lldp_topology_scan` |
| `device.vlans` / `device.ip_interfaces` | VLAN/switchport and L3 addressing hints | `connector:device:Sysdb/bridging` / `connector:device:Sysdb/ip` |
| `events.list` / `events.search` | Structured events or client-side substring search | `resource_api:event.v1` |
| `routing.bgp` / `routing.routes` | BGP snapshot or RIB-like entries | `connector:device:Sysdb/routing` |
| `device.features` / `overlay.evpn` / `overlay.vxlan` / `device.health` | Feature, overlay, and system state | Connector Sysdb |
| `endpoints.get` | Single endpoint lookup by IP, MAC, or hostname | `resource_api:endpointlocation.v1` |
| `endpoints.list` / `endpoints.filter` | LLDP-seeded bulk endpoint lookup, optionally filtered | inventory + LLDP + EndpointLocation |
| `compliance.designed_config` | Designed CLI and studio provenance | `service_api:compliancecheck.getconfig` |
| `compliance.config_status` / `compliance.image_status` | Product compliance status; may be unavailable on some tenants | configstatus / imagestatus Resource APIs |

Connector-based tools are best-effort: EOS paths differ by release, so `coverage` may be `partial` and `warnings` may explain empty results.

### Studios write tools

Registered only when `CLOUDVISION_MCP_ALLOW_WRITES=1` is set before the process starts. Every one is a **dry-run** unless called with `confirm=True` and the `preview_token` from a matching dry-run; every one refuses the mainline workspace and only drafts `ws-mcp-*` workspaces. **None of them can submit a workspace, approve or execute a change control** — the operator reviews the built workspace in the CloudVision UI and submits there. Spec: [`docs/studios-phase2-spec.md`](docs/studios-phase2-spec.md) and [`docs/studios-phase2-final-spec.md`](docs/studios-phase2-final-spec.md).

| Action | Purpose |
| --- | --- |
| `studios_write.create_workspace` / `studios_write.delete_workspace` | Draft workspace lifecycle (delete only while `WORKSPACE_STATE_PENDING`) |
| `studios_write.build` | `REQUEST_START_BUILD`; poll with `studios.get_workspace` / `studios.get_build` |
| `studios_write.set_description` | Compare-and-set one port description in `studio-campus-access-interfaces` |
| `studios_write.set_inputs` | Generic path-scoped Inputs write, description-only leaf allowlist, never the root path |
| `studios_write.assign_tags` | Replace a studio's tag query with `expected_current_query` CAS |
| `studios_write.create_studio` / `studios_write.delete_studio` | Studio upsert / remove with EOS lint on templates |
| `studios_write.set_mss_inputs` | Compare-and-set MSS Service policy inputs; CAS on `inputs_sha256` from `studios.inputs` |

Claude Code in auto mode can deny MCP calls without a prompt. Allowlist the
group tools in `permissions.allow`, for example:
`mcp__cloudvision-mcp__inventory`, `mcp__cloudvision-mcp__studios`,
`mcp__cloudvision-mcp__studios_write`, and
`mcp__cloudvision-mcp__compliance`. The write group's dry-run /
`preview_token` gate still applies, and no action can submit.

### Endpoint locations

CloudVision rejects bulk `GetAll` on EndpointLocation (gRPC UNIMPLEMENTED). The bulk and filtered tools above **do not** call `GetAll`; they seed search keys from LLDP on active EOS switches, then resolve each key via **`GetSome`** (falling back to batched **`GetOne`** when needed).

**Coverage limits:** only hosts that appear as LLDP neighbors (or otherwise show up in LLDP Sysdb) are discoverable. Silent DHCP/Wi‑Fi clients without LLDP are excluded — use DHCP/OPNsense sources for those. LLDP sweeps follow the same bounded oper-up physical-port pattern as topology (active EOS only).

**Single-location conversion:** `convert_response_to_endpoint_location` currently keeps only the **first** attachment location per endpoint. Filtered lookups (`device_id`, `interface`, `vlan_id`) match against that first location only — endpoints with multiple switch attachments may be missed when the filter targets a secondary attachment.

**Response fields (bulk/filtered):** in addition to `devices` and `endpoints`, responses include:

- `seed_stats` — e.g. `switches_scanned`, `lldp_neighbor_rows`, `unique_search_keys`, `getsome_hits`, `getsome_misses`, `lookup_method`
- `warnings` — partial LLDP, no keys seeded, GetSome fallback, lookup misses, etc.

Hard failures (missing credentials, device not found for filter) return `error` plus `warnings` instead of a silent empty success.

## Simple LLDP strategy for agents

Use this workflow when you need relevant and eventually complete LLDP data across mixed device models.

1. Run `inventory.list` and keep active EOS serials.
2. Run `topology.lldp` per device.
3. If a device returns `lldp_data_unparsed`, do **not** treat it as "no neighbors":
   - retry with `port_name` for concrete interfaces, or
   - run `topology.map` in **batches** (`device_serial_allowlist`, 1-5 devices).
4. Keep batch calls bounded with `max_ethernet_ports` to avoid long sweeps.
5. Merge results across batches and dedupe links using:
   - `local_serial + local_port + (remote_eth_addr or remote_chassis_id)`.

Why this works:
- Some models return wildcard LLDP snapshots that are present but not directly parseable.
- Per-port probes and topology sweeps use concrete interface paths, which consistently recover neighbor edges.
- Batching avoids unstable long-running full-fabric calls and gives repeatable, additive results.

## Agent-facing LLDP field contract

The LLDP tools are additive and best-effort, but agents can rely on this contract:

- Existing keys remain stable.
- New keys are additive (never replace existing keys).
- Missing LLDP TLVs are represented by omitted keys or empty values (not errors).

### `topology.lldp` row contract (`items[]`)

Common/stable keys:

- `local_interface` (string)
- `neighbor_key` (string)
- `neighbor_source` (string; e.g. `remoteSystem`, `remoteSystemByMsap`, `remoteLeaf`)
- `system_name` (string, when present)
- `remote_port_id` (string, when present)
- `remote_chassis_id` (string, when present)
- `eth_addr` (string, when present)

Additional rich keys (when present):

- `management_address` (string)
- `management_addresses` (array of strings)
- `system_description` (string)
- `system_capabilities` (array of strings)
- `enabled_system_capabilities` (array of strings)
- `pvid` (string)
- `vlans` (array of strings)
- `lldp_med` (object; LLDP-MED payloads normalized to arrays of strings)

Example `items[]` row:

```json
{
  "local_interface": "Ethernet5",
  "neighbor_key": "1",
  "neighbor_source": "remoteSystem",
  "system_name": "downstream-720xp-48",
  "remote_port_id": "Ethernet48",
  "remote_chassis_id": "ec:8a:48:04:30:c0",
  "eth_addr": "ec:8a:48:04:30:c0",
  "management_address": "10.20.30.40",
  "management_addresses": ["10.20.30.40"],
  "system_description": "Arista EOS switch",
  "system_capabilities": ["bridge", "router"],
  "enabled_system_capabilities": ["bridge"],
  "pvid": "120",
  "vlans": ["120", "121"],
  "lldp_med": {
    "lldpMedPolicy": ["voice", "121"]
  }
}
```

### `topology.map` contract (`topology.edges[]` and `topology.links[]`)

`topology.edges[]` includes local/remote adjacency identity plus rich remote metadata:

- Identity keys: `local_serial`, `local_hostname`, `local_model`, `local_port`, `remote_system_name`, `remote_chassis_id`, `remote_eth_addr`, `remote_port_id`, `neighbor_source`
- Rich remote keys: `remote_management_address`, `remote_management_addresses`, `remote_system_description`, `remote_system_capabilities`, `remote_enabled_system_capabilities`, `remote_pvid`, `remote_vlans`, `remote_lldp_med`

`topology.links[]` includes graph linkage keys plus the same rich remote keys for deterministic downstream consumption.

Example `topology.edges[]` row:

```json
{
  "local_serial": "JPE12345678",
  "local_hostname": "core-710p",
  "local_model": "CCS-710P-16P",
  "local_port": "Ethernet5",
  "remote_system_name": "downstream-720xp-48",
  "remote_chassis_id": "ec:8a:48:04:30:c0",
  "remote_eth_addr": "ec:8a:48:04:30:c0",
  "remote_port_id": "Ethernet48",
  "remote_management_address": "10.20.30.40",
  "remote_management_addresses": ["10.20.30.40"],
  "remote_system_description": "Arista EOS switch",
  "remote_system_capabilities": ["bridge", "router"],
  "remote_enabled_system_capabilities": ["bridge"],
  "remote_pvid": "120",
  "remote_vlans": ["120", "121"],
  "remote_lldp_med": {
    "lldpMedPolicy": ["voice", "121"]
  },
  "neighbor_source": "remoteSystem"
}
```

## Server Options

The server can be configured with the following flags
| Flag | Description |
| --- | --- |
| -t | MCP Transport {"http", "stdio"} (default=stdio) |
| -p | MCP Port for Streamable HTTP (default=8000) |
| --host | Bind address for HTTP (default=127.0.0.1) |
| -c | CVP Connection protocol {"grcp", "http"} (default=grpc) |
| -d | Enable debug logging |

### **Note**

For gRPC connections, a trusted cert mut be running on CloudVision. Otherwise, you will need to have a copy of the self-signed cert in the project directory before building the container image. The cert file should be named `cert.pem`

## Client Configurations

The example client configs can work with Claude Desktop or a local Ollama LLM via (https://github.com/jonigl/mcp-client-for-ollama) project.

### STDIO MCP Server Configuration
```
  {
    "mcpServers": {
      "CVP MCP Server": {
        "command": "uv",
        "args": [
          "run",
          "--directory",
          "<path_to_project_directory>",
          "./cloudvision_mcp.py"
        ]
      }
    }
  }
```

### Streamable HTTP Server Configuration
```
  {
    "mcpServers": {
      "CVP MCP Server": {
        "type": "streamable_http",
        "url": "<mcp_server_address>:<port>/mcp"
      }
    }
  }

```

### Streamable HTTP Server Configuration (Claude Desktop)
```
  {
    "mcpServers": {
      "CVP MCP Server": {
        "command": "npx",
        "args": [
          "mcp-remote",
          "http://<mcp_server_address>:<port>/mcp",
          "--allow-http"
        ]
      }
    }
  }

```
