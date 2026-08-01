# Design: LLDP-seeded EndpointLocation (replace GetAll)

**Date:** 2026-07-28  
**Status:** Approved — implementation plan at `docs/superpowers/plans/2026-07-31-lldp-seeded-endpoint-locations.md` (not yet implemented)  
**Repo:** `cloudvision-mcp`

## Problem

CloudVision rejects bulk EndpointLocation enumeration:

```text
GetAll of EndpointLocation is not allowed
grpc_status: 12 (UNIMPLEMENTED)
```

MCP tools that call `GetAll` (`get_cvp_all_endpoint_locations`, `get_cvp_endpoint_locations_filtered`) catch the error and return `{devices: {}, endpoints: []}`, which looks like “no endpoints” rather than an API restriction.

`GetOne` with a search term (IP, MAC, or hostname) still works.

## Goals

- Restore useful bulk/filtered endpoint location tools without using `GetAll` / `Subscribe` / `GetAllBatched` on EndpointLocation.
- Seed search keys **server-side** from existing CloudVision MCP data (LLDP), then resolve via `GetSome` (with `GetOne` fallback).
- Never silently return empty on EndpointLocation API denial; surface clear `error` / `warnings`.
- Keep `get_cvp_endpoint_location(search_term)` behavior unchanged.

## Non-goals

- FDB / ARP / OPNsense seeding (follow-up if needed).
- Fixing protobuf serialization quirks on `explanation_list` / StringValue display.
- Changing inventory, LLDP, or topology tool contracts beyond reuse.

## Approach (approved)

**LLDP-first pipeline:**

1. Load CVP inventory; keep **streaming-active EOS** switches (same spirit as topology/LLDP defaults: skip inactive and lab/virtual unless an existing include flag is wired later).
2. For each selected switch, collect LLDP neighbor rows via existing helpers (`grpc_get_lldp_neighbors` / oper-up port probe patterns used by topology).
3. Extract **deduped search keys** from neighbor rows, preferring:
   - management IPs (`management_address`, `management_addresses`, `mgmt_addr`)
   - chassis / eth MACs (`remote_chassis_id`, `chassis_id`, `eth_addr`, `chassis_id_str`)
   - system names last (`system_name`, `system_name_str`, `remote_system_name`)
4. Call EndpointLocation **`GetSome`** with those keys (`EndpointLocationSomeRequest.keys[].search_term`). If `GetSome` is unavailable or fails for the cluster, fall back to batched **`GetOne`**.
5. Build the existing response shape (`devices` + `endpoints`), enriching switch inventory for attachment serials.
6. For filtered tool: apply **client-side** filters on `device_id` (serial), `interface`, `vlan_id` after resolution.

## Tool behavior

| Tool | Behavior |
| --- | --- |
| `get_cvp_endpoint_location` | Unchanged: DNS-resolve search term candidates → `GetOne`. |
| `get_cvp_all_endpoint_locations` | Run LLDP→keys→`GetSome`/`GetOne` pipeline. |
| `get_cvp_endpoint_locations_filtered` | Same pipeline, then filter. Prefer scoping LLDP to one switch when `device_id` resolves. |

### Response additions

Keep existing `devices` / `endpoints` keys. Add:

- `seed_stats`: e.g. `switches_scanned`, `lldp_neighbor_rows`, `unique_search_keys`, `getsome_hits`, `getsome_misses` (names may vary slightly; document in README when implemented).
- `warnings`: partial LLDP, key lookup failures, GetSome fallback used, etc.
- `error`: only for hard failures (missing credentials, total inability to seed or query).

Do **not** return a bare empty success when EndpointLocation bulk APIs are denied; that path must not be used.

## Limits (honest coverage)

- Only neighbors that speak LLDP (or otherwise appear in LLDP Sysdb) are discoverable.
- Silent DHCP/Wi‑Fi hosts without LLDP will not appear (OPNsense/DHCP remains the right source for those).
- LLDP sweeps are bounded like topology (oper-up physical ports, active EOS only) to keep runtime acceptable.
- Hostname keys are weaker than IP/MAC; prefer addresses when present.

## Implementation sketch

| Area | Responsibility |
| --- | --- |
| `cvp_mcp/grpc/endpoint.py` | Replace `grpc_all_endpoint_locations` / `grpc_endpoints_by_filter` GetAll usage with `GetSome` + `GetOne` helpers; raise or return structured errors instead of swallowing UNIMPLEMENTED as `[]`. |
| New helper (e.g. `cvp_mcp/grpc/endpoint_seed.py`) | Inventory → LLDP → key extraction + normalization/dedupe. |
| `cloudvision_mcp.py` | Wire tools to new pipeline; include `seed_stats` / `warnings`. |
| Tests | Unit tests for key extraction, filter matching, GetSome response parsing, and “GetAll must not be called” / error surfacing. |
| README | Document LLDP-seeded bulk endpoint behavior and coverage limits. |

## Testing

- Unit: seed key extraction from sample LLDP rows; dedupe; filter by serial/interface/vlan; mock stub so GetAll is never invoked; GetSome/GetOne paths return converted endpoints.
- Live (manual): against homelab CVP via deployed MCP — `get_cvp_all_endpoint_locations` returns non-empty endpoints for LLDP neighbors (e.g. switch-to-switch and known LLDP hosts); filtered by `720xp-24` works; single `get_cvp_endpoint_location("pi5")` still works.

## Out of scope / follow-ups

- MAC/FDB Connector seeding for non-LLDP endpoints.
- Optional caller-supplied `search_terms` merge.
- Deploy/image bump for strongpod (separate ops step after merge).
