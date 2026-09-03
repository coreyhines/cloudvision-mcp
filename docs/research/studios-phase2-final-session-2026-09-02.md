# Session record — Studios Phase 2 final (2026-09-02)

**Branch:** `feat/studios-phase2-final` → PR #17, **merged to `main`** 2026-09-03 01:44Z
(`1b89451`). Follow-ups (dependency pin, serial pre-commit hooks, this record)
are PR #18 from `chore/phase2-final-followups` (also merged).
**Spec:** `docs/studios-phase2-final-spec.md` (status: implemented on `main`).
**Reviews:** `docs/research/studios-phase2-final-adversarial-review.md` (spec
review + post-implementation code review, all findings applied).
**Coordinator:** Claude Code (Fable 5.1), single session, no farm — buckets
were sequential in one checkout.

## State for the next agent

Code-side is done and green. Live §B / §C / §D.9 were verified 2026-09-02 on
the deployed image. Detailed live-verify narratives (device serials, private
IPs, hostnames) live **outside this repo** under
`~/code/untracked/cloudvision-mcp/research/` — do not commit equivalents under
`docs/research/`.

| Check | Result |
| --- | --- |
| `uv run pytest -q` | 628 passed |
| `uv run ruff check cvp_mcp tests cloudvision_mcp.py`, `uv run black --check …` | clean |
| `uv run pre-commit run --all-files` | stable (uv-backed hooks now `require_serial`) |
| Registration (`tests/test_write_registration.py`) | writes off → no write tools; on → nine, never `submit_cvp_workspace` |
| Capture gate (spec §D.0) | passed; fixture `tests/fixtures/inputs_mss_service_root_2026-09-02.json` is post-change mainline |
| Live §B / §C / §D.9 | closed 2026-09-02 (operator notes untracked) |

Commits on the branch, in order: spec docs → **S** submit retired → **R**
digest + loader → **M** `set_cvp_mss_policy_inputs` → **D** parent/support spec
→ review fixes → `mcp[cli]` pin → serial pre-commit hooks → this record.

## What was decided (do not reopen)

- **The MCP never submits.** Human reviews the workspace diff in the CVP UI and
  submits there. `REQUEST_SUBMIT` is not in the helper allowlist; there is no
  submit env gate to turn on. Spec §A.
- `set_cvp_mss_policy_inputs` is fixed to `studio-mss-service`, bounded op
  vocabulary, digest CAS. No generic root POST. Spec §D.
- Policies: no create, no remove (hidden policy-id mapper is out of scope).
- `<any>` is exact and stands alone; CIDRs strict; upsert merges; content
  refusals scoped to touched entries. Review record explains each.

## Priority (operator)

Day-to-day goal: put desired inputs into an **existing** studio and produce
production-ready designed config (build → human review/submit). Keep
`create_cvp_studio` / `delete_cvp_studio` available but low priority — do not
retire like submit; do not prioritize studio CRUD over Inputs-on-existing.

## Next steps

1. ~~Merge #17 / #18~~ done.
2. ~~Deploy + live §B / §C / §D.9~~ done (details untracked).
3. Claude Code client: allowlist write tools in `permissions.allow` (README
   "Studios write tools") if using that client.
4. Broader cleanup (separate change): audit older `docs/research/` and
   `tests/fixtures/` for tenant identifiers; move or redact as needed.

## Gotchas hit this session (already fixed or recorded)

- zsh Bash tool does not word-split `$VAR` file lists → use arrays. A failed
  `git add` before `git commit` commits whatever was staged.
- `uv run`-backed pre-commit hooks raced across parallel batches → fixed with
  `require_serial: true` in `.pre-commit-config.yaml`. If the venv is left
  inconsistent ("missing RECORD"), `uv sync --reinstall`.
- Live `get_cvp_studio_inputs` for MSS returned the **post-change** document;
  the 2.3 draft's description of the row was pre-change. Tests derive `PRE`
  from the fixture and prove the ops reproduce `POST`.
- Live-verify notes with serials/IPs/hostnames must not be committed under
  `docs/research/`; use `~/code/untracked/cloudvision-mcp/`.

## Not done, on purpose

- `client_error` logging with `exc_info`; `inputs_root_ambiguous` for multiple
  root rows per workspace; refusing string-valued `immutable` flags — listed
  as "not taken" in the review record; separate changes if wanted.
