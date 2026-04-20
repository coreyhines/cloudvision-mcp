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
| `get_cvp_lldp_neighbors` | LLDP neighbor rows (`remoteSystem` and `remoteSystemByMsap` per port; `neighbor_source` on each row). Tries **literal** `portStatus/Ethernet*` paths (Telemetry Browser style) before wildcards; optional args `port_name`, `remote_neighbor_key` when Aeris shows a fixed path. | `connector:device:Sysdb/l2discovery/lldp` (falls back to legacy `Sysdb/lldp` paths) |
| `map_cvp_network_topology` | Full-fabric LLDP sweep over CVP inventory. Default `lldp_port_source=auto` probes LLDP only on **Sysdb oper-up** physical ports (`Ethernet*`, `Management*`) when Connector returns interface data (much faster than scanning every `Ethernet1..N`); if that list is empty, it falls back to the model-based `Ethernet1..N` sweep. Use `lldp_port_source=oper_up_only` to **disable fallback sweep** and skip devices with no oper-up list, or `lldp_port_source=full_range` for the legacy full sweep. Then **neighbor indices 2 and 3** on ports where LLDP returned data (extra adjacencies). Returns `topology` (nodes, links, edges, stats) plus `text` in `output_format` **json**, **mermaid**, **table** (markdown), or **containerlab** (YAML lab sketch — **images are placeholders**). Optional `device_serial_allowlist`, `include_inactive_devices`, `topology_name`, `topology_node_scope` (`full_inventory` vs `connected` — only nodes that have LLDP edges). Response now includes `execution_guidance` with batching best practices for agents. | `inventory+connector:lldp_topology_scan` |
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
