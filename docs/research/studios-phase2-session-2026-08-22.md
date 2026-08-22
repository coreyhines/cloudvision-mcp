# Parallel bucket session — Studios Phase 2.0

**Date:** 2026-08-22
**Integration branch:** `feat/studios-phase2-impl` @ `14d3b87` + uncommitted review apply
**Coordinator:** Cursor
**Spec:** `docs/studios-phase2-spec.md`
**Tracker:** `docs/research/studios-phase2-buckets.md`

### Session summary (required — post in chat)

| Bucket | Owner | Planned backend | Resolved backend | Cost pool | Model | Exec | Sub-agent ID | Branch | Commit | Tests | Status |
|--------|-------|-----------------|------------------|-----------|-------|------|--------------|--------|--------|-------|--------|
| 0 Write gates | ollama-local | ollama-local-cli | ollama-local-cli | ollama_sunk | qwen3.8:27b-mlx | — | — | `feat/studios-phase2-bucket-0-ollama` | `3758f6d` — write env gates and preview_token | write_access | merged |
| 1a Resource helper | claude-opus | claude-cli | claude-cli | claude_subscription | opus | — | — | `feat/studios-phase2-bucket-1a-claude` | `610d8c0` — allowlisted Resource API write helper | resource_write | merged |
| 1b Workspace + CAS | codex-default | codex-cli | claude-cli | claude_subscription | opus | — | — | `feat/studios-phase2-bucket-1b-claude` | `df189f2` — workspace draft and description CAS | studios_write | merged |
| 2 MCP wiring | cursor-auto | cursor-auto | cursor-auto | cursor_included | auto | inline | — | `feat/studios-phase2-impl` | `14d3b87` — register write tools behind env gate | import-time | merged |
| R1 Defect review | cursor-named-sonnet | cursor-cli | cursor-cli | cursor_api_quota | sonnet | subagent | — | — | `docs/research/studios-phase2-review-R1.md` | n/a | done |
| R2 Safety review | cursor-auto | cursor-auto | cursor-auto | cursor_included | auto | subagent | — | — | `docs/research/studios-phase2-review-R2.md` | n/a | done |
| R3 Contract review | claude-opus | claude-cli | cursor-auto | cursor_included | auto | subagent | — | — | `docs/research/studios-phase2-review-R3.md` | n/a | done |
| RS Apply reviews | coordinator | inline | inline | cursor_included | auto | inline | — | `feat/studios-phase2-impl` | this session | 114 write / 283 all | done |

**Reroutes:** Bucket 1b Codex CLI 401 invalid refresh token → Claude opus. R2 probe wanted ollama-cloud (`kimi-k2.7-code:cloud`) at week 89% YELLOW → Cursor auto. R3 Claude CLI not used; Cursor Task.

### Integration health

| Check | Result |
|-------|--------|
| Combined tests | `uv run pytest -q` → **283 passed** |
| Write tests | 114 passed (`test_write_access` + `test_resource_write` + `test_studios_write`) |
| ruff | clean on Phase 2 files |
| black | `cvp_mcp/grpc/studios_write.py` reformatted |
| Unmerged bucket branches | leftover `feat/studios-phase2-bucket-0-ollama`, `-1a-claude`, `-1b-claude` (already merged) |
| Stashed WIP | none for this feature (unrelated stash `fix/lldp-inventory-device-resolution`) |
| Worktrees active | none |
| Claude at farm | session 0% → **21%**; week-all 63% |
| Ollama at farm | local used for bucket 0; cloud week **89%** unused this execute |
| Cursor at farm | total 9% / API 18% (unchanged) |
| Capacity source | `burnbar_primary` |
| Portfolio notes | Ollama local (sunk) + Claude subscription (1a/1b) + Cursor included (wiring + reviews). Codex unused after 401. |

### Economics

See `docs/research/pb-sessions/studios-phase2/economics.md`.

| Provider | Before % | After % | Delta | Source | Notes |
|----------|----------|---------|-------|--------|-------|
| anthropic (session / week) | 0% / 0% | 21% / 0% | +21 / +0 | claude scrape | 1a + 1b opus farms |
| cursor (total / api) | 9% / 18% | 9% / 18% | +0 / +0 | burnbar | wiring + Wave 4 Tasks |
| ollama-cloud (session / week) | 29% / 89% | 29% / 89% | +0 / +0 | burnbar | not farmed |
| codex / ChatGPT Plus | 0% | 0% | +0 | chatgpt.json | 401, unused |

| Metric | Value |
|--------|-------|
| Cost pools active | cursor_included, claude_subscription, ollama_sunk |
| Wall clock (snapshot span) | 54.9 min |
| Buckets / Exec mix | 2 external CLI (0, 1a) + 1 rerouted CLI (1b) + 1 inline (2) + 3 review Tasks + 1 inline apply |
| Double-burn class | `cross_pool` |
| Snapshot paths | `docs/research/pb-sessions/studios-phase2/{before,after,economics}.*` |

### Review apply (this turn)

Fail-closed truncated Inputs; description CAS requires pending workspace + studio GET (`immutable` / `from_package`); build `request_id` bound into preview token and required on confirm; DELETE query names allowlisted. Tests updated (114).

**Not done (by design):** live CVaaS POST, submit registration, `CLOUDVISION_MCP_ALLOW_WRITES` on the homelab MCP host, PR to main.

### Next

| Item | Action |
|-------|--------|
| Human enable writes | Only on container token (398), never workstation `~/.env` (1031 → 401) |
| First live 2.0 | dry-run → `ws-mcp-test-*` → description CAS → build → delete workspace; no submit |
| Spec follow-up | Confirm must echo preview `request_id` (code is stricter than spec prose) |
| Cleanup | delete leftover farm branches |
