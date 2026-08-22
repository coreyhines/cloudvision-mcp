# Bucket R5 — Testing, contradictions, open questions

Review **Bucket R5 only**. Read-only. Do not edit the spec or product code.

## Context

- **Feature:** Studios support spec review
- **Owner:** cursor-named-sonnet
- **Model:** sonnet (Cursor picker — user asked for multiple models)
- **Exec:** subagent (readonly)

## Read first

1. Entire `docs/studios-support-spec.md` (look for stale sentences vs later sections)
2. `docs/research/studios-support-review-buckets.md` — out of scope / open questions
3. Existing tests under `tests/` for envelope / URI allowlist patterns to compare against the Phase 1 testing section

## Deliverables (ONLY these files)

| File | Purpose |
|------|---------|
| `docs/research/studios-support-review-R5.md` | Findings |

## Review questions

1. Internal contradictions (role-grant leftover vs dual-API conclusion, “blocked on 403” vs compliance 200, etc.)
2. Phase 1 + Phase 2 test plans — missing fixtures, live-test safety (`ws-mcp-test-*`, no submit)?
3. Open questions — which are blockers vs nice-to-have?
4. Tool inventory table vs body of the spec — mismatches?

## Severity

Critical / Important / Minor. Quote conflicting sentences.

## Do NOT

- Edit the spec or Python
- Duplicate R2/R3/R4 except to flag cross-section inconsistency

## Sub-agent rules

- Complete **only** bucket R5.
- Edit **only** `docs/research/studios-support-review-R5.md`.
- No commit required.

## Report back

```
Bucket R5: <success|failed>
Files: docs/research/studios-support-review-R5.md
Notes: <top 3 findings>
```
