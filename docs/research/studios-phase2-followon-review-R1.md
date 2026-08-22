# Follow-on R1 — defect-first (coordinator; Claude CLI review farm hung)

Date: 2026-08-22. Branch: `feat/studios-phase2-followon`.

## Critical

None found in helpers + wiring: submit is not an MCP tool (`grep submit_cvp_workspace cloudvision_mcp.py` empty). `SUBMIT_STALENESS_FIELD` remains `None`.

## Important

| ID | Issue |
|----|--------|
| R1-I1 | AssignedTags GET is unprobed live; assign refuses when GET is unavailable, so first-ever tag set cannot proceed until `/AssignedTags/all` is confirmed. Fail-closed; document as known. |
| R1-I2 | Generic inputs `path_values` empty is forbidden; 2.0 description CAS still uses a different helper for root POST. Callers must not mix them. |

## Minor

| ID | Issue |
|----|--------|
| R1-M1 | Claude review farms R1–R3 with `READONLY=1` then parallel `&` hung/no-op; coordinator wrote these notes. |
| R1-M2 | Full pytest **505 passed** after merges (2026-08-22). |
