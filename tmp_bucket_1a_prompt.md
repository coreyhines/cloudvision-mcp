# Bucket 1a — Resource POST/DELETE helper

Implement **Bucket 1a only**.

## Context

- Feature: Studios Phase 2.0
- Integration branch: `feat/studios-phase2-impl`
- Depends: none (do not import write_access for path allowlist; you MAY import `submit_enabled` for REQUEST_SUBMIT)
- Owner: Claude CLI opus

## Read first

1. `docs/studios-phase2-spec.md` — HTTP helper, path allowlist, request allowlist
2. `cvp_mcp/grpc/uri_fetch.py` — host allowlist + bearer GET pattern (`_check_uri_allowed`)
3. `cvp_mcp/env.py` if needed for token

## Deliverables (ONLY these files)

| File | Purpose |
|------|---------|
| `cvp_mcp/grpc/resource_write.py` | POST/DELETE with allowlists; **no HTTP if refused** |
| `tests/test_resource_write.py` | Unit tests; mock urlopen |

## Implement

Allowed paths (exact, no query string):

- `/api/resources/workspace/v1/WorkspaceConfig`
- `/api/resources/studio/v1/InputsConfig`
- `/api/resources/studio/v1/AssignedTagsConfig`
- `/api/resources/studio/v1/StudioConfig`

`post_resource_config(base_url, path, body, token, *, cafile=None, cvp_endpoint=None) -> tuple[dict|None, str|None]`

Before any HTTP:

1. path must be in allowlist (path only; caller passes path without `?`)
2. if `body` has `request` (or `Request`): value in `{REQUEST_START_BUILD, REQUEST_SUBMIT}` only; any other string → error, no HTTP
3. if request is REQUEST_SUBMIT: import `submit_enabled` from `cvp_mcp.write_access` if that module exists; if import fails or submit_enabled is False → `submit_disabled`, no HTTP. If write_access is missing (bucket 0 not merged), treat SUBMIT as `submit_disabled`.
4. For paths WorkspaceConfig and StudioConfig only: reject if top-level or `requestParams`/`request_params` dict contains keys `start` or `schedule` (case-insensitive). Do **not** recurse into a string field `inputs`.
5. `key.workspaceId` or `key.workspace_id` after strip non-empty; else `workspace_id_required`
6. Host allowlist via existing uri helper

POST JSON, Authorization Bearer. Return parsed object or (None, err).

`delete_resource_config(base_url, path, params: dict, token, ...)`:

- path exact allowlist (WorkspaceConfig)
- URL-encode params; reject param values containing `?`, `&`, `#` → `invalid_workspace_id`
- `key.workspaceId` in params non-empty

Tests with unittest.mock.patch urllib.request.urlopen:

- bad path never calls urlopen
- REQUEST_ROLLBACK / unknown request no HTTP
- InputsConfig body `{"key":{...},"inputs":"{\"change\":1}"}` allowed (string contains change)
- WorkspaceConfig body `{"key":{...},"start":true}` rejected
- empty workspaceId no HTTP
- delete with `&` in id no HTTP

## Do NOT

- Description CAS, MCP `@mcp.tool`, `cloudvision_mcp.py`

## Verify

```bash
uv run ruff check cvp_mcp/grpc/resource_write.py tests/test_resource_write.py
uv run black cvp_mcp/grpc/resource_write.py tests/test_resource_write.py
uv run pytest tests/test_resource_write.py -q
```

## Commit

```
feat(studios): add allowlisted Resource API write helper (bucket 1a)
```

## Report back

```
Bucket 1a: <success|failed>
Branch: <branch>
Commit: <hash> — <subject>
Tests: <N> passed
Files: ...
Notes: ...
```
