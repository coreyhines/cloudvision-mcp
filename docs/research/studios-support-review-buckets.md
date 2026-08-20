# Studios support spec — multi-model review (bucketize output)

Generated from: `docs/studios-support-spec.md`
Integration branch: `feat/studios-support-spec` _(findings only; no product-code merge)_
Coordination skill: Parallel Buckets (Cursor adapter)

## Approval status

| Field | Value |
|-------|-------|
| Status | **`approved`** (scope: all, granted: 2026-08-20T03:48Z) |
| Approved by | user (AskQuestion: Approve schedule) |
| Approved waves | Wave 1 + Wave 2 |
| Notes | Read-only spec review. No product-code edits. Wave 1 is five parallel reviewers; Wave 2 is coordinator synthesis. |

**Do not farm or dispatch until status is `approved` or `approved_wave_N`.**

## Bucket sizing summary

| Metric | Value |
|--------|-------|
| Total buckets | 6 (R1–R5 review + RS synthesis) |
| Parallel wave 1 | R1 ∥ R2 ∥ R3 ∥ R4 ∥ R5 |
| Wave 2 | RS (inline coordinator) |
| Claude buckets | 1 (R3 claude-opus) |
| Ollama-local buckets | 1 (R1) |
| Cursor buckets | 2 (R2 cursor-auto, R5 cursor-named-sonnet) |
| Codex buckets | 1 (R4 gpt-5.6-sol) |
| Cursor sub-agent buckets | 2 |
| Cursor inline buckets | 1 (RS) |
| Estimated phases | 2 |

## Session status (update each wave)

| Field | Value |
|-------|-------|
| **Level** | **YELLOW** (Claude OAuth usage unreadable; Cursor GREEN 6% total / 10% API) |
| **Updated** | 2026-08-19 22:47 CDT |
| **Claude session / week %** | unavailable (`no_oauth_token`; burnbar anthropic not logged in) |
| **Cursor total / API %** | 6% / 10% (burnbar, live) |
| **Codex / ChatGPT Plus week %** | 0% (`chatgpt.json`) |
| **Ollama Cloud** | session 0%, week 26% |
| **Capacity probe** | `burnbar_primary` |
| **Before snapshot** | `docs/research/pb-sessions/studios-support-review/before.json` |
| **Note** | Ollama local scan was OK at catalog time; burnbar later reported 127.0.0.1:11434 unreachable. Re-probe before farming R1. Workstation `morpheus` DNS fails. |

## Bucket registry (schedule)

| Wave | ID | Title | Profile | Anthropic | Owner | Backend | Model | Exec | Files (own) | Depends on | Fits one session? |
|------|-----|-------|---------|-----------|-------|---------|-------|------|-------------|------------|-------------------|
| 1 | R1 | API facts, dual config APIs, token/auth | `pure_logic` | opus | ollama-local | ollama-local | qwen3.8:27b-mlx | — | `docs/research/studios-support-review-R1.md` | — | yes |
| 1 | R2 | Phase 1 read tools completeness | `read_tools` | none | cursor-auto | cursor-auto | auto | **subagent** | `docs/research/studios-support-review-R2.md` | — | yes |
| 1 | R3 | Phase 2 writes vs Arista REST + EOS safety | `pure_logic` | **opus** | claude-opus | claude-cli | opus | — | `docs/research/studios-support-review-R3.md` | — | yes |
| 1 | R4 | Caller info, gates, dry-run/confirm/submit | `pure_logic` | none | codex-default | codex-cli | gpt-5.6-sol | — | `docs/research/studios-support-review-R4.md` | — | yes |
| 1 | R5 | Testing, contradictions, open questions | `pure_logic` | none | cursor-named-sonnet | cursor-cli | sonnet | **subagent** | `docs/research/studios-support-review-R5.md` | — | yes |
| 2 | RS | Synthesis + recommended spec edits | `integration_merge` | none | coordinator (Cursor) | inline | auto | **inline** | `docs/research/studios-support-review-synthesis.md` | R1–R5 | yes |

**Probe overrides**

- **R1 Model:** `--distribute` returned `qwen3.6:35b-a3b-mxfp8` (env default). Schedule uses **`qwen3.8:27b-mlx`** from `models.choices` (user pick).
- **R1 thinking:** Ollama review with `"think": false` (skill default for read-only review).
- **R5:** probe placed this on `cursor-named-sonnet` (`api_metered`, Cursor API 10%). Acceptable at GREEN; reply *Edit R5 → cursor-auto* to keep it on the included pool.

## Merge order

```text
(R1 ∥ R2 ∥ R3 ∥ R4 ∥ R5) → RS synthesis (inline)
```

No product-code merge. Findings files are independent; RS is the only consumer.

## File ownership map

| File | Bucket | Notes |
|------|--------|-------|
| `docs/studios-support-spec.md` | all reviewers **read** | no writes in Wave 1 |
| `docs/research/studios-support-review-R1.md` | R1 | findings only |
| `docs/research/studios-support-review-R2.md` | R2 | findings only |
| `docs/research/studios-support-review-R3.md` | R3 | findings only |
| `docs/research/studios-support-review-R4.md` | R4 | findings only |
| `docs/research/studios-support-review-R5.md` | R5 | findings only |
| `docs/research/studios-support-review-synthesis.md` | RS | Wave 2 only |
| `cvp_mcp/**`, `cloudvision_mcp.py` | none | out of scope for this pass |

## Out of scope

- Implementing Phase 1/2 MCP tools
- Redeploy / live CVP writes
- Change-control execute/approve APIs
- Editing the spec until Wave 2 (and only after user asks to apply RS)

## Open questions (resolve before approval)

- [ ] If Ollama local is down at farm time, reroute R1 to **ollama-cloud / kimi-k3** (week 26%) or Cursor auto?
- [ ] Claude CLI has no OAuth usage token in this session — farm R3 anyway, or swap R3 to Cursor opus?

## Session reports

| Date | Chat posted | File |
|------|-------------|------|
| 2026-08-19 | bucketize-only (this turn) | _(none until execute)_ |
