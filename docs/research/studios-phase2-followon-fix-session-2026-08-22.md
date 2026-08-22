# Parallel bucket session — Studios 2.1 live-gap fix

**Date:** 2026-08-22
**Integration branch:** `feat/studios-phase2-followon` @ `62b73c9`
**Coordinator:** Cursor
**Spec:** `docs/studios-phase2-followon-fix-spec.md`
**Tracker:** `docs/research/studios-phase2-followon-fix-buckets.md`

## Session summary

| Bucket | Owner | Planned backend | Resolved backend | Cost pool | Model | Exec | Sub-agent ID | Branch | Commit | Tests | Status |
|--------|-------|-----------------|------------------|-----------|-------|------|--------------|--------|--------|-------|--------|
| T AssignedTags | claude-opus | claude-cli | claude-cli | claude_subscription | opus | — | — | `feat/studios-phase2-followon-fix-bucket-T-claude` | `105bf7f` — treat AssignedTags no-row as empty query CAS | 54 tag + 110 with write tests in worktree | merged |
| I Generic Inputs | codex-default | codex-cli | codex-cli | chatgpt_plus_codex | gpt-5.6-sol | — | — | `feat/studios-phase2-followon-fix-bucket-I-codex` | `399433c` — list Resource paths and overlay studio GET | 198 required subset in worktree | merged |
| D Parent spec | cursor-auto | cursor-cli | cursor-cli | cursor_included | auto | — | `e51a0cf5-3c99-4007-bd35-c13c3797e208` | `feat/studios-phase2-followon-fix-bucket-D-cursor` | `7cd83a7` — AssignedTags no-row is empty query; Resource path ≠ JSON key | n/a (docs) | merged |

## Integration health

| Check | Result |
|-------|--------|
| Combined tests | `uv run pytest -q` → **532 passed** |
| Scoped tests | `tests/test_studio_tags.py tests/test_studio_inputs_generic.py tests/test_studios_write.py tests/test_studio_crud.py` → **252 passed** |
| ruff | `ruff check` + `ruff format --check` on T/I files — passed |
| Unmerged bucket branches | none (merged; branches kept) |
| Executors at farm | Claude GREEN session 14%; Codex `--auth` ok week 4→5%; Cursor GREEN 11→12% total |
| Portfolio notes | T Claude opus, I Codex, D Cursor auto as probed. Ollama unused (local down, cloud week 100%). |

## Economics (capacity snapshot)

| Provider | Before | After | Delta | Source |
|----------|--------|-------|-------|--------|
| anthropic | 14% / 67% | 14% / 67% | +0 pt / +0 pt | burnbar |
| cursor | 11% / 18% | 12% / 18% | +1 pt / +0 pt | burnbar |
| ollama-cloud | 65% / 100% | 65% / 100% | +0 pt / +0 pt | burnbar |
| codex | 4% / — | 5% / — | +1 pt / — | chatgpt.json |

| Metric | Value |
|--------|-------|
| Snapshot before | `2026-08-22T21:11:56Z` |
| Snapshot after | `2026-08-22T21:24:04Z` |
| Wall clock (snapshot span) | 12.1 min |
| Capacity probe | `burnbar_primary` |

Anthropic session meter did not move in burnbar (possible lag). Codex week +1 pt, Cursor total +1 pt.

## What landed

- **T:** complete `/all` vs empty/404 vs truncation; no-row → `query=""` + `coverage="full"`; overlay-then-mainline CAS; `expected_current_query=""` is a valid token.
- **I:** `available_path_values` on miss; `root_path_forbidden` before HTTP; `_read_studio_anywhere` 404-only fallthrough.
- **D:** parent spec replacements (AssignedTags `/all` live; `""` CAS; Resource path ≠ JSON key).

## Still open

- Live CVaaS verify (spec §8): GET AssignedTags on Access Interfaces, preview-only assign, generic `["campus"]` miss envelope, overlay studio GET. Writes on, submit off. Coordinator, not farmed.
- Submit stays unregistered.

## Reroutes

None. Posted owners matched `--distribute`.
