# Parallel bucket session — Studios Phase 2 follow-on

**Date:** 2026-08-22
**Integration branch:** `feat/studios-phase2-followon`
**Coordinator:** Cursor
**Spec:** `docs/studios-phase2-spec.md` slices 2.1/2.2
**Tracker:** `docs/research/studios-phase2-followon-buckets.md`

### Session summary

| Bucket | Owner | Planned | Resolved | Cost pool | Model | Exec | Branch | Commit | Tests | Status |
|--------|-------|---------|----------|-----------|-------|------|--------|--------|-------|--------|
| 0 | claude-opus | claude-cli | claude-cli | claude_subscription | opus | — | `…-bucket-0-claude` | `00785a2` | resource_write | merged |
| 1a | claude-opus | claude-cli | claude-cli | claude_subscription | opus | — | `…-1a-claude` | `3cf15fc` | studio_tags | merged |
| 1b | claude-opus | claude-cli | claude-cli | claude_subscription | opus | — | `…-1b-claude` | `7dc1da6` | inputs_generic | merged |
| 1c | claude-opus | claude-cli | claude-cli | claude_subscription | opus | — | `…-1c-claude` | `ece3d8e` | workspace_submit | merged |
| 1d | claude-opus | claude-cli | claude-cli | claude_subscription | opus | — | `…-1d-claude` | `1be2e0f` | studio_crud | merged |
| W | claude-opus | claude-cli | claude-cli | claude_subscription | opus | — | `…-W-claude` | `24a6d4d` | wiring | merged |
| R1–R3 | claude-opus | claude-cli | **cursor-auto** | cursor_included | auto | inline | followon | (this commit) | n/a | CLI farm hung; coordinator notes |
| O | cursor-auto | inline | skipped | — | — | — | — | — | — | blocked on deploy of #14 + this branch |

**Reroutes:** Review CLI farms hung after worktree create (`claude -p` no-op). Notes written inline.

### Integration health

| Check | Result |
|-------|--------|
| Combined tests | `uv run pytest -q` → **505 passed** |
| Submit MCP tool | not registered |
| Homelab writes | still unset on strongpod |

### Next

Push `feat/studios-phase2-followon` and open PR onto `feat/studios-phase2-impl` (#14). Enable container writes only after that image is deployed.
