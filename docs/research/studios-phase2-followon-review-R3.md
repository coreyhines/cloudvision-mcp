# Follow-on R3 — contract vs spec 2.1/2.2

| Spec | Code | Match |
|------|------|-------|
| Writes env exact `"1"` | `write_access.WRITES_ENV` | yes |
| Submit env + field | `SUBMIT_STALENESS_FIELD is None` | yes (unregistered) |
| AssignedTags GET fail-closed | `assigned_tags_unavailable` / coverage none | yes |
| Empty generic path | `root_path_forbidden` | yes |
| Empty tag query | `empty_query_forbidden` | yes |
| StudioConfig POST | allowlist + `studio_crud.py` | yes |
| No shutdown in templates | lint in `studio_crud.py` | yes |
| Submit not an MCP tool | not in `cloudvision_mcp.py` | yes |

## Gaps

- Live `AssignedTags/all` URL still unprobed (spec open item).
- Generic Inputs POST untried against CVaaS (same as 2.0 Inputs POST).
