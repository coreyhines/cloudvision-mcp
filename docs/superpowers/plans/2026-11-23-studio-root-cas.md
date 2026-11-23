# Studio Root-CAS Framework + Telemetry Writer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract a shared root-row Studio Inputs CAS library, thin-adapt MSS/Campus onto it without changing their public MCP APIs, and ship `studios_write.set_telemetry_inputs` so agents can draft TerminAttr onboarding-profile changes.

**Architecture:** New `cvp_mcp/grpc/studio_root_cas.py` owns load → digest CAS → scoped diff → preview/confirm POST at `path.values []`. Plugins keep their own op validators/appliers. Telemetry is `ops_only` with a minimal vocabulary. Generic `set_inputs` stays root-refusing.

**Tech Stack:** Python 3.13, existing `cvp_mcp` grpc helpers (`inputs_digest`, `studios_write._load_root_inputs`, `post_resource_config`, `write_access`), pytest, agent-fence allowlist, Podman image `hub.freeblizz.com/cloudvision_mcp`.

**Spec:** `docs/superpowers/specs/2026-11-23-studio-root-cas-design.md`

## Global Constraints

- Draft workspaces only: id must start with `ws-mcp-`.
- Never submit / approve / execute Change Control from MCP.
- Dry-run default; `confirm=True` requires matching `preview_token`.
- CAS via `expected_inputs_sha256` (64-hex from `studios.inputs`).
- Do **not** loosen `studios_write.set_inputs` (`root_path_forbidden`, description allowlist).
- Telemetry v1 mode: **`ops_only`** (no full-document replace).
- Public MCP names for MSS/Campus stay `set_mss_inputs` / `set_campus_svi_inputs` with same required fields and refusal codes.
- Telemetry studio id: `studio-telemetry-config`.
- Writable telemetry top-level keys for diff scope: `onboardingProfiles`, `profileAssignment` only in v1.
- Max 20 operations per call.
- Commits: Conventional Commits; only create git commits when the human asks (plan steps still show suggested messages).
- Format with black; lint with ruff; tests with pytest from repo root / `uv run pytest`.

## File map

| File | Responsibility |
|------|----------------|
| `cvp_mcp/grpc/studio_root_cas.py` | Shared preflight + CAS + scope check + preview/post |
| `cvp_mcp/grpc/studio_mss_inputs.py` | Keep MSS ops; call root_cas for pipeline |
| `cvp_mcp/grpc/studio_campus_svi_inputs.py` | Keep Campus ops; call root_cas for pipeline |
| `cvp_mcp/grpc/studio_telemetry_inputs.py` | Telemetry ops + `set_cvp_telemetry_inputs` |
| `cvp_mcp/members/studios_write.py` | Register `set_telemetry_inputs` MemberSpec |
| `cvp_mcp/tool_groups.py` | Alias map entry if required by consolidation |
| `tests/fixtures/inputs_telemetry_config_root_minimal.json` | Captured/minimal root document |
| `tests/test_studio_root_cas.py` | Core helper unit tests |
| `tests/test_studio_telemetry_inputs.py` | Telemetry writer tests |
| `tests/test_studio_mss_inputs.py` | Must stay green after adapter |
| `tests/test_studio_campus_svi_inputs.py` | Must stay green after adapter |
| `agent-fence/gateway/policy/cloudvision.yaml` | Allowlist `set_telemetry_inputs` |
| `podman/strongpod/cloudvision-mcp/cloudvision-mcp-app.container` | Image pin bump after publish |

---

### Task 1: Shared `studio_root_cas` + MSS thin adapter

**Files:**
- Create: `cvp_mcp/grpc/studio_root_cas.py`
- Modify: `cvp_mcp/grpc/studio_mss_inputs.py` (replace duplicated pipeline after op normalize with core call)
- Test: `tests/test_studio_root_cas.py`
- Regression: `tests/test_studio_mss_inputs.py` (existing)

**Interfaces:**
- Consumes: `_load_root_inputs`, `inputs_sha256`, `canonical_json`, `_changed_leaf_paths`, `post_resource_config`, `preview_token` / `check_preview_token`, `_read_workspace`, `_read_studio_anywhere`, `_credentials`, `_outcome`, `_refused`, `INPUTS_CONFIG_PATH`, `WORKSPACE_STATE_PENDING`
- Produces:
  - `@dataclass RootCasConfig` with fields: `tool_name: str`, `studio_id: str`, `operation: str`, `writable_top_level_keys: tuple[str, ...]`, `scope_error_code: str`, `scope_error_message: str`, `load_error_message: str`, `digest_mismatch_message: str`
  - `def run_root_inputs_cas(datadict: dict[str, Any], workspace_id: str, expected_inputs_sha256: str, *, config: RootCasConfig, operations: list[dict[str, Any]], apply: Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]], confirm: bool = False, preview_token_value: str | None = None, extra_fields: dict[str, Any] | None = None, early_warnings: list[str] | None = None) -> dict[str, Any]`
  - `apply` returns `(after_document, entry_summary, error_dict_or_None)` where `entry_summary` has keys `added`, `replaced`, `removed` (lists of strings; may be empty)

- [ ] **Step 1: Write failing test for digest mismatch path**

Create `tests/test_studio_root_cas.py`:

```python
"""Unit tests for shared studio root Inputs CAS."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from cvp_mcp.grpc import studio_root_cas as root_cas
from cvp_mcp.grpc.inputs_digest import inputs_sha256
from cvp_mcp.write_access import WRITES_ENV

DOC = {"onboardingProfiles": [], "profileAssignment": []}
SHA = inputs_sha256(DOC)
CFG = root_cas.RootCasConfig(
    tool_name="set_cvp_test_root_inputs",
    studio_id="studio-test-root",
    operation="set_test_root_inputs",
    writable_top_level_keys=("onboardingProfiles", "profileAssignment"),
    scope_error_code="tree_diff_outside_test_scope",
    scope_error_message="outside writable keys",
    load_error_message="Could not read test root Inputs.",
    digest_mismatch_message="digest mismatch",
)
DATADICT = {"cvtoken": "t", "cvp": "cvp.example.com", "cert": None}
WS = "ws-mcp-root-cas-test"


@pytest.fixture(autouse=True)
def _writes_on(monkeypatch):
    monkeypatch.setenv(WRITES_ENV, "1")


def _apply_noop(document):
    return document, {"added": [], "replaced": [], "removed": []}, None


def test_digest_mismatch_refuses_without_post():
    with (
        patch.object(root_cas, "_read_workspace", return_value=({"state": "WORKSPACE_STATE_PENDING"}, "ok", [])),
        patch.object(
            root_cas,
            "_read_studio_anywhere",
            return_value=({"immutable": None, "from_package": None}, "", None, []),
        ),
        patch.object(root_cas, "_load_root_inputs", return_value=(DOC, "", None, [])),
        patch.object(root_cas, "_credentials", return_value=("t", "https://cvp", None)),
        patch("cvp_mcp.grpc.studio_root_cas.post_resource_config") as post,
    ):
        env = root_cas.run_root_inputs_cas(
            DATADICT,
            WS,
            "0" * 64,
            config=CFG,
            operations=[],
            apply=_apply_noop,
        )
    assert env["object"]["error"]["code"] == "inputs_digest_mismatch"
    assert post.call_count == 0
```

Adjust import/patch targets to match how the module re-exports helpers (prefer importing `_load_root_inputs` into `studio_root_cas` from `studios_write` and patching `cvp_mcp.grpc.studio_root_cas._load_root_inputs`).

- [ ] **Step 2: Run test — expect fail (module missing)**

```bash
cd /Users/corey/code/cloudvision-mcp && uv run pytest tests/test_studio_root_cas.py::test_digest_mismatch_refuses_without_post -v
```

Expected: `ImportError` or `AttributeError` for `studio_root_cas`.

- [ ] **Step 3: Implement minimal `studio_root_cas.py`**

Implement `RootCasConfig` and `run_root_inputs_cas` by lifting the control flow from `set_cvp_mss_policy_inputs` after op normalization (workspace pending → studio GET → load root → digest CAS → `apply` → scope via writable keys → preview/confirm/post). Scope check: a changed leaf path is in-scope if any segment matches `$.{key}` or `$.{key}[` for `key` in `writable_top_level_keys`.

Do **not** put MSS-specific op validation in this module.

- [ ] **Step 4: Make digest-mismatch test pass; add preview happy-path test**

```python
def test_preview_apply_posts_nothing():
    def apply(document):
        after = dict(document)
        after["onboardingProfiles"] = [{"profileName": "x"}]
        return after, {"added": ["profile:x"], "replaced": [], "removed": []}, None

    with (
        patch.object(root_cas, "_read_workspace", return_value=({"state": "WORKSPACE_STATE_PENDING"}, "ok", [])),
        patch.object(
            root_cas,
            "_read_studio_anywhere",
            return_value=({"immutable": None, "from_package": None}, "", None, []),
        ),
        patch.object(root_cas, "_load_root_inputs", return_value=(DOC, "", None, [])),
        patch.object(root_cas, "_credentials", return_value=("t", "https://cvp", None)),
        patch("cvp_mcp.grpc.studio_root_cas.post_resource_config") as post,
    ):
        env = root_cas.run_root_inputs_cas(
            DATADICT,
            WS,
            SHA,
            config=CFG,
            operations=[{"op": "noop"}],
            apply=apply,
        )
    assert env["object"]["outcome"] == "preview"
    assert "preview_token" in env["object"]
    assert post.call_count == 0
```

- [ ] **Step 5: Refactor MSS to call `run_root_inputs_cas`**

In `set_cvp_mss_policy_inputs`, keep writes gate, workspace id check, digest format check, op normalize, disruptive lint, then:

```python
def _apply(document):
    after, entry_summary, apply_error = _apply_operations(document, ops)
    if apply_error:
        return document, {"added": [], "replaced": [], "removed": []}, apply_error
    touched = set(entry_summary["added"]) | set(entry_summary["replaced"])
    result_error, result_warnings = _check_result(after, touched)
    if result_error:
        return document, entry_summary, result_error
    # stash result_warnings via closure list if needed
    return after, entry_summary, None

return run_root_inputs_cas(
    datadict,
    workspace,
    expected_inputs_sha256,
    config=RootCasConfig(
        tool_name=TOOL_NAME,
        studio_id=MSS_STUDIO_ID,
        operation="set_mss_policy_inputs",
        writable_top_level_keys=WRITABLE_COLLECTIONS,
        scope_error_code="tree_diff_outside_mss_scope",
        scope_error_message="The applied operations changed leaves outside the writable MSS collections.",
        load_error_message="Could not read the MSS Service root Inputs document.",
        digest_mismatch_message="The current MSS Service document does not match expected_inputs_sha256; re-read and retry.",
    ),
    operations=ops,
    apply=_apply,
    confirm=confirm,
    preview_token_value=preview_token_value,
)
```

Preserve MSS refusal codes and envelope field names (`entries_added`, etc.).

- [ ] **Step 6: Run tests**

```bash
cd /Users/corey/code/cloudvision-mcp && uv run pytest tests/test_studio_root_cas.py tests/test_studio_mss_inputs.py -v
```

Expected: all PASS.

- [ ] **Step 7: Commit (when human asks)**

```bash
git add cvp_mcp/grpc/studio_root_cas.py cvp_mcp/grpc/studio_mss_inputs.py tests/test_studio_root_cas.py
git commit -m "$(cat <<'EOF'
feat(studios): extract shared root Inputs CAS helper

EOF
)"
```

---

### Task 2: Campus SVI thin adapter

**Files:**
- Modify: `cvp_mcp/grpc/studio_campus_svi_inputs.py`
- Test: `tests/test_studio_campus_svi_inputs.py` (existing; must stay green)

**Interfaces:**
- Consumes: `run_root_inputs_cas`, `RootCasConfig` from Task 1
- Produces: unchanged `set_cvp_campus_svi_inputs(...)` signature and refusal codes

- [ ] **Step 1: Refactor Campus entrypoint onto `run_root_inputs_cas`**

Keep Campus-specific op normalize, campus/pod tag targeting, and SVI apply. Pass `writable_top_level_keys=("campusServices",)` (or the exact top-level key the scoped diff already used — verify against current `_scope_violations` in that file before changing).

- [ ] **Step 2: Run Campus tests**

```bash
cd /Users/corey/code/cloudvision-mcp && uv run pytest tests/test_studio_campus_svi_inputs.py -v
```

Expected: all PASS.

- [ ] **Step 3: Commit (when human asks)**

```bash
git add cvp_mcp/grpc/studio_campus_svi_inputs.py
git commit -m "$(cat <<'EOF'
refactor(studios): route campus SVI writer through root CAS helper

EOF
)"
```

---

### Task 3: Telemetry fixture + pure op apply

**Files:**
- Create: `tests/fixtures/inputs_telemetry_config_root_minimal.json`
- Create: `cvp_mcp/grpc/studio_telemetry_inputs.py` (apply/validate only first)
- Test: `tests/test_studio_telemetry_inputs.py`

**Interfaces:**
- Consumes: live shape from design + fixture
- Produces:
  - `TELEMETRY_STUDIO_ID = "studio-telemetry-config"`
  - `WRITABLE_TOP_LEVEL_KEYS = ("onboardingProfiles", "profileAssignment")`
  - `def normalize_operations(operations: Any) -> tuple[list[dict[str, Any]], dict[str, Any] | None]`
  - `def apply_operations(document: dict[str, Any], ops: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]`

- [ ] **Step 1: Capture fixture**

```bash
# Via cloudvision MCP (or container token curl). Save root `inputs` object only.
# Redact nothing required (paths are not secrets). Keep campus-1709 profile + one assignment row.
```

Write `tests/fixtures/inputs_telemetry_config_root_minimal.json`:

```json
{
  "inputs": {
    "cluster_profiles": [],
    "customFlags": [],
    "device_configuration_resolver": [],
    "disableAAAResolver": [],
    "multi_cluster": false,
    "onboardingProfiles": [
      {
        "cluster_profiles": [
          {
            "cvopt_cvaddr_group": {"addr1": "apiserver.cv-staging.corp.arista.io"},
            "cvopt_cvaddr_v6_group": {},
            "cvopt_name": "cvaas",
            "ingestPort": 443
          }
        ],
        "customFlags": {},
        "dev_collection": [
          {
            "cluster_profile_name": "cvaas",
            "cvopt_auth_token": null,
            "cvopt_auth_type": "token-secure",
            "cvopt_proxy": null,
            "cvopt_sourceintf": null,
            "cvopt_vrf": null
          }
        ],
        "disableAAAResolver": {"disableAAA": true},
        "multi_cluster": true,
        "profileName": "campus-1709",
        "select_active_cluster": {},
        "sourceInterfaceAndVrf": {}
      }
    ],
    "profileAssignment": [
      {
        "inputs": {
          "profile_assignment_group": {
            "default_config": true,
            "onboardingProfile": "campus-1709"
          }
        },
        "tags": {"query": "Campus:campus-1709"}
      }
    ],
    "sourceVrfAssignment": [],
    "vrfAssignment": []
  }
}
```

Prefer replacing this with a real captured document if field names differ.

- [ ] **Step 2: Failing tests for ops**

```python
def test_patch_sets_auth_token_path():
    from cvp_mcp.grpc import studio_telemetry_inputs as tel
    doc = json.loads(FIXTURE_PATH.read_text())["inputs"]
    ops, err = tel.normalize_operations(
        [
            {
                "op": "patch_onboarding_profile",
                "profileName": "campus-1709",
                "patch": {"cvopt_auth_token": "/tmp/cv-onboarding-token"},
            }
        ]
    )
    assert err is None
    after, summary, apply_err = tel.apply_operations(doc, ops)
    assert apply_err is None
    prof = next(p for p in after["onboardingProfiles"] if p["profileName"] == "campus-1709")
    assert prof["dev_collection"][0]["cvopt_auth_token"] == "/tmp/cv-onboarding-token"


def test_upsert_profile_and_assign_device():
    from cvp_mcp.grpc import studio_telemetry_inputs as tel
    doc = json.loads(FIXTURE_PATH.read_text())["inputs"]
    ops, err = tel.normalize_operations(
        [
            {
                "op": "upsert_onboarding_profile",
                "profile": {
                    "profileName": "staging-mgmt-ztx",
                    "multi_cluster": False,
                    "disableAAAResolver": {"disableAAA": True},
                    "cluster_profiles": [
                        {
                            "cvopt_name": "cvaas",
                            "ingestPort": 443,
                            "cvopt_cvaddr_group": {
                                "addr1": "apiserver.cv-staging.corp.arista.io"
                            },
                            "cvopt_cvaddr_v6_group": {},
                        }
                    ],
                    "dev_collection": [
                        {
                            "cluster_profile_name": "cvaas",
                            "cvopt_auth_token": "/tmp/cv-onboarding-token",
                            "cvopt_auth_type": "token-secure",
                            "cvopt_vrf": "mgmt",
                            "cvopt_proxy": None,
                            "cvopt_sourceintf": None,
                        }
                    ],
                    "customFlags": {},
                    "select_active_cluster": {},
                    "sourceInterfaceAndVrf": {"source_vrf": "mgmt"},
                },
            },
            {
                "op": "set_profile_assignment",
                "query": "device:WTW23350044",
                "onboardingProfile": "staging-mgmt-ztx",
                "default_config": True,
            },
        ]
    )
    assert err is None
    after, summary, apply_err = tel.apply_operations(doc, ops)
    assert apply_err is None
    names = [p["profileName"] for p in after["onboardingProfiles"]]
    assert "staging-mgmt-ztx" in names
    queries = [r["tags"]["query"] for r in after["profileAssignment"]]
    assert "device:WTW23350044" in queries
```

- [ ] **Step 3: Run — expect fail**

```bash
cd /Users/corey/code/cloudvision-mcp && uv run pytest tests/test_studio_telemetry_inputs.py -v
```

- [ ] **Step 4: Implement normalize + apply in `studio_telemetry_inputs.py`**

Ops: `upsert_onboarding_profile`, `remove_onboarding_profile`, `set_profile_assignment`, `patch_onboarding_profile`.
`patch` allowed keys: `cvopt_auth_token`, `cvopt_vrf` (writes `dev_collection[0].cvopt_vrf`), `disableAAA` (writes `disableAAAResolver.disableAAA`), `multi_cluster`, `addr1` (writes first cluster_profiles `cvopt_cvaddr_group.addr1`), `ingestPort`.
Refuse unknown op / unknown patch key / missing profile with stable codes: `telemetry_operation_invalid`, `telemetry_profile_not_found`, `telemetry_operations_required`, `telemetry_operations_too_many`.

- [ ] **Step 5: Run tests — PASS**

```bash
cd /Users/corey/code/cloudvision-mcp && uv run pytest tests/test_studio_telemetry_inputs.py -v
```

- [ ] **Step 6: Commit (when human asks)**

```bash
git add cvp_mcp/grpc/studio_telemetry_inputs.py tests/test_studio_telemetry_inputs.py tests/fixtures/inputs_telemetry_config_root_minimal.json
git commit -m "$(cat <<'EOF'
feat(studios): add telemetry onboarding-profile op apply

EOF
)"
```

---

### Task 4: Wire `set_cvp_telemetry_inputs` through root CAS

**Files:**
- Modify: `cvp_mcp/grpc/studio_telemetry_inputs.py`
- Modify: `tests/test_studio_telemetry_inputs.py`

**Interfaces:**
- Produces: `def set_cvp_telemetry_inputs(datadict, workspace_id, expected_inputs_sha256, operations, confirm=False, *, preview_token_value=None) -> dict`

- [ ] **Step 1: Add mocked preview/confirm tests** (mirror Campus test harness: patch workspace/studio/inputs/urlopen)

```python
def test_preview_patch_token_path():
    # load fixture DOC + SHA; mock pending workspace + studio + root row
    env = tel.set_cvp_telemetry_inputs(
        DATADICT,
        WORKSPACE,
        PRE_SHA,
        [
            {
                "op": "patch_onboarding_profile",
                "profileName": "campus-1709",
                "patch": {"cvopt_auth_token": "/tmp/cv-onboarding-token"},
            }
        ],
    )
    assert env["object"]["outcome"] == "preview"
    assert env["object"]["posted_at_root"] is True
    after = json.loads(env["object"]["request_body"]["inputs"])
    assert (
        after["onboardingProfiles"][0]["dev_collection"][0]["cvopt_auth_token"]
        == "/tmp/cv-onboarding-token"
    )
```

Also test: scope violation if apply somehow touches `customFlags`; digest mismatch; confirm without token refused.

- [ ] **Step 2: Implement `set_cvp_telemetry_inputs` via `run_root_inputs_cas`**

`TOOL_NAME = "set_cvp_telemetry_inputs"`. Run disruptive lint on canonical ops JSON before HTTP (same as MSS).

- [ ] **Step 3: pytest PASS**

```bash
cd /Users/corey/code/cloudvision-mcp && uv run pytest tests/test_studio_telemetry_inputs.py tests/test_studio_root_cas.py -v
```

- [ ] **Step 4: Commit (when human asks)**

```bash
git add cvp_mcp/grpc/studio_telemetry_inputs.py tests/test_studio_telemetry_inputs.py
git commit -m "$(cat <<'EOF'
feat(studios): CAS-write telemetry studio inputs at root

EOF
)"
```

---

### Task 5: MCP registration

**Files:**
- Modify: `cvp_mcp/members/studios_write.py`
- Modify: `cvp_mcp/tool_groups.py` (add alias if other set_* have aliases)
- Test: `tests/test_grouped_tool.py` (extend help/action list assertions if present)

**Interfaces:**
- Produces MCP action `studios_write.set_telemetry_inputs` with required `workspace_id`, `expected_inputs_sha256`, `operations`

- [ ] **Step 1: Add member wrapper + MemberSpec**

```python
def studios_write_set_telemetry_inputs(
    workspace_id: str,
    expected_inputs_sha256: str,
    operations: list[dict],
    confirm: bool = False,
    preview_token: str | None = None,
) -> dict:
    return _write_call(
        "set_telemetry_inputs_failed",
        "set_cvp_telemetry_inputs",
        grpc_set_telemetry_inputs,
        workspace_id,
        expected_inputs_sha256,
        operations,
        confirm=confirm,
        preview_token_value=preview_token,
    )
```

Register beside `set_campus_svi_inputs` with description listing the four ops.

- [ ] **Step 2: Update grouped-tool tests / help snapshot if they enumerate actions**

```bash
cd /Users/corey/code/cloudvision-mcp && uv run pytest tests/test_grouped_tool.py -v
```

- [ ] **Step 3: black + ruff**

```bash
cd /Users/corey/code/cloudvision-mcp && uv run black cvp_mcp/grpc/studio_root_cas.py cvp_mcp/grpc/studio_telemetry_inputs.py cvp_mcp/grpc/studio_mss_inputs.py cvp_mcp/grpc/studio_campus_svi_inputs.py cvp_mcp/members/studios_write.py tests/test_studio_root_cas.py tests/test_studio_telemetry_inputs.py && uv run ruff check cvp_mcp/grpc/studio_root_cas.py cvp_mcp/grpc/studio_telemetry_inputs.py cvp_mcp/members/studios_write.py
```

- [ ] **Step 4: Commit (when human asks)**

```bash
git add cvp_mcp/members/studios_write.py cvp_mcp/tool_groups.py tests/test_grouped_tool.py
git commit -m "$(cat <<'EOF'
feat(mcp): expose studios_write.set_telemetry_inputs

EOF
)"
```

---

### Task 6: Agent-fence allowlist + image pin notes

**Files:**
- Modify: `/Users/corey/code/agent-fence/gateway/policy/cloudvision.yaml` (add `set_telemetry_inputs` next to `set_mss_inputs`)
- Regenerate / update rendered `gateway/config.yaml` if that repo’s process requires it (follow existing `set_campus_svi_inputs` addition pattern in git history)
- Modify: `/Users/corey/code/podman/strongpod/cloudvision-mcp/cloudvision-mcp-app.container` — bump `Image=` **after** image publish (do not invent a version; use next semver after current `1.76`, e.g. `1.77`, once built)

- [ ] **Step 1: Add allowlist entry**

In `policy/cloudvision.yaml` under `studios_write` actions, add:

```yaml
- set_telemetry_inputs
```

- [ ] **Step 2: Rebuild/render gateway config if required by agent-fence README**

Follow the same commands used when `set_campus_svi_inputs` was added.

- [ ] **Step 3: Document deploy sequence in plan completion notes (do not invent secrets)**

```text
1. cloudvision-mcp: build+push hub.freeblizz.com/cloudvision_mcp:<new>
2. podman pin Image= to <new>; merge/deploy strongpod
3. agent-fence policy merge/deploy
4. Reload Cursor cloudvision MCP
```

- [ ] **Step 4: Commits in respective repos when human asks**

---

### Task 7: Live dry-run acceptance (no submit)

**Systems:** cloudvision MCP against `www.cv-staging.corp.arista.io`

- [ ] **Step 1: Read inputs sha**

```text
studios action=inputs studio_id=studio-telemetry-config
```

Record `inputs_sha256`.

- [ ] **Step 2: Ensure draft workspace**

Reuse `ws-mcp-ztx-terminattr-20261123` if still `PENDING`, else create a new `ws-mcp-ztx-terminattr-*`.

- [ ] **Step 3: Dry-run upsert + assign**

```text
studios_write action=set_telemetry_inputs
  workspace_id=...
  expected_inputs_sha256=...
  operations=[
    {op: upsert_onboarding_profile, profile: {...staging-mgmt-ztx...}},
    {op: set_profile_assignment, query: "device:WTW23350044", onboardingProfile: "staging-mgmt-ztx", default_config: true}
  ]
  confirm=false
```

Expected: `outcome=preview`, `posted_at_root=true`, request_body shows token path `/tmp/cv-onboarding-token`.

- [ ] **Step 4: Confirm write (still no submit)**

Re-call with `confirm=true` + `preview_token`. Expected: `outcome=accepted`.

- [ ] **Step 5: Build only**

```text
studios_write action=build workspace_id=...
```

Expected: build starts. **Stop.** Human reviews UI / CC. Do not submit from MCP.

- [ ] **Step 6: Record result in Stash** (optional): workspace id, sha before/after, profile name.

---

## Spec coverage self-review

| Spec requirement | Task |
|------------------|------|
| Shared root-CAS core | Task 1 |
| Thin adapt MSS | Task 1 |
| Thin adapt Campus | Task 2 |
| Hybrid mode framework (`ops_only` / `document_ok`) | Task 1 config; telemetry ops_only; document_ok not implemented (explicit non-goal v1) |
| `set_telemetry_inputs` minimal ops | Tasks 3–5 |
| ztx acceptance path | Task 7 |
| No generic set_inputs loosening | Global Constraints |
| No submit | Global Constraints + Task 7 stop |
| Agent-fence + image | Task 6 |
| Tests + fixture | Tasks 1–5 |

**Placeholders:** none intentional. Fixture may be replaced with live capture in Task 3 Step 1 if schema drift appears.

**Type consistency:** `run_root_inputs_cas` + `RootCasConfig` + `apply -> (after, entry_summary, error)` used uniformly in Tasks 1–4.
