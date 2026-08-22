# Studios Phase 2.1 live-gap fix — bucket plan

Generated from: `docs/studios-phase2-followon-fix-spec.md` (revised 2026-08-22 after R1/R2/R3)
Integration branch: `feat/studios-phase2-followon`
Coordination skill: Parallel Buckets (Cursor adapter)

## Approval status

| Field | Value |
|-------|-------|
| Status | **`approved`** |
| Approved by | user (2026-08-22; AskQuestion: approve as posted) |
| Approved waves | all (wave 1: T ∥ I ∥ D) |
| Notes | Probe spread kept: T claude-opus, I codex-default, D cursor-auto. |

Approved 2026-08-22. Wave 1 (T ∥ I ∥ D) may execute.

## Bucket sizing summary

| Metric | Value |
|--------|-------|
| Total buckets | 3 (T, I, D) |
| Parallel wave 1 | T ∥ I ∥ D |
| Claude buckets | 1 (T) |
| Codex buckets | 1 (I) |
| Cursor buckets | 1 (D) |
| Ollama buckets | 0 (local unreachable; cloud week 100%) |
| Estimated phases | 1 |

## Session status

| Field | Value |
|-------|-------|
| **Level** | GREEN (Anthropic session 14%; Cursor 11%/API 18%; Codex week 4%) |
| **Updated** | 2026-08-22 16:11 CDT |
| **Claude session / week-all %** | 14% / 67% — session reset ~20:10 CDT |
| **Cursor total / API %** | 11% / 18% |
| **Codex week %** | 4% (`chatgpt.json`); burnbar ChatGPT snapshot **stale RED** — `codex_status --auth` **ok** |
| **Ollama cloud week %** | **100% RED** — do not farm |
| **Ollama local** | unreachable (`127.0.0.1:11434`) |
| **Capacity probe** | burnbar_primary |
| **Before snapshot** | `docs/research/pb-sessions/studios-phase2-followon-fix/before.json` |

## Bucket registry (schedule)

| Wave | ID | Title | Profile | Anthropic | Owner | Backend | Model | Exec | Files (own) | Depends | Fits one session? |
|------|-----|-------|---------|-----------|-------|---------|-------|------|-------------|------------|-------------------|
| 1 | T | AssignedTags no-row + CAS inherit | `write_crud` | opus | **claude-opus** | claude-cli | opus | — | `cvp_mcp/grpc/studio_tags.py`, `tests/test_studio_tags.py` | — | yes |
| 1 | I | Generic Inputs paths + overlay studio GET | `write_crud` | opus | **codex-default** | codex-cli | gpt-5.6-sol | — | `cvp_mcp/grpc/studio_inputs_generic.py`, `tests/test_studio_inputs_generic.py` | — | yes |
| 1 | D | Parent spec replacements | `serialize` | none | **cursor-auto** | cursor-cli | auto | — | `docs/studios-phase2-spec.md` | — | yes |

S extract cancelled (spec §Farm later). I **imports** `_read_studio_anywhere` from `studio_crud.py` (read-only; do not edit that file).

### Probe vs override

| Bucket | Probe owner | Posted owner | Why |
|--------|-------------|--------------|-----|
| T | claude-opus | claude-opus | `--distribute` |
| I | codex-default | codex-default | `--distribute`; Codex `--auth` ok. Last follow-on farm skipped Codex to spend Anthropic — **not** applied unless user says so. |
| D | cursor-auto | cursor-auto | `--distribute` |

## Deliverables by bucket

### T — AssignedTags

- Complete `/all` vs empty-body vs 404 vs truncation (`truncated_to_` / `ndjson_skip_invalid_line`)
- 0 filter matches after **complete** stream → `query=""`, `coverage="full"`
- Overlay-then-mainline resolver for assign (and GET when draft id passed)
- `expected_current_query=""` is a valid CAS token; omit/`None` still `expected_current_query_required`
- Bind `""` into `preview_token`
- Keep existing error **strings** (`empty_query_forbidden`, `current_query_mismatch`, `assigned_tags_unavailable`, `assigned_tags_read_failed`, `assigned_tags_ambiguous`)
- Tests in spec §5 (tags)

**Do NOT:** generic Inputs, parent spec, `studios_write.py`, `studios.py`, submit, live CVaaS

### I — Generic Inputs

- Miss envelope: `available_path_values` + `details.hint`
- `[]` → `root_path_forbidden` before HTTP
- Import `_read_studio_anywhere` (404-only fallthrough)
- Fail closed on truncated Inputs/`all`
- Tests in spec §5 (inputs)

**Do NOT:** `studio_tags.py`, `studio_crud.py` (except import), `studios_write.py`, `get_cvp_studio()`, generic root POST, submit

### D — Parent spec

Replace named sentences in `docs/studios-phase2-spec.md` listed in fix-spec §7. Do not append a contradictory paragraph. Keep `current_query_mismatch`; do not revive `tag_query_mismatch`.

**Do NOT:** Python, tests, MCP wiring

## Merge order

```text
(T ∥ I ∥ D) → coordinator merge + pytest
```

## File ownership map

| File | Bucket |
|------|--------|
| `cvp_mcp/grpc/studio_tags.py` | T |
| `tests/test_studio_tags.py` | T |
| `cvp_mcp/grpc/studio_inputs_generic.py` | I |
| `tests/test_studio_inputs_generic.py` | I |
| `docs/studios-phase2-spec.md` | D |
| `studio_crud.py` / `studios_write.py` / `studios.py` | nobody (import/read only) |

## Out of scope

- Register `submit_cvp_workspace`
- 2.0 description CAS
- Keyed AssignedTags GetOne
- Generic root Inputs POST
- Live CVaaS writes (verify after merge, coordinator)

## Open questions

- [ ] User still wants all Anthropic (override I → claude-opus) like the previous follow-on farm?

## Farm commands (after approval)

```bash
export PARALLEL_BUCKETS_HOME=/Users/corey/code/parallel-buckets
export REPO=/Users/corey/code/cloudvision-mcp
export BASE=feat/studios-phase2-followon
export SLUG=studios-phase2-followon-fix

REPO=$REPO BASE=$BASE BUCKET=T SLUG=$SLUG CLAUDE_MODEL=opus \
  PROMPT_FILE=tmp_bucket_T_prompt.md \
  bash $PARALLEL_BUCKETS_HOME/scripts/farm_claude_bucket.sh

REPO=$REPO BASE=$BASE BUCKET=I SLUG=$SLUG \
  PROMPT_FILE=tmp_bucket_I_prompt.md \
  bash $PARALLEL_BUCKETS_HOME/scripts/farm_codex_bucket.sh

REPO=$REPO BASE=$BASE BUCKET=D SLUG=$SLUG \
  PROMPT_FILE=tmp_bucket_D_prompt.md \
  bash $PARALLEL_BUCKETS_HOME/scripts/farm_cursor_bucket.sh
```

## Session reports

| Date | Chat posted | File |
|------|-------------|------|
| | no | `docs/research/studios-phase2-followon-fix-session-*.md` |
