# Live 2.1 failures — analysis (2026-08-22)

Tenant: CVaaS staging (`www.cv-staging.corp.arista.io`). Probe: container token (398 chars) on `cloudvision_mcp:1.59`. MCP writes on, submit off.

## What we ran

MCP tools against Access Interface Configuration (`studio-campus-access-interfaces`) and a throwaway draft `ws-mcp-livetest-20260822a` (created and deleted).

| Call | Error / outcome | HTTP underneath |
|------|----------------|-----------------|
| `get_cvp_studio_assigned_tags` | `assigned_tags_unavailable` | **200** on `GET …/AssignedTags/all` |
| `assign_cvp_studio_tags` (non-empty query) | `assigned_tags_unavailable` | same GET; no POST |
| empty tag query | `empty_query_forbidden` | none (correct) |
| `set_cvp_studio_inputs` `path_values=[]` | `root_path_forbidden` | none (correct per 2.1) |
| `path_values=["campus"]` | `inputs_path_not_found` | Inputs/all **200**; no row with that Resource path |
| Studio create (`shutdown` in template) | `disruptive_content_forbidden` | none (correct) |
| Studio create (harmless template) | `accepted` | StudioConfig POST 200 |
| Generic Inputs on the new studio | `preflight_failed` Studio GET | keyed `GET Studio?key.workspaceId=` **mainline** |
| Studio delete + workspace delete | `accepted` | 200; workspace GET 404 after |

2.0 description CAS still works because it loads **root** Inputs (`path.values []`) and does not use generic Inputs.

## Cause 1 — AssignedTags: empty filter treated as “API missing”

Live `GET /api/resources/studio/v1/AssignedTags/all`:

- Status **200**, NDJSON, **22** `result.value` rows.
- Each row has `key.studioId`, `key.workspaceId`, `query`.
- Unique studios in the stream: `studio-management-connectivity`, `studio-mss-service`, `studio-telemetry-config`, `studio-topology-file-converter`.
- **`studio-campus-access-interfaces` is not in the set.**
- Workspace ids on those rows are UUID drafts, not `""`.

`studio_tags._fetch_assigned_tags` treats **zero client-filter matches** the same as HTTP 404 / `empty_response` (`status="unavailable"`). Assign then refuses because it cannot confirm `expected_current_query`.

This is a **product bug**, not a missing endpoint.

Correct model: no row ⇒ current query is `""` (never assigned). First assign is a POST with `expected_current_query=""`. Empty **new** `query` stays forbidden.

## Cause 2 — Generic Inputs: Resource path ≠ JSON tree

`get_cvp_studio_inputs` on the access studio returns **one** item:

```json
{ "workspace_id": "", "path_values": [], "inputs": { "campus": [ … ] } }
```

`campus` is a **JSON key inside that document**, not `Inputs.key.path.values`.

`set_cvp_studio_inputs` looks up `item.path_values == path_values`. `["campus"]` never matches `[]` → `inputs_path_not_found`.

2.1 spec forbids empty `path_values` so generic Inputs **cannot** edit this studio’s only Resource row. Description edits stay on `set_cvp_access_interface_description`.

The live test used the wrong path kind. The tool also never tells the caller which Resource paths exist.

## Cause 3 — Overlay studio GET uses mainline

`set_cvp_studio_inputs` calls `get_cvp_studio(datadict, studio, "")` (mainline). A studio created only in a draft workspace 404s. `studio_crud` already GETs workspace overlay then mainline. Generic Inputs does not.

## Non-causes

- Token / writes gate: create/delete workspace and studio CRUD succeeded with the same process.
- `AssignedTags/all` is not 404. `tag/v1/*` is 403 (wrong API, ignore).
- NDJSON parser works on this stream (22 values).

## What “fixed” means

1. Tags GET returns `coverage="full"` with `query=""` when `/all` is 200 and this studio has no row.
2. Assign with `expected_current_query=""` POSTs the first query; mismatch still refuses.
3. Generic Inputs returns `available_path_values` on miss; never treat JSON keys as Resource paths.
4. Access studio root remains 2.0 CAS only; generic refuses `[]` with a pointer to that tool.
5. Studio/Inputs preflight GETs the **draft workspace**, then mainline.
