# Bucket R2 — Phase 1 read tools completeness

Review **Bucket R2 only**. Read-only. Do not edit the spec or product code.

## Context

- **Feature:** Studios support spec review
- **Owner:** cursor-auto
- **Model:** auto
- **Exec:** subagent (readonly)

## Read first

1. `docs/studios-support-spec.md` — Phase 1 — Read tools (through `get_cvp_workspace_build`)
2. Existing MCP envelope: `cvp_mcp/grpc/envelope.py`
3. `cloudvision_mcp.py` — how tools are registered / `tool_enabled`

## Deliverables (ONLY these files)

| File | Purpose |
|------|---------|
| `docs/research/studios-support-review-R2.md` | Findings |

## Review questions

1. Are Phase 1 tools enough to answer “which studio generated this config line”?
2. Missing parameters, response fields, or NDJSON parsing rules for implementers?
3. Does `get_cvp_designed_config` specify enough of the compliance payload/response to code against?
4. Are workspace/build poll tools sufficient as Phase 2 prerequisites?

## Severity

Critical / Important / Minor. Cite spec headings. Suggest concrete spec text, do not implement.

## Do NOT

- Edit the spec or Python
- Review write-tool bodies (R3) or test-plan gaps (R5) except as they block Phase 1

## Sub-agent rules

- Complete **only** bucket R2.
- Edit **only** `docs/research/studios-support-review-R2.md`.
- No commit required.
- Return the Report back block.

## Report back

```
Bucket R2: <success|failed>
Files: docs/research/studios-support-review-R2.md
Notes: <top 3 findings>
```
