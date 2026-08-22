# Bucket 0 — Write gates + preview_token

Implement **Bucket 0 only**.

## Context

- Feature: Studios Phase 2.0
- Integration branch: `feat/studios-phase2-impl`
- Depends: none
- Owner: ollama-local, model `qwen3.8:27b-mlx`

## Read first

1. `docs/studios-phase2-spec.md` — Canonical names, Process/env gates, Write envelope
2. `cvp_mcp/tool_access.py` — existing `tool_enabled` (do not change it)
3. `tests/test_security_helpers.py` — test style

## Deliverables (ONLY these files)

| File | Purpose |
|------|---------|
| `cvp_mcp/write_access.py` | Env gates, workspace id, preview token |
| `tests/test_write_access.py` | Unit tests, no HTTP |

## Implement

```python
# cvp_mcp/write_access.py
WRITES_ENV = "CLOUDVISION_MCP_ALLOW_WRITES"
SUBMIT_ENV = "CLOUDVISION_MCP_ALLOW_SUBMIT"
SUBMIT_STALENESS_FIELD: str | None = None  # None => submit unregistered

def writes_enabled() -> bool:  # os.environ.get(WRITES_ENV, "").strip() == "1"
def submit_enabled() -> bool:  # writes_enabled() and SUBMIT_STALENESS_FIELD and SUBMIT_ENV == "1"
def preview_token(tool_name: str, args: dict) -> str:
    # sha256 of tool_name + "|" + json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    # utf-8, hex digest
def check_preview_token(tool_name, args, token) -> str | None:
    # None/mismatch -> "preview_required"; else None
def validate_workspace_id(workspace_id: str) -> str | None:
    # strip; empty -> workspace_id_required
    # lower startswith builtin- -> builtin_workspace_forbidden
    # not startswith ws-mcp- -> invalid_workspace_id
    # else None (ok). Leading/trailing space stripped before checks.
```

Tests (pytest, monkeypatch):

- unset, `""`, `0`, `true`, `yes` => writes_enabled False; only `"1"` True
- submit_enabled False when field is None even if both envs `"1"`
- preview_token stable for same dict; order-independent
- check_preview_token mismatch
- workspace: `""`, `"  "`, `"Builtin-x"`, `"ws-other"` fail; `"ws-mcp-desc-20260822-aabbccdd"` ok

## Do NOT

- HTTP, MCP tools, `cloudvision_mcp.py`, `studios.py`
- Other buckets

## Verify

```bash
uv run ruff check cvp_mcp/write_access.py tests/test_write_access.py
uv run black cvp_mcp/write_access.py tests/test_write_access.py
uv run pytest tests/test_write_access.py -q
```

## Commit

```
feat(studios): add write env gates and preview_token (bucket 0)
```

## Report back

```
Bucket 0: <success|failed>
Branch: <branch>
Commit: <hash> — <subject>
Tests: <N> passed
Files: ...
Notes: ...
```
