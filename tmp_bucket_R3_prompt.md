# Bucket R3 — Phase 2 writes vs Arista REST + EOS safety

Review **Bucket R3 only**. Read-only. Do not edit the spec or product code.

## Context

- **Feature:** Studios support spec review
- **Owner:** claude-opus
- **Model:** opus
- **Exec:** claude-cli farm (`farm_claude_bucket.sh`, ask/review)

## Read first

1. `docs/studios-support-spec.md` — Canonical provisioning workflow + Phase 2 tool reference
2. Arista REST examples (if reachable): https://aristanetworks.github.io/cloudvision-apis/examples/REST/studios%20and%20workspaces
3. Homelab rule: CloudVision Studios → Workspace → review → Change Control — never ad-hoc EOS `configure` as the normal path; never shutdown ports unless explicitly asked.

## Deliverables (ONLY these files)

| File | Purpose |
|------|---------|
| `docs/research/studios-support-review-R3.md` | Findings |

## Review questions

1. Does the documented create → inputs → tags → build → review → submit sequence match Arista’s REST examples?
2. Are POST bodies complete (especially InputsConfig JSON-string `inputs`, AssignedTagsConfig query, REQUEST_START_BUILD / REQUEST_SUBMIT)?
3. Safety: is CC execute correctly excluded? Any path that could push running-config from MCP?
4. Builtin workspace protection (`^builtin-`) — enough? Other dangerous IDs?
5. Delete-studio unassign-then-remove — missing steps?

## Severity

Critical / Important / Minor. Prefer spec patches over architecture essays.

## Do NOT

- Edit spec or Python
- Implement execute/approve CC
- Review MCP env-gate mechanics in depth (R4)

## Report back

```
Bucket R3: <success|failed>
Files: docs/research/studios-support-review-R3.md
Notes: <top 3 findings>
```
