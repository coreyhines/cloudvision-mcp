# LLDP-seeded EndpointLocation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace EndpointLocation `GetAll` (blocked by CVP) with an LLDP-seeded `GetSome`/`GetOne` pipeline so bulk and filtered endpoint tools return real data.

**Architecture:** Collect LLDP neighbors from streaming-active EOS switches, extract deduped IP/MAC/hostname search keys, resolve them via EndpointLocation `GetSome` (fallback batched `GetOne`), then optionally filter by switch serial / interface / VLAN. Never call `GetAll`/`Subscribe`/`GetAllBatched` for EndpointLocation; never return a silent empty list when those APIs fail.

**Tech Stack:** Python 3.13, gRPC `arista.endpointlocation.v1`, existing `cvp_mcp.grpc.lldp` / inventory helpers, pytest + unittest.mock, black/ruff.

**Spec:** `docs/superpowers/specs/2026-07-28-lldp-seeded-endpoint-locations-design.md`

## Global Constraints

- Do **not** call EndpointLocation `GetAll`, `Subscribe`, `GetAllBatched`, or `SubscribeBatched`.
- Keep `get_cvp_endpoint_location(search_term)` behavior unchanged (`GetOne` only).
- Default seed scope: streaming-active EOS only; exclude lab/virtual (`_is_lab_device`) and inactive devices.
- Prefer search keys in order: management IPs → chassis/eth MACs → system names.
- Response must keep `devices` + `endpoints`; add `seed_stats` + `warnings` (and `error` only for hard failures).
- Coverage is LLDP-only (honest); no FDB/ARP/OPNsense in this plan.
- Format with black; pass ruff; add type hints on new public functions.
- Commit after each task green; do not push unless asked.

## File structure

| File | Responsibility |
| --- | --- |
| `cvp_mcp/grpc/endpoint_seed.py` (create) | Pure key extraction/normalization + inventory→LLDP seed orchestration returning keys + stats/warnings |
| `cvp_mcp/grpc/endpoint.py` (modify) | `GetSome` + batched `GetOne`; dict-based location filter; remove GetAll usage; structured lookup results |
| `cvp_mcp/grpc/__init__.py` (modify) | Export new public helpers used by tools |
| `cloudvision_mcp.py` (modify) | Wire `get_cvp_all_endpoint_locations` / `get_cvp_endpoint_locations_filtered` to pipeline |
| `tests/test_endpoint_seed.py` (create) | Key extraction / dedupe / MAC normalize |
| `tests/test_endpoint_lookup.py` (create) | GetSome/GetOne mocks; no GetAll; filter; tool wiring smoke |
| `README.md` (modify) | Document LLDP-seeded bulk endpoints + limits |

---

### Task 1: Seed key extraction (pure functions)

**Files:**
- Create: `cvp_mcp/grpc/endpoint_seed.py`
- Test: `tests/test_endpoint_seed.py`

**Interfaces:**
- Consumes: LLDP neighbor row dicts (README contract + `_neighbor_row` fields)
- Produces:
  - `normalize_endpoint_search_key(raw: str) -> str | None`
  - `extract_endpoint_search_keys(lldp_rows: list[dict]) -> list[str]`  
    Deduped, stable order: all IPs first (row order), then MACs, then hostnames. Skip empties / self-noise not required yet.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_endpoint_seed.py
from cvp_mcp.grpc.endpoint_seed import (
    extract_endpoint_search_keys,
    normalize_endpoint_search_key,
)


def test_normalize_strips_and_lowercases_hostname():
    assert normalize_endpoint_search_key("  Pi5.FreeBlizz.com ") == "pi5.freeblizz.com"


def test_normalize_mac_to_colon_lowercase():
    assert normalize_endpoint_search_key("2CCF67E1DAFC") == "2c:cf:67:e1:da:fc"
    assert normalize_endpoint_search_key("2c-cf-67-e1-da-fc") == "2c:cf:67:e1:da:fc"


def test_normalize_rejects_empty():
    assert normalize_endpoint_search_key("") is None
    assert normalize_endpoint_search_key("   ") is None


def test_extract_prefers_ip_then_mac_then_name_and_dedupes():
    rows = [
        {
            "management_addresses": ["10.0.2.2", "10.0.2.2"],
            "remote_chassis_id": "2c:cf:67:e1:da:fc",
            "system_name": "pi5",
        },
        {
            "management_address": "10.0.3.2",
            "eth_addr": "38:05:25:30:6f:05",
            "system_name_str": "strongpod",
        },
        {
            "chassis_id_str": "2C:CF:67:E1:DA:FC",  # dup mac of row0
            "system_name": "pi5",  # dup name
        },
    ]
    keys = extract_endpoint_search_keys(rows)
    assert keys == [
        "10.0.2.2",
        "10.0.3.2",
        "2c:cf:67:e1:da:fc",
        "38:05:25:30:6f:05",
        "pi5",
        "strongpod",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/corey/code/cloudvision-mcp && .venv/bin/pytest tests/test_endpoint_seed.py -v`  
Expected: FAIL (import error / module missing)

- [ ] **Step 3: Implement minimal `endpoint_seed.py`**

```python
# cvp_mcp/grpc/endpoint_seed.py
from __future__ import annotations

import re
from typing import Any

_MAC_HEX = re.compile(r"[^0-9a-fA-F]")


def normalize_endpoint_search_key(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    compact = _MAC_HEX.sub("", s)
    if len(compact) == 12 and all(c in "0123456789abcdefABCDEF" for c in compact):
        compact = compact.lower()
        return ":".join(compact[i : i + 2] for i in range(0, 12, 2))
    return s.lower()


def _add(bucket: list[str], seen: set[str], raw: Any) -> None:
    if raw is None:
        return
    if isinstance(raw, (list, tuple)):
        for item in raw:
            _add(bucket, seen, item)
        return
    key = normalize_endpoint_search_key(str(raw))
    if not key or key in seen:
        return
    seen.add(key)
    bucket.append(key)


def extract_endpoint_search_keys(lldp_rows: list[dict]) -> list[str]:
    ips: list[str] = []
    macs: list[str] = []
    names: list[str] = []
    seen: set[str] = set()
    for row in lldp_rows or []:
        if not isinstance(row, dict):
            continue
        for field in (
            "management_addresses",
            "management_address",
            "mgmt_addr",
            "remote_management_addresses",
            "remote_management_address",
        ):
            _add(ips, seen, row.get(field))
        for field in (
            "remote_chassis_id",
            "chassis_id",
            "chassis_id_str",
            "eth_addr",
            "remote_eth_addr",
        ):
            _add(macs, seen, row.get(field))
        for field in (
            "system_name",
            "system_name_str",
            "remote_system_name",
        ):
            _add(names, seen, row.get(field))
    return ips + macs + names
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_endpoint_seed.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cvp_mcp/grpc/endpoint_seed.py tests/test_endpoint_seed.py
git commit -m "$(cat <<'EOF'
Add LLDP neighbor key extraction for endpoint seeding.

EOF
)"
```

---

### Task 2: EndpointLocation GetSome + GetOne fallback (no GetAll)

**Files:**
- Modify: `cvp_mcp/grpc/endpoint.py`
- Modify: `cvp_mcp/grpc/__init__.py`
- Test: `tests/test_endpoint_lookup.py`

**Interfaces:**
- Consumes: gRPC `channel`, list of search keys; existing `convert_response_to_endpoint_location`, `_device_map_entries`
- Produces:
  - `EndpointLookupResult` TypedDict: `endpoints: list[EndpointLocation]`, `hits: int`, `misses: int`, `warnings: list[str]`, `method: str` (`"getsome"` | `"getone"`)
  - `grpc_endpoints_for_search_keys(channel, search_keys: list[str]) -> EndpointLookupResult`
  - `endpoint_location_matches_filters(endpoint: dict, *, device_id: str | None, interface: str | None, vlan_id: int | None) -> bool` (dict-shaped `location_list` from converter)
  - Keep `grpc_one_endpoint_location` as-is for the single-term tool
  - Remove or rewrite `grpc_all_endpoint_locations` / `grpc_endpoints_by_filter` so they **do not** call `GetAll` (delete GetAll bodies; callers move in Task 4)

- [ ] **Step 1: Write failing lookup tests**

```python
# tests/test_endpoint_lookup.py
from unittest.mock import MagicMock, patch

from cvp_mcp.grpc import endpoint


def test_grpc_endpoints_for_search_keys_uses_getsome_not_getall():
    channel = MagicMock()
    stub = MagicMock()
    stub.GetAll = MagicMock(side_effect=AssertionError("GetAll must not be called"))

    # One streamed GetSome response with value + empty error
    resp = MagicMock()
    resp.HasField.side_effect = lambda f: f == "value"
    resp.error = MagicMock()
    device = MagicMock()
    # _device_map_entries path: response.value has device_map
    # Simpler: patch _device_map_entries + convert
    stub.GetSome.return_value = [resp]

    converted = {
        "hostname": "pi5",
        "mac_address": "2c:cf:67:e1:da:fc",
        "ip_address": "10.0.2.2",
        "location_list": [
            {
                "device_id": {"value": "JPE19151499"},
                "interface": {"value": "Ethernet6"},
                "vlan_id": {"value": 2},
            }
        ],
    }

    with patch.object(endpoint.services, "EndpointLocationServiceStub", return_value=stub):
        with patch.object(endpoint, "_device_map_entries", return_value=[("k", device)]):
            with patch.object(
                endpoint, "convert_response_to_endpoint_location", return_value=converted
            ):
                result = endpoint.grpc_endpoints_for_search_keys(channel, ["10.0.2.2"])

    stub.GetSome.assert_called_once()
    stub.GetAll.assert_not_called()
    assert result["method"] == "getsome"
    assert result["hits"] == 1
    assert result["endpoints"] == [converted]


def test_getsome_failure_falls_back_to_getone():
    channel = MagicMock()
    stub = MagicMock()
    stub.GetSome.side_effect = RuntimeError("GetSome of EndpointLocation is not allowed")
    converted = {
        "hostname": "pi5",
        "mac_address": "2c:cf:67:e1:da:fc",
        "ip_address": "10.0.2.2",
        "location_list": [],
    }

    with patch.object(endpoint.services, "EndpointLocationServiceStub", return_value=stub):
        with patch.object(
            endpoint, "grpc_one_endpoint_location", return_value=[converted]
        ) as one:
            result = endpoint.grpc_endpoints_for_search_keys(channel, ["10.0.2.2", "pi5"])

    assert result["method"] == "getone"
    assert "getsome_failed" in ",".join(result["warnings"])
    assert one.call_count == 2
    assert result["hits"] == 2


def test_endpoint_location_matches_filters():
    ep = {
        "hostname": "pi5",
        "mac_address": "",
        "ip_address": "",
        "location_list": [
            {
                "device_id": {"value": "JPE19151499"},
                "interface": {"value": "Ethernet6"},
                "vlan_id": {"value": 2},
            }
        ],
    }
    assert endpoint.endpoint_location_matches_filters(
        ep, device_id="JPE19151499", interface=None, vlan_id=None
    )
    assert endpoint.endpoint_location_matches_filters(
        ep, device_id="JPE19151499", interface="Ethernet6", vlan_id=2
    )
    assert not endpoint.endpoint_location_matches_filters(
        ep, device_id="OTHER", interface=None, vlan_id=None
    )
    assert not endpoint.endpoint_location_matches_filters(
        ep, device_id=None, interface="Ethernet1", vlan_id=None
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_endpoint_lookup.py -v`  
Expected: FAIL (`grpc_endpoints_for_search_keys` missing)

- [ ] **Step 3: Implement lookup helpers in `endpoint.py`**

Implementation notes (must match tests):

```python
def grpc_endpoints_for_search_keys(channel, search_keys: list[str]) -> dict:
    keys = [k for k in (search_keys or []) if k]
    if not keys:
        return {
            "endpoints": [],
            "hits": 0,
            "misses": 0,
            "warnings": ["no_search_keys"],
            "method": "getsome",
        }
    stub = services.EndpointLocationServiceStub(channel)
    try:
        return _grpc_endpoints_via_getsome(stub, keys)
    except Exception as e:
        logging.error("EndpointLocation GetSome failed: %s", e)
        warnings = [f"getsome_failed:{e}"]
        endpoints = []
        hits = 0
        for key in keys:
            found = grpc_one_endpoint_location(channel, key)
            if found:
                hits += 1
                endpoints.extend(found)
            # else miss
        return {
            "endpoints": _dedupe_endpoints(endpoints),
            "hits": hits,
            "misses": len(keys) - hits,
            "warnings": warnings + ["fell_back_to_getone"],
            "method": "getone",
        }


def _grpc_endpoints_via_getsome(stub, keys: list[str]) -> dict:
    req = services.EndpointLocationSomeRequest(
        keys=[
            models.EndpointLocationKey(search_term=wrappers.StringValue(value=k))
            for k in keys
        ]
    )
    endpoints = []
    hits = 0
    misses = 0
    warnings: list[str] = []
    for resp in stub.GetSome(req, timeout=RPC_TIMEOUT):
        if resp.HasField("error") and resp.error.value:
            misses += 1
            warnings.append(f"getsome_key_error:{resp.error.value}")
            continue
        if not resp.HasField("value"):
            misses += 1
            continue
        batch = []
        for _k, device in _device_map_entries(resp.value):
            batch.append(convert_response_to_endpoint_location(device))
        if batch:
            hits += 1
            endpoints.extend(batch)
        else:
            misses += 1
    return {
        "endpoints": _dedupe_endpoints(endpoints),
        "hits": hits,
        "misses": misses,
        "warnings": warnings,
        "method": "getsome",
    }


def endpoint_location_matches_filters(
    endpoint: dict,
    *,
    device_id: str | None = None,
    interface: str | None = None,
    vlan_id: int | None = None,
) -> bool:
    locs = endpoint.get("location_list") or []
    if not locs:
        return not device_id and not interface and vlan_id is None
    for loc in locs:
        if device_id:
            did = (loc.get("device_id") or {}).get("value")
            if did != device_id:
                continue
        if interface:
            iface = (loc.get("interface") or {}).get("value")
            if iface != interface:
                continue
        if vlan_id is not None:
            vid = (loc.get("vlan_id") or {}).get("value")
            if vid != vlan_id:
                continue
        return True
    return False
```

Also:
- Add `_dedupe_endpoints` by `(mac_address, ip_address, hostname)` tuple.
- Change `grpc_all_endpoint_locations` and `grpc_endpoints_by_filter` to raise `RuntimeError("endpointlocation_getall_disabled")` **or** delete them and update imports in Task 4 — prefer delete + fix imports so GetAll cannot be called accidentally.
- Remove `_location_matches_filter` protobuf helper if unused after dict filter lands.

Export new symbols from `cvp_mcp/grpc/__init__.py`.

- [ ] **Step 4: Run lookup tests**

Run: `.venv/bin/pytest tests/test_endpoint_lookup.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cvp_mcp/grpc/endpoint.py cvp_mcp/grpc/__init__.py tests/test_endpoint_lookup.py
git commit -m "$(cat <<'EOF'
Resolve endpoint locations via GetSome with GetOne fallback.

EOF
)"
```

---

### Task 3: LLDP seed orchestration

**Files:**
- Modify: `cvp_mcp/grpc/endpoint_seed.py`
- Modify: `tests/test_endpoint_seed.py`

**Interfaces:**
- Consumes: `datadict`, optional `device_serials: list[str] | None`, `grpc_all_inventory`, `grpc_get_lldp_neighbors`, `_is_lab_device`, `extract_endpoint_search_keys`
- Produces:
  - `EndpointSeedResult` dict:  
    `search_keys: list[str]`,  
    `seed_stats: dict` with `switches_scanned`, `lldp_neighbor_rows`, `unique_search_keys`,  
    `warnings: list[str]`
  - `seed_endpoint_search_keys(datadict: dict, *, device_serials: list[str] | None = None, include_lab_devices: bool = False) -> dict`

Behavior:
1. If `device_serials` provided, scan only those serials (still verify they look like inventory devices when possible).
2. Else call `grpc_all_inventory` (needs open channel OR pass channel — follow inventory pattern: open channel inside helper with `createConnection` like `network_map`, **or** accept `channel` + reuse `grpc_all_inventory(channel)` from caller). Prefer accepting `channel` + `datadict` so tools open one channel:

```python
def seed_endpoint_search_keys(
    datadict: dict,
    channel,
    *,
    device_serials: list[str] | None = None,
    include_lab_devices: bool = False,
) -> dict: ...
```

3. Select active EOS: `streaming_status == "Active"` and `device_type == "EOS"` (or not lab); skip AP / inactive / lab unless `include_lab_devices`.
4. For each serial, call `grpc_get_lldp_neighbors(datadict, serial, device_model=model)` and extend rows from `items`.
5. Collect warnings from LLDP envelopes; continue on per-switch failure.
6. If zero switches scanned → warning `no_switches_to_scan`.
7. If zero keys → warning `no_lldp_search_keys`.

- [ ] **Step 1: Write failing orchestration tests**

```python
from unittest.mock import MagicMock, patch

from cvp_mcp.grpc.endpoint_seed import seed_endpoint_search_keys


def test_seed_endpoint_search_keys_from_lldp_inventory():
    datadict = {"cvp": "x:443", "cvtoken": "t"}
    channel = MagicMock()
    active = [
        {
            "serial_number": "SN1",
            "hostname": "720xp-24",
            "model": "CCS-720XP-24ZY4",
            "streaming_status": "Active",
            "device_type": "EOS",
        }
    ]
    lldp = {
        "items": [
            {
                "management_address": "10.0.2.2",
                "remote_chassis_id": "2c:cf:67:e1:da:fc",
                "system_name": "pi5",
            }
        ],
        "warnings": [],
    }
    with patch(
        "cvp_mcp.grpc.endpoint_seed.grpc_all_inventory",
        return_value=(active, []),
    ):
        with patch(
            "cvp_mcp.grpc.endpoint_seed.grpc_get_lldp_neighbors",
            return_value=lldp,
        ) as lldp_fn:
            result = seed_endpoint_search_keys(datadict, channel)

    lldp_fn.assert_called_once()
    assert result["search_keys"][0] == "10.0.2.2"
    assert result["seed_stats"]["switches_scanned"] == 1
    assert result["seed_stats"]["lldp_neighbor_rows"] == 1
    assert result["seed_stats"]["unique_search_keys"] == 3


def test_seed_respects_device_serials_allowlist():
    datadict = {"cvp": "x:443", "cvtoken": "t"}
    channel = MagicMock()
    with patch(
        "cvp_mcp.grpc.endpoint_seed.grpc_all_inventory",
        return_value=(
            [
                {
                    "serial_number": "SN1",
                    "streaming_status": "Active",
                    "device_type": "EOS",
                    "model": "X",
                },
                {
                    "serial_number": "SN2",
                    "streaming_status": "Active",
                    "device_type": "EOS",
                    "model": "Y",
                },
            ],
            [],
        ),
    ):
        with patch(
            "cvp_mcp.grpc.endpoint_seed.grpc_get_lldp_neighbors",
            return_value={"items": [], "warnings": []},
        ) as lldp_fn:
            seed_endpoint_search_keys(
                datadict, channel, device_serials=["SN2"]
            )
    assert lldp_fn.call_count == 1
    assert lldp_fn.call_args.args[1] == "SN2"
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest tests/test_endpoint_seed.py -v`  
Expected: FAIL on missing `seed_endpoint_search_keys`

- [ ] **Step 3: Implement `seed_endpoint_search_keys`**

Import inventory + LLDP + `_is_lab_device`. Use `grpc_all_inventory(channel)` return `(active, inactive)` as today. Filter and scan as above.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_endpoint_seed.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cvp_mcp/grpc/endpoint_seed.py tests/test_endpoint_seed.py
git commit -m "$(cat <<'EOF'
Seed endpoint search keys from active-switch LLDP neighbors.

EOF
)"
```

---

### Task 4: Wire MCP tools

**Files:**
- Modify: `cloudvision_mcp.py` (`get_cvp_all_endpoint_locations`, `get_cvp_endpoint_locations_filtered`, imports)
- Modify: `tests/test_endpoint_lookup.py` (tool-level smoke with patches)

**Interfaces:**
- Consumes: `seed_endpoint_search_keys`, `grpc_endpoints_for_search_keys`, `endpoint_location_matches_filters`, `grpc_one_inventory_serial`, `_resolve_device_serial`
- Produces: tool return dict:
  ```python
  {
    "devices": dict[str, SwitchInfo],
    "endpoints": list[EndpointLocation],
    "seed_stats": dict,  # includes getsome_hits/misses merged from lookup
    "warnings": list[str],
  }
  ```
  On hard failure (e.g. missing env): `{"error": "...", "warnings": [...]}`

- [ ] **Step 1: Write failing tool smoke test**

```python
def test_get_cvp_all_endpoint_locations_pipeline(monkeypatch):
    import cloudvision_mcp as mcp_mod

    monkeypatch.setattr(mcp_mod, "CVP_TRANSPORT", "grpc")
    monkeypatch.setattr(mcp_mod, "get_env_vars", lambda: {"cvp": "h:443", "cvtoken": "t"})
    monkeypatch.setattr(mcp_mod, "createConnection", lambda d: MagicMock())

    fake_channel = MagicMock()
    fake_channel.__enter__ = lambda s: fake_channel
    fake_channel.__exit__ = lambda *a: False

    with patch("cloudvision_mcp.grpc.secure_channel", return_value=fake_channel):
        with patch(
            "cloudvision_mcp.seed_endpoint_search_keys",
            return_value={
                "search_keys": ["10.0.2.2"],
                "seed_stats": {
                    "switches_scanned": 1,
                    "lldp_neighbor_rows": 1,
                    "unique_search_keys": 1,
                },
                "warnings": [],
            },
        ):
            with patch(
                "cloudvision_mcp.grpc_endpoints_for_search_keys",
                return_value={
                    "endpoints": [
                        {
                            "hostname": "pi5",
                            "mac_address": "2c:cf:67:e1:da:fc",
                            "ip_address": "10.0.2.2",
                            "location_list": [
                                {"device_id": {"value": "SN1"}, "interface": {"value": "Ethernet6"}, "vlan_id": {"value": 2}}
                            ],
                        }
                    ],
                    "hits": 1,
                    "misses": 0,
                    "warnings": [],
                    "method": "getsome",
                },
            ):
                with patch(
                    "cloudvision_mcp.grpc_one_inventory_serial",
                    return_value={"serial_number": "SN1", "hostname": "720xp-24"},
                ):
                    out = mcp_mod.get_cvp_all_endpoint_locations()

    assert out["endpoints"][0]["hostname"] == "pi5"
    assert out["seed_stats"]["unique_search_keys"] == 1
    assert out["seed_stats"]["getsome_hits"] == 1
    assert "SN1" in out["devices"]
```

(Adjust import paths if tools import helpers from `cvp_mcp.grpc.endpoint` / `endpoint_seed` directly — patch where bound.)

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest tests/test_endpoint_lookup.py::test_get_cvp_all_endpoint_locations_pipeline -v`  
Expected: FAIL (old tool still calls GetAll path / missing seed_stats)

- [ ] **Step 3: Rewrite tools**

`get_cvp_all_endpoint_locations`:

1. Open grpc channel.
2. `seed = seed_endpoint_search_keys(datadict, channel)`
3. `lookup = grpc_endpoints_for_search_keys(channel, seed["search_keys"])`
4. Enrich `devices` from endpoint location serials via `grpc_one_inventory_serial`.
5. Merge stats: `seed_stats = {**seed["seed_stats"], "getsome_hits": lookup["hits"], "getsome_misses": lookup["misses"], "lookup_method": lookup["method"]}`
6. `warnings = seed["warnings"] + lookup["warnings"]`
7. Return dict.

`get_cvp_endpoint_locations_filtered`:

1. Resolve `device_id` to serial if provided (existing `_resolve_device_serial` error envelope on failure).
2. `seed_endpoint_search_keys(..., device_serials=[serial] if serial else None)`.
3. Lookup → filter with `endpoint_location_matches_filters`.
4. Same response shape.

Remove imports of deleted `grpc_all_endpoint_locations` / `grpc_endpoints_by_filter`.

Update tool docstrings to say LLDP-seeded GetSome/GetOne, not “streams all”.

- [ ] **Step 4: Run unit suite subset**

Run: `.venv/bin/pytest tests/test_endpoint_seed.py tests/test_endpoint_lookup.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cloudvision_mcp.py tests/test_endpoint_lookup.py cvp_mcp/grpc/__init__.py
git commit -m "$(cat <<'EOF'
Wire bulk endpoint tools to LLDP-seeded GetSome lookups.

EOF
)"
```

---

### Task 5: README + full test hygiene

**Files:**
- Modify: `README.md` (Tools table + short “Endpoint locations” subsection)
- Modify: `docs/superpowers/specs/2026-07-28-lldp-seeded-endpoint-locations-design.md` (Status → Implemented when code lands — do this in the same commit as README after tests green)

- [ ] **Step 1: Document behavior**

Add README rows/section stating:
- Bulk/filtered endpoints are **LLDP-seeded** via `GetSome`/`GetOne`.
- Coverage excludes silent non-LLDP hosts.
- Response includes `seed_stats` and `warnings`.

- [ ] **Step 2: Run full unit tests + format**

```bash
.venv/bin/black cvp_mcp/grpc/endpoint.py cvp_mcp/grpc/endpoint_seed.py tests/test_endpoint_seed.py tests/test_endpoint_lookup.py cloudvision_mcp.py
.venv/bin/ruff check cvp_mcp/grpc/endpoint.py cvp_mcp/grpc/endpoint_seed.py tests/test_endpoint_seed.py tests/test_endpoint_lookup.py
.venv/bin/pytest tests/ -q
```

Expected: all green (or only pre-existing failures unrelated — do not leave new failures).

- [ ] **Step 3: Commit**

```bash
git add README.md docs/superpowers/specs/2026-07-28-lldp-seeded-endpoint-locations-design.md
git commit -m "$(cat <<'EOF'
Document LLDP-seeded endpoint location tools.

EOF
)"
```

---

### Task 6: Manual live verification (no code unless bugs)

**Files:** none (ops check)

- [ ] **Step 1:** From Cursor MCP (or against `https://cloudvision-mcp.freeblizz.com/mcp` after deploy), call:
  - `get_cvp_endpoint_location` with `pi5` → still works
  - `get_cvp_all_endpoint_locations` → non-empty `endpoints`, populated `seed_stats`, no GetAll error swallowed
  - `get_cvp_endpoint_locations_filtered` with `device_id=720xp-24` → endpoints attached to that switch

- [ ] **Step 2:** On strongpod, confirm app logs show GetSome or GetOne fallback, **not** `GetAll of EndpointLocation is not allowed` for these tools.

- [ ] **Step 3:** If live fails because image is stale, note deploy as follow-up (out of scope for code tasks unless user asks to deploy).

Do **not** commit empty commits for this task.

---

## Spec coverage checklist

| Spec requirement | Task |
| --- | --- |
| No EndpointLocation GetAll | 2, 4 |
| LLDP seed from active EOS | 3 |
| Key preference IP → MAC → name | 1 |
| GetSome with GetOne fallback | 2 |
| Filter by serial/interface/vlan | 2, 4 |
| `seed_stats` + `warnings` | 3, 4 |
| Single-term tool unchanged | 4 (leave alone) |
| README limits | 5 |
| Unit + live verify | 1–6 |
| No FDB/OPNsense | (excluded) |

## Placeholder / consistency review

- Function names locked: `normalize_endpoint_search_key`, `extract_endpoint_search_keys`, `seed_endpoint_search_keys`, `grpc_endpoints_for_search_keys`, `endpoint_location_matches_filters`.
- `seed_stats` keys: `switches_scanned`, `lldp_neighbor_rows`, `unique_search_keys`, `getsome_hits`, `getsome_misses`, `lookup_method`.
- If live CVP also blocks `GetSome`, Task 2 fallback covers it; Task 6 confirms.
