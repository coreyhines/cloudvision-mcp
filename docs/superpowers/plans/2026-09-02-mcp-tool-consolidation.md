# MCP Tool Surface Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hard-cut the CloudVision MCP catalog from ~44 flat `get_cvp_*` / write tools to **12** always-on group tools (+ optional `studios_write`) with required `action`, matching `docs/mcp-tool-consolidation-spec.md`.

**Architecture:** Presentation-only regrouping (opnsense `GroupedTool` ideas, **not** that package). Extract today’s `@mcp.tool` bodies into `MemberSpec.call` callables; a `GroupedTool` builds a union JSON Schema, validates/strips per action, and dispatches. Register via **MCP SDK** `FastMCP` (`mcp.server.fastmcp` from `mcp[cli]`) — **not** the standalone `fastmcp` package opnsense uses (`FunctionTool(parameters=…)` is unavailable here). Use a dynamic keyword-only handler signature + overwrite `tool.parameters` with the authored union schema.

**Tech Stack:** Python 3.13, MCP SDK FastMCP, existing `cvp_mcp.grpc.*`, pytest + importlib reload, black/ruff.

**Spec:** `docs/mcp-tool-consolidation-spec.md`
**Sibling:** `docs/compliance-config-image-spec.md`
**Review record:** `docs/mcp-tool-consolidation-adversarial-review.md`

## Global Constraints

- Hard cut: **no** flat MCP names in `list_tools`; **no** aliases; **no** `TOOL_SURFACE` flag.
- Always-on groups: exactly **12** (`inventory`, `endpoints`, `device`, `overlay`, `routing`, `topology`, `events`, `flow`, `probes`, `compliance`, `meta`, `studios`).
- `studios_write` registered only when `CLOUDVISION_MCP_ALLOW_WRITES` is exactly `"1"` (import-time; restart to change) → **13** tools.
- Member slots: **46** (44 legacy flats + `compliance.config_status` + `compliance.image_status`).
- Dispatcher never opens gRPC; members already do env + envelope.
- MCP **never submits** workspaces / never approves change controls.
- `inventory.get` returns a **dict**, not a JSON `str`.
- Rate-limit buckets only: `inventory.list` (6/60s), `topology.map` (4/60s), `events.search` (10/60s). No bucket for `inventory.search`.
- `CVP_MCP_DISABLED_TOOLS`: soft-disable; accepts `group` and `group.action`; tool stays listed.
- Status actions: register; on known 403 return forbidden envelopes (`coverage=none`); no digest fake status.
- Stay on a **feature branch** until acceptance; do not merge mid-wave (I7).
- Format with black; pass ruff; type-hint new public APIs.
- Commit after each task green; do not push unless asked.
- Live serials/IPs/hosts stay out of tracked `docs/`.
- **No import cycle:** `cvp_mcp.members.*` must **not** import `cloudvision_mcp`. Use `cvp_mcp.env.env_datadict_from_os`, `cvp_mcp.grpc.*`, `createConnection`, `tool_envelope`, `client_error` directly. Device resolve uses `resolve_device_to_serial` (not `cloudvision_mcp._resolve_device_serial`).

## File structure

| File | Responsibility |
| --- | --- |
| `cvp_mcp/schema_fields.py` (create) | Shared JSON-Schema property snippets (`device_id`, `workspace_id`, `confirm`, `preview_token`, `studio_id`, …) + `is_shared` |
| `cvp_mcp/grouped_tool.py` (create) | `MemberSpec`, `GroupedTool` (union schema, help, validate, strip, dispatch, disable/rate hooks) |
| `cvp_mcp/register_grouped.py` (create) | SDK FastMCP registration: dynamic signature + `parameters` overwrite |
| `cvp_mcp/tool_groups.py` (create) | `GROUPS`, member maps, frozen `LEGACY_FLAT_TO_ACTION` / `ALWAYS_ON_GROUPS`, docstring count string |
| `cvp_mcp/members/*.py` (create) | One module per group: extract today’s tool bodies as plain callables (+ MemberSpecs) |
| `cvp_mcp/members/compliance_status.py` (create) | Forbidden stubs for `config_status` / `image_status` (no Resource API dependency) |
| `cvp_mcp/tool_access.py` (modify) | Match `group` and `group.action` (and keep exact-name match for safety during transition) |
| `cvp_mcp/rate_limit.py` (modify) | Rename three bucket keys; remove dead search inventory bucket if any |
| `cloudvision_mcp.py` (modify) | Delete all `@mcp.tool` flats; call `register_all_groups(mcp)` after `mcp = FastMCP(...)` |
| `tests/test_grouped_tool.py` (create) | Dispatcher unit tests |
| `tests/test_register_grouped.py` (create) | FastMCP list/call with union schema |
| `tests/test_tool_catalog.py` (create) | Frozen 46-member / 12-group constants (no server import) |
| `tests/test_tool_surface.py` (create, Task 8) | 12/13 names, no `get_cvp_*`, help completeness via reload |
| `tests/test_write_registration.py` (modify) | Expect `studios_write` instead of nine flat write names |
| `tests/test_lab_device_filter.py`, `tests/test_device_resolve.py` (modify) | Import LLDP member callable, not flat MCP tool |
| `README.md`, `docs/studios-*.md` (modify) | Flat → `group.action` (product docs only) |

---

### Task 1: Shared fields + GroupedTool dispatcher

**Files:**
- Create: `cvp_mcp/schema_fields.py`
- Create: `cvp_mcp/grouped_tool.py`
- Test: `tests/test_grouped_tool.py`

**Interfaces:**
- Produces:
  - `SHARED_FIELDS: dict[str, dict[str, Any]]`
  - `is_shared(name: str) -> bool`
  - `@dataclass MemberSpec`: `action`, `description`, `required: list[str]`, `properties: dict[str, dict]`, `call: Callable[..., Any]`, optional `rate_limit_key: str | None = None`
  - `class GroupedTool`: `name`, `description`, `members: dict[str, MemberSpec]`, `input_schema`, `help() -> dict`, `execute(params: dict) -> Any`
  - Error shapes (exact keys):
    - `{"error": "action_unknown", "tool": <group>, "action": <val>, "hint": "help"}`
    - `{"error": "action_args_invalid", "tool": <group>, "action": <val>, "required": [...]}`
    - `{"error": "tool_disabled", "tool": <group or group.action>}`
    - `{"error": "rate_limit_exceeded", "tool": <group.action>}`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_grouped_tool.py
from cvp_mcp.grouped_tool import GroupedTool, MemberSpec


def _echo(**kwargs):
    return {"echo": kwargs}


def _group() -> GroupedTool:
    return GroupedTool(
        name="inventory",
        description="Inventory ops",
        members={
            "get": MemberSpec(
                action="get",
                description="One device",
                required=["device_id"],
                properties={
                    "device_id": {"type": "string", "description": "Serial or hostname"}
                },
                call=_echo,
            ),
            "search": MemberSpec(
                action="search",
                description="Search",
                required=["query"],
                properties={"query": {"type": "string", "description": "Substring"}},
                call=_echo,
            ),
        },
    )


def test_schema_requires_only_action_and_enums_members_plus_help():
    g = _group()
    assert g.input_schema["required"] == ["action"]
    assert set(g.input_schema["properties"]["action"]["enum"]) == {
        "get",
        "search",
        "help",
    }


def test_help_lists_required_and_optional():
    help_out = _group().execute({"action": "help"})
    actions = {row["action"]: row for row in help_out["actions"]}
    assert actions["get"]["required"] == ["device_id"]
    assert "query" in actions["search"]["required"]


def test_unknown_action_envelope():
    out = _group().execute({"action": "nope"})
    assert out["error"] == "action_unknown"
    assert out["tool"] == "inventory"
    assert out["hint"] == "help"


def test_missing_required_envelope():
    out = _group().execute({"action": "get"})
    assert out["error"] == "action_args_invalid"
    assert out["required"] == ["device_id"]


def test_strips_wrong_action_fields_and_omits_none():
    out = _group().execute(
        {"action": "get", "device_id": "ABC", "query": "should-strip", "noise": 1}
    )
    assert out == {"echo": {"device_id": "ABC"}}


def test_empty_string_required_is_missing():
    out = _group().execute({"action": "get", "device_id": "  "})
    assert out["error"] == "action_args_invalid"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_grouped_tool.py -v`
Expected: FAIL (import / not found)

- [ ] **Step 3: Implement `schema_fields.py` and `grouped_tool.py`**

Minimal behaviour (port ideas from opnsense `grouped_tool.py`, adapt envelopes to this repo):

- Union schema: `action` enum = sorted(members) + `help`; only `action` in JSON-Schema `required`.
- Shared field names use `SHARED_FIELDS` verbatim; other colliding fields get `[action1, action2] …` description prefix.
- `execute`: pop `action`; `help` → metadata; unknown → `action_unknown`; before call, remap aliases (empty map OK); drop keys not in member.properties; treat `None` / `""` / whitespace-only strings as absent for required checks; call `member.call(**cleaned)`.
- Optional: accept `disabled_checker: Callable[[str], bool]` and `rate_limiter: Callable[[str], dict | None]` injected later in Task 3 — for Task 1, inline hooks that call `disabled_tools()` / buckets if imported, or leave no-ops and wire in Task 3. Prefer wiring disable/rate **inside** `GroupedTool.execute` in Task 3 so Task 1 stays pure.

Include `device_id`, `workspace_id`, `studio_id`, `confirm`, `preview_token`, `query` (only if identical meaning — if search vs events differ, **omit** `query` from shared and keep per-member text).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_grouped_tool.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cvp_mcp/schema_fields.py cvp_mcp/grouped_tool.py tests/test_grouped_tool.py
git commit -m "$(cat <<'EOF'
feat: add GroupedTool dispatcher and shared schema fields

EOF
)"
```

---

### Task 2: SDK FastMCP registration helper

**Files:**
- Create: `cvp_mcp/register_grouped.py`
- Test: `tests/test_register_grouped.py`

**Interfaces:**
- Consumes: `GroupedTool` with `name`, `description`, `input_schema`, `execute`
- Produces: `register_grouped_tool(mcp: FastMCP, group: GroupedTool) -> None`

**Critical (do not skip):** Official MCP SDK `FastMCP.add_tool` builds `fn_metadata` from the callable signature. Passing `**kwargs` alone makes Pydantic require a bogus `kwargs` field. Mutating `tool.parameters` alone does **not** fix `call_tool`. Registration **must**:

1. Build a keyword-only signature: required `action: str`, every other union property as `Any | None = None`.
2. Set `handler.__signature__` and `__annotations__`.
3. `mcp.add_tool(handler, name=group.name, description=group.description)`.
4. Overwrite `mcp._tool_manager._tools[group.name].parameters = group.input_schema` so `list_tools` shows enums/descriptions.
5. Handler body: `return group.execute({k: v for k, v in kwargs.items()})` (GroupedTool strips Nones / wrong keys).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_register_grouped.py
import asyncio

from mcp.server.fastmcp import FastMCP

from cvp_mcp.grouped_tool import GroupedTool, MemberSpec
from cvp_mcp.register_grouped import register_grouped_tool


def test_register_exposes_enum_and_dispatches():
    mcp = FastMCP("test")
    group = GroupedTool(
        name="meta",
        description="Meta",
        members={
            "probe_apis": MemberSpec(
                action="probe_apis",
                description="Probe",
                required=[],
                properties={},
                call=lambda: {"ok": True},
            )
        },
    )
    register_grouped_tool(mcp, group)
    tools = asyncio.run(mcp.list_tools())
    assert tools[0].name == "meta"
    assert "help" in tools[0].inputSchema["properties"]["action"]["enum"]
    result = asyncio.run(mcp.call_tool("meta", {"action": "probe_apis"}))
    # SDK wraps dict returns as TextContent JSON; assert payload contains ok
    text = result[0].text if isinstance(result, list) else str(result)
    assert "ok" in text
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `uv run pytest tests/test_register_grouped.py -v`

- [ ] **Step 3: Implement `register_grouped_tool`**

```python
# cvp_mcp/register_grouped.py (skeleton)
from __future__ import annotations

import inspect
from typing import Any

from mcp.server.fastmcp import FastMCP

from cvp_mcp.grouped_tool import GroupedTool


def register_grouped_tool(mcp: FastMCP, group: GroupedTool) -> None:
    props = group.input_schema.get("properties") or {}
    parameters: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {"return": Any}
    for name in props:
        if name == "action":
            parameters.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=str,
                )
            )
            annotations[name] = str
        else:
            parameters.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=None,
                    annotation=Any | None,
                )
            )
            annotations[name] = Any | None

    def handler(**kwargs: Any) -> Any:
        return group.execute(dict(kwargs))

    handler.__name__ = group.name
    handler.__doc__ = group.description
    handler.__signature__ = inspect.Signature(parameters, return_annotation=Any)
    handler.__annotations__ = annotations
    mcp.add_tool(handler, name=group.name, description=group.description)
    # MCP SDK FastMCP has no FunctionTool(parameters=…) (unlike standalone fastmcp).
    # Overwrite list_tools schema after signature-based registration (mcp[cli]<2).
    mcp._tool_manager._tools[group.name].parameters = group.input_schema
```

- [ ] **Step 4: Run test — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add cvp_mcp/register_grouped.py tests/test_register_grouped.py
git commit -m "$(cat <<'EOF'
feat: register GroupedTool on MCP SDK FastMCP with union schemas

EOF
)"
```

---

### Task 3: Disable grammar + rate-limit key rename

**Files:**
- Modify: `cvp_mcp/tool_access.py`
- Modify: `cvp_mcp/rate_limit.py`
- Modify: `cvp_mcp/grouped_tool.py` (call disable + rate before member)
- Test: `tests/test_grouped_tool.py` (add cases), keep/extend any existing rate-limit tests

**Interfaces:**
- Produces:
  - `is_tool_disabled(tool_key: str) -> bool` where `tool_key` is `group` or `group.action`; true if exact match **or** (for `group.action`) the bare `group` is listed.
  - `_EXPENSIVE_TOOL_LIMITS` keys: `inventory.list`, `topology.map`, `events.search` only.
  - `GroupedTool.execute`: if disabled → `tool_disabled`; if member has `rate_limit_key` and bucket denies → `rate_limit_exceeded` with `tool` = that key.

- [ ] **Step 1: Failing tests**

```python
def test_disable_whole_group(monkeypatch):
    monkeypatch.setenv("CVP_MCP_DISABLED_TOOLS", "inventory")
    out = _group().execute({"action": "get", "device_id": "x"})
    assert out["error"] == "tool_disabled"


def test_disable_one_action(monkeypatch):
    monkeypatch.setenv("CVP_MCP_DISABLED_TOOLS", "inventory.search")
    assert _group().execute({"action": "get", "device_id": "x"})["echo"]["device_id"] == "x"
    assert _group().execute({"action": "search", "query": "abc"})["error"] == "tool_disabled"
```

Add a rate-limit test that constructs a member with `rate_limit_key="inventory.list"`, exhausts the bucket (6 calls), asserts `rate_limit_exceeded`.

- [ ] **Step 2–4: Implement, pytest green, commit**

```bash
git commit -m "$(cat <<'EOF'
feat: disable group.action keys and retarget rate-limit buckets

EOF
)"
```

---

### Task 4: Catalog constants + red surface test

**Files:**
- Create: `cvp_mcp/tool_groups.py` (skeleton: constants + `build_groups()` can return `[]` until later tasks)
- Create: `tests/test_tool_catalog.py` (constants / bijection only — no `cloudvision_mcp` import)

**Interfaces:**
- Produces frozen data matching the spec table:

```python
ALWAYS_ON_GROUPS: frozenset[str] = frozenset({...12 names...})
DOCSTRING_SURFACE = (
    "46 operations behind 12 names (13 with writes)"
)
LEGACY_FLAT_TO_ACTION: dict[str, str] = {
    "get_cvp_one_device": "inventory.get",
    # ... every row from the spec member map, including the two status actions
    # as synthetic flats: "__compliance_config_status__": "compliance.config_status"
    # Prefer listing status actions only in MEMBER_ACTIONS frozenset rather than fake flats.
}
MEMBER_ACTIONS: frozenset[str]  # exactly 46 "group.action" strings
```

Surface test (reload pattern from `tests/test_write_registration.py`):

```python
def test_writes_off_surface(monkeypatch):
    names = _tool_names(monkeypatch, None)
    assert names == ALWAYS_ON_GROUPS
    assert not any(n.startswith("get_cvp_") for n in names)
    assert "studios_write" not in names
    assert "submit_cvp_workspace" not in names


def test_writes_on_surface(monkeypatch):
    names = _tool_names(monkeypatch, "1")
    assert names == ALWAYS_ON_GROUPS | {"studios_write"}


def test_member_bijection():
    from cvp_mcp.tool_groups import MEMBER_ACTIONS, iter_member_actions

    assert set(iter_member_actions()) == MEMBER_ACTIONS
    assert len(MEMBER_ACTIONS) == 46


def test_docstring_count_locked():
    from cvp_mcp import tool_groups

    assert DOCSTRING_SURFACE in tool_groups.__doc__
```

- [ ] **Step 1: Write tests + skeleton constants so bijection/docstring can pass; surface tests FAIL until registration exists**

Until Task 8 deletes flats, full `list_tools` name-count tests stay out of CI. **Decision:** Task 4 only commits catalog constants + tests that lock those sets **without** importing `cloudvision_mcp`. Task 8 adds the reload-based surface tests.

Task 4 deliverable: frozen `MEMBER_ACTIONS` (46), `LEGACY_FLAT_TO_ACTION` (44 flats), `ALWAYS_ON_GROUPS`, module docstring containing `DOCSTRING_SURFACE`, and tests that lock those sets **without** requiring registration yet.

- [ ] **Step 2–4: Implement constants, green constant tests, commit**

```bash
git commit -m "$(cat <<'EOF'
feat: lock grouped MCP member catalog constants

EOF
)"
```

---

### Task 5: Extract members for always-on read groups (batch A)

**Files:**
- Create: `cvp_mcp/members/__init__.py`
- Create: `cvp_mcp/members/inventory.py`, `endpoints.py`, `device.py`, `overlay.py`, `routing.py`
- Modify: `cvp_mcp/tool_groups.py` — wire these groups into `build_groups()`
- Test: dispatch smoke per group in `tests/test_tool_groups_dispatch.py` (mock member calls or patch grpc)

**Interfaces:**
- Each module exports `members() -> dict[str, MemberSpec]`.
- `inventory.get` **must** return `dict`: replace `return json.dumps(...)` with `return err` / `return device` (same fields as today’s JSON payload). Keep error keys (`device_not_found`, `device_ambiguous`, …).
- Move bodies out of `cloudvision_mcp.py` **without** registering group tools yet (keep old `@mcp.tool` wrappers thin: call the member, until Task 8 deletes them). Dual path during the branch is OK; merge gate deletes flats. Members must not import `cloudvision_mcp` (see Global Constraints).

Thin wrapper pattern while flats still exist:

```python
# cloudvision_mcp.py (temporary)
@mcp.tool()
@tool_enabled("get_cvp_one_device")
def get_cvp_one_device(device_id) -> dict:  # type change to dict
    return inventory_get(device_id)
```

Prefer changing the flat return type immediately when extracting `inventory.get` so callers/tests see dict early.

- [ ] **Step 1: Failing test for inventory.get dict**

```python
def test_inventory_get_returns_dict_not_str(monkeypatch):
    from cvp_mcp.members.inventory import inventory_get

    monkeypatch.setattr(...)  # mock resolve + grpc to return {"serial_number": "X"}
    out = inventory_get("X")
    assert isinstance(out, dict)
```

- [ ] **Step 2–4: Extract batch A, green tests, commit**

```bash
git commit -m "$(cat <<'EOF'
refactor: extract inventory/endpoints/device/overlay/routing member callables

EOF
)"
```

---

### Task 6: Extract members batch B + compliance stubs

**Files:**
- Create: `cvp_mcp/members/topology.py`, `events.py`, `flow.py`, `probes.py`, `meta.py`, `compliance.py`, `studios.py`
- Create: `cvp_mcp/members/compliance_status.py` — stubs only
- Modify: `cvp_mcp/tool_groups.py`
- Test: stub envelope tests + designed_config still via compliance member

**Status stub contract** (sibling spec):

```python
def config_status(device_id: str) -> dict:
    return tool_envelope(
        data_source="resource_api:configstatus.v1.summary",
        coverage="none",
        items=[],
        warnings=["configstatus_forbidden"],
        obj={"device_id_input": device_id, "hint": "Resource API Summary returned 403 on this tenant"},
    )
```

Same pattern for `image_status` with `imagestatus_forbidden`. Do **not** call the Resource API in this PR unless a 200 is capturable (it is not).

`studios` group description **must** include cross-ref that designed config is `compliance` action `designed_config`.

Attach rate_limit_key on members: `list`→`inventory.list`, `map`→`topology.map`, `search`→`events.search`.

- [ ] **Steps: tests → implement → green → commit**

```bash
git commit -m "$(cat <<'EOF'
refactor: extract remaining read members and compliance status stubs

EOF
)"
```

---

### Task 7: Extract `studios_write` members

**Files:**
- Create: `cvp_mcp/members/studios_write.py`
- Modify: `cvp_mcp/tool_groups.py` — `build_write_group() -> GroupedTool`
- Modify: keep existing write tool tests targeting grpc helpers; add one group dispatch smoke with `confirm` dry-run refusal

**Interfaces:**
- Group description **must** contain: never submits; never approves/executes change controls; dry-run unless `confirm` + matching `preview_token`; drafts only `ws-mcp-*`.
- Members: `create_workspace`, `delete_workspace`, `build`, `set_description`, `set_inputs`, `assign_tags`, `create_studio`, `delete_studio`, `set_mss_inputs` (9).

- [ ] **Steps: extract, thin-wrap existing flats, commit**

```bash
git commit -m "$(cat <<'EOF'
refactor: extract studios_write member callables

EOF
)"
```

---

### Task 8: Hard cut registration

**Files:**
- Modify: `cloudvision_mcp.py` — remove every `@mcp.tool` function; after `mcp = FastMCP(...)`, call:

```python
from cvp_mcp.register_grouped import register_grouped_tool
from cvp_mcp.tool_groups import build_groups, build_write_group
from cvp_mcp.write_access import writes_enabled

for group in build_groups():
    register_grouped_tool(mcp, group)
if writes_enabled():
    register_grouped_tool(mcp, build_write_group())
```

- Create/extend: `tests/test_tool_surface.py` — full surface + help completeness
- Modify: `tests/test_write_registration.py`:

```python
WRITE_TOOLS = {"studios_write"}
# assert WRITE_TOOLS <= names when on; assert not when off; never submit
```

- Modify: `tests/test_lab_device_filter.py`, `tests/test_device_resolve.py` — import from `cvp_mcp.members.topology` (or wherever LLDP body lives)
- Grep: `from cloudvision_mcp import get_cvp_` and fix.

**Help completeness test:**

```python
def test_every_group_help_lists_all_members(monkeypatch):
    # reload writes off; for each tool in list_tools, call action=help via GroupedTool or mcp.call_tool
    # assert set(actions) == expected members for that group
```

- [ ] **Step 1: Surface tests fail (flats still present) — then delete flats and register groups**

- [ ] **Step 2: Full pytest**

Run: `uv run pytest -q`
Expected: PASS (fix any fallout)

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat!: hard-cut MCP catalog to grouped action tools

EOF
)"
```

---

### Task 9: Docs rename (Wave 4)

**Files:**
- Modify: `README.md` — group + `action` tables; Claude allowlist examples → `mcp__cloudvision-mcp__inventory`, `…__studios`, `…__studios_write`, `…__compliance`, etc.
- Modify: `docs/studios-support-spec.md`, `docs/studios-phase2-spec.md`, `docs/studios-phase2-final-spec.md`, `docs/studios-phase2-followon-fix-spec.md`, `docs/studios-phase2-3-mss-root-inputs-spec.md` — replace flat MCP tool names with `group.action` where they document the live surface (M4). Do **not** rewrite historical research dumps under `docs/research/` if present.

- [ ] **Step 1: Grep for leftover flat MCP names in those paths**

Run: `rg -n "get_cvp_|create_cvp_|set_cvp_|assign_cvp_|delete_cvp_|build_cvp_|map_cvp_|search_cvp_" README.md docs/studios-*.md`

- [ ] **Step 2: Update docs, re-grep clean (or only intentional historical mentions with prose “formerly”), commit**

```bash
git commit -m "$(cat <<'EOF'
docs: document grouped MCP tool surface (breaking catalog)

EOF
)"
```

---

### Task 10: Acceptance gate

**Files:** none new — verification only

- [x] **Step 1: Run acceptance checklist** (2026-09-03: 674 passed; ruff, black,
  pre-commit clean)

```bash
uv run pytest -q
uv run pre-commit run --all-files  # if configured; else black + ruff
```

Manual asserts (script or pytest already covering):

- [x] `list_tools` names ∈ 12 or 13; no flat legacy names
- [x] 46 member actions reachable; every `help` complete
- [x] Writes env gate; no submit tool
- [x] Disable + rate-limit keys use `group` / `group.action`
- [x] `inventory.get` dict-valued
- [x] README allowlist examples use new names only
- [x] PR titled/bodied as **breaking** catalog rename

- [x] **Step 2: Commit any leftover fixes; stop for human approve before merge/deploy**
  (PR open; merge and deploy are the human's call — new image tag, restart,
  clients reload MCP tools and update `permissions.allow`)

---

## Mapping: spec waves → plan tasks

| Spec wave | Plan tasks |
| --- | --- |
| 1 skeleton + red surface | Tasks 1–4 |
| 2 always-on + inventory.get | Tasks 5–6, 8 (reads) |
| 3 studios_write | Tasks 7–8 |
| 4 docs | Task 9 |
| 5 status stubs | **Folded into Task 6** (required for 46-member bijection before merge) |

---

## Plan adversarial review (2026-09-02) — applied

Reviewed the first draft of this plan against the consolidation spec, sibling compliance spec, adversarial review C1–M5, and a live probe of MCP SDK FastMCP registration. Updates already folded above:

| ID | Finding | Plan fix |
| --- | --- | --- |
| P1 | opnsense `FunctionTool(parameters=…)` does not exist on `mcp[cli]` FastMCP; mutating `.parameters` alone breaks `call_tool` (Pydantic still requires `kwargs`). | Task 2 normative dynamic-signature + parameters overwrite; called out in Architecture. |
| P2 | Spec Wave 5 after Wave 2 would leave `MEMBER_ACTIONS` at 44 until late; merge bijection needs 46. | Status stubs moved into Task 6 (read compliance), not a trailing wave. |
| P3 | Mid-branch dual registration could merge accidentally. | Global constraint + Task 8 as single hard cut; surface name tests only enforced at Task 8+. |
| P4 | `inventory.get` dict migration underspecified for errors. | Task 5: return the same error/device **objects** previously `json.dumps`’d. |
| P5 | Tests import `cloudvision_mcp.get_cvp_lldp_neighbors`. | Task 8 explicitly retargets those imports to members. |
| P6 | `test_write_registration` still asserts nine flat write names. | Task 8 updates to `{"studios_write"}`. |
| P7 | FastMCP fills omitted optional fields as `None`. | Task 1 strip/`None`-as-absent rules; registration test documents behaviour. |
| P8 | Shared `query` may not be identical across inventory vs events. | Task 1: only share fields with identical meaning; leave `query` per-member if wording differs. |
| P9 | Disable/rate originally deferred vaguely. | Task 3 dedicated; MemberSpec.`rate_limit_key`. |
| P10 | Docstring count string not pinned in code. | Task 4 `DOCSTRING_SURFACE` constant + test. |
| P11 | Extracting bodies into `cvp_mcp.members` while calling `cloudvision_mcp.get_env_vars` / `_resolve_device_serial` would create an import cycle once `cloudvision_mcp` imports `tool_groups`. | Global constraint: members use `cvp_mcp.env` + `cvp_mcp.grpc` only. |
| P12 | Draft referred to “Task 10” for hard cut while hard cut is Task 8. | Renumbered references; acceptance is Task 10. |
| P13 | Private `mcp._tool_manager._tools[…].parameters = …` is brittle across SDK bumps. | Accepted for this release (only workable path without `FunctionTool`); pin comment in `register_grouped.py` citing SDK version constraint `mcp[cli]>=1.29.1,<2`. |

**Spec coverage check:** C1–C3, I1–I8, M1–M5, acceptance bullets, member map, writes gate, disable grammar, rate buckets, designed_config cross-ref, status forbidden stubs, docs Wave 4 — each maps to a task above. No TBD placeholders remain for registration mechanics.

**Out of scope (unchanged):** unlocking configstatus 403; digest fake compliance; renaming grpc helpers off `get_cvp_*`; retiring studio create/delete.
