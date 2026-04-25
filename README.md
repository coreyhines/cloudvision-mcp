# MCP Server for CloudVision

This MCP server can be used to query and interact with Arista CloudVision.

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

Run
```
  podman run -d --name cvp-mcp -p 8000:8000 --env-file cvp-mcp.env cloudvision-mcp:latest
```

The server will be running by default with Streamable HTTP on port 8000

### Remote MCP clients and other agents

Connector tools (LLDP, inventory, topology map, etc.) read **`CVP`** and **`CVPTOKEN` from the environment of the MCP server process** (see `cvp_mcp/env.py`). They are **not** sent by the MCP client. If another agent, IDE, or hosted runner connects to your MCP URL but the **container or host running `cloudvision_mcp.py` lacks those variables**, calls will return empty data or warnings such as `missing_CVP`, `missing_CVPTOKEN`, and `mcp_server_missing_cloudvision_credentials`. Fix by injecting the same env-file or secrets you use locally into that deployment, and ensure the service account token can reach the CVP gRPC API from that network.

## Tools and data sources

Responses for the newer tools use a common envelope: `device_id`, `collected_at`, `data_source`, `coverage`, `warnings`, and either `items` or `object`.

| Tool | Purpose | Typical `data_source` |
| --- | --- | --- |
| `get_cvp_probe_arista_apis` | Installed `arista.*.v1` Python API bundles | local package introspection |
| `get_cvp_device_config` | Config summary URIs + optional running-config body | `resource_api:configstatus.v1`; if URIs/APIs are missing, **Connector fallback** scans Sysdb/Smash + analytics `Devices/...` paths for a running-config-like blob |
| `get_cvp_interfaces` | Interface admin/oper, speed, MTU, counters | `connector:device:Sysdb/interface` |
| `get_cvp_lldp_neighbors` | LLDP neighbor rows (`remoteSystem` and `remoteSystemByMsap` per port; `neighbor_source` on each row). Tries **literal** `portStatus/Ethernet*` paths (Telemetry Browser style) before wildcards; optional args `port_name`, `remote_neighbor_key` when Aeris shows a fixed path. Normalized rows now include rich remote metadata when present: management addresses, system description, capabilities, VLAN/PVID hints, and LLDP-MED payloads. | `connector:device:Sysdb/l2discovery/lldp` (falls back to legacy `Sysdb/lldp` paths) |
| `map_cvp_network_topology` | Full-fabric LLDP sweep over CVP inventory. Default `lldp_port_source=auto` probes LLDP only on **Sysdb oper-up** physical ports (`Ethernet*`, `Management*`, `Port-Channel*`) when Connector returns interface data (much faster than scanning every `Ethernet1..N`); if that list is empty, it falls back to the model-based `Ethernet1..N` sweep. Use `lldp_port_source=oper_up_only` to **disable fallback sweep** and skip devices with no oper-up list, or `lldp_port_source=full_range` for the legacy full sweep. Then probes additional neighbor indices (`2..8`) on ports where LLDP returned data to capture multi-neighbor adjacencies. Returns `topology` (nodes, links, edges, stats) plus `text` in `output_format` **json**, **mermaid**, **table** (markdown), or **containerlab** (YAML lab sketch — **images are placeholders**). Optional `device_serial_allowlist`, `include_inactive_devices`, `topology_name`, `topology_node_scope` (`full_inventory` vs `connected` — only nodes that have LLDP edges). Response now includes `execution_guidance` with batching best practices for agents. | `inventory+connector:lldp_topology_scan` |
| `get_cvp_vlans` | Switchport / VLAN hints | `connector:device:Sysdb/bridging` |
| `get_cvp_ip_interfaces` | L3 addressing hints | `connector:device:Sysdb/ip` |
| `get_cvp_events` | Structured CVP events (`GetAll` + filters) | `resource_api:event.v1` |
| `search_cvp_events` | Substring search over normalized event fields | `resource_api:event.v1+client_search` |
| `get_cvp_bgp_status` | BGP snapshot | `connector:device:Sysdb/routing/bgp` |
| `get_cvp_routes` | RIB-like entries | `connector:device:Sysdb/routing` |
| `get_cvp_features` | Feature-related Sysdb | `connector:device:Sysdb/feature` |
| `get_cvp_evpn` | EVPN Sysdb | `connector:device:Sysdb/evpn` |
| `get_cvp_vxlan` | VxLAN Sysdb | `connector:device:Sysdb/vxlan` |
| `get_cvp_system_health` | Version / environment / platform | `connector:device:Sysdb/sys+environment` |

Connector-based tools are best-effort: EOS paths differ by release, so `coverage` may be `partial` and `warnings` may explain empty results.

## Simple LLDP strategy for agents

Use this workflow when you need relevant and eventually complete LLDP data across mixed device models.

1. Run `get_cvp_all_inventory` and keep active EOS serials.
2. Run `get_cvp_lldp_neighbors` per device.
3. If a device returns `lldp_data_unparsed`, do **not** treat it as "no neighbors":
   - retry with `port_name` for concrete interfaces, or
   - run `map_cvp_network_topology` in **batches** (`device_serial_allowlist`, 1-5 devices).
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

### `get_cvp_lldp_neighbors` row contract (`items[]`)

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

### `map_cvp_network_topology` contract (`topology.edges[]` and `topology.links[]`)

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
| -t | MCP Transport {"http", "stdio"} (default=http) |
| -p | MCP Port for Streamable HTTP (default=8000) |
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
