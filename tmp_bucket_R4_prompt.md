# Bucket R4 — Caller information, gates, dry-run/confirm/submit

Review **Bucket R4 only**. Read-only. Do not edit the spec or product code.

## Context

- **Feature:** Studios support spec review
- **Owner:** codex-default
- **Model:** gpt-5.6-sol
- **Exec:** codex-cli farm

## Read first

1. `docs/studios-support-spec.md` — Global write gates; Phase 2 information callers must provide; submit extra gate
2. `cloudvision_mcp.py` — existing `tool_enabled` / env patterns if present

## Deliverables (ONLY these files)

| File | Purpose |
|------|---------|
| `docs/research/studios-support-review-R4.md` | Findings |

## Review questions

1. Is the information table complete for an agent to draft a workspace without guessing schema?
2. Are `CLOUDVISION_MCP_ALLOW_WRITES`, `confirm`, dry-run, `allow_submit`, and “no compound tools” specified tightly enough to implement without loopholes?
3. What should each write tool return on dry-run vs success vs async accept (submit 200 ≠ done)?
4. Missing IDs: `request_id` uniqueness, `workspace_id` collision check, `cc_ids` polling?

## Severity

Critical / Important / Minor. Propose exact parameter defaults.

## Do NOT

- Edit spec or Python
- Redesign the Arista REST sequence (R3)

## Report back

```
Bucket R4: <success|failed>
Files: docs/research/studios-support-review-R4.md
Notes: <top 3 findings>
```
