# Bucket R2 — safety / adversarial review (Phase 2.0)

Status: **done** (findings applied on `feat/studios-phase2-impl`)
Owner: cursor-auto (Task; scheduled ollama-cloud rerouted — week 89% YELLOW)
Scope: write gates, path allowlist, preview tokens, EOS lint, mainline
protection, shutdown/submit leak.

## Attack questions

| Question | Answer after fixes |
| --- | --- |
| Can MCP write mainline (`workspaceId=""`)? | No. `validate_workspace_id` requires `ws-mcp-`, rejects empty and `builtin-`. |
| Can confirm skip preview? | No. Missing/mismatched token → `preview_required`. |
| Can truncated Inputs POST a partial tree? | No. Truncation/skip warnings fail closed. |
| Can extra DELETE params hit another resource? | No. Query names allowlisted. |
| Can description CAS shut a port via `enabled`/`shutdown`? | Lint on the **new** description string; tree-diff must be exactly one `adapterDetails.description` leaf. Pre-existing "reload" elsewhere is not a refuse (lint is on introduced text). Generic Inputs POST is not 2.0. |
| Can MCP emit `REQUEST_SUBMIT`? | Helper allowlists the enum but `submit_enabled()` is false until `SUBMIT_STALENESS_FIELD` is set; submit tool is **not** registered. |
| Can `start`/`schedule` sneak onto WorkspaceConfig? | Envelope denylist on Workspace/Studio config paths. Inputs JSON string may contain those words. |

## Remaining (not 2.0 blockers)

| ID | Severity | Note |
| --- | --- | --- |
| R2-O1 | Important for 2.1 | `AssignedTags/all` still unprobed. Do not ship tag replace. |
| R2-O2 | Important for first live use | InputsConfig POST against CVaaS is unit-mocked only. First live run: dry-run, `ws-mcp-test-*`, build, delete — no submit. |
| R2-O3 | Minor | Homelab MCP must keep `CLOUDVISION_MCP_ALLOW_WRITES` unset until a human enables it on the container. Workstation `~/.env` token (1031) still 401; use container token. |

## Must not regress

- Never `shutdown` / `no shutdown` on a switchport from MCP.
- Never ChangeControlConfig.
- Never register submit in 2.0.
