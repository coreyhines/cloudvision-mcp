# Session record — Studios Phase 2 final (2026-09-02)

**Branch:** `feat/studios-phase2-final` → PR #17, **merged to `main`** 2026-09-03 01:44Z
(`1b89451`). Follow-ups (dependency pin, serial pre-commit hooks, this record)
are PR #18 from `chore/phase2-final-followups`.
**Spec:** `docs/studios-phase2-final-spec.md` (status: implemented on branch).
**Reviews:** `docs/research/studios-phase2-final-adversarial-review.md` (spec
review + post-implementation code review, all findings applied).
**Coordinator:** Claude Code (Fable 5.1), single session, no farm — buckets
were sequential in one checkout.

## State for the next agent

Everything code-side is done and green. What remains is **live verification on
the deployed image**, which no test can substitute for, plus the merge.

| Check | Result |
| --- | --- |
| `uv run pytest -q` | 628 passed |
| `uv run ruff check cvp_mcp tests cloudvision_mcp.py`, `uv run black --check …` | clean |
| `uv run pre-commit run --all-files` | stable (uv-backed hooks now `require_serial`) |
| Registration (`tests/test_write_registration.py`) | writes off → no write tools; on → nine, never `submit_cvp_workspace` |
| Capture gate (spec §D.0) | passed; fixture `tests/fixtures/inputs_mss_service_root_2026-09-02.json` is post-change mainline |

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

## Next steps, in order

1. ~~Merge PR #17~~ done. Merge PR #18 (follow-ups).
2. **Deploy** the image to the homelab MCP host (strongpod) with
   `CLOUDVISION_MCP_ALLOW_WRITES=1`; restart the process (env is read at
   import). Existing deploy path: see `fix(deploy)` commits / quadlet notes in
   README.
3. **Live verify, spec §B** (2.0 loop — never run on the tenant):
   operator names one port; create `ws-mcp-test-desc-*`; description CAS
   preview → confirm; build → `BUILD_STATE_SUCCESS`; open workspace in CVP UI,
   confirm a one-line diff; **do not submit**; delete workspace. Record in
   `docs/research/studios-phase2-live-verify-<date>.md`; flip the parent
   spec's first Open row.
4. **Live verify, spec §C** (2.1 §8): assigned-tags reads on
   `studio-campus-access-interfaces` (`query=""`) and `studio-mss-service`
   (live query); assign preview only; generic Inputs miss envelope; overlay
   studio GET via a throwaway `create_cvp_studio`; delete draft.
5. **Live verify, spec §D.9** (2.3): `get_cvp_studio_inputs(studio-mss-service)`
   carries `inputs_sha256`; `create_cvp_workspace ws-mcp-test-mss-*`; stale
   digest → `inputs_digest_mismatch`; real preview; confirm; re-read with the
   draft id → overlay row, mainline digest unchanged; build; inspect
   `traffic-policy` in CVP; delete draft. The §D.7 ops re-applied to today's
   mainline preview `inputs_unchanged` (the change already exists) — use the
   throwaway group in §D.9 step 5 for a real diff. **Never submit.**
6. Claude Code client: allowlist the write tools in `permissions.allow`
   (README "Studios write tools") or the auto-mode classifier denies them
   without a prompt.

## Gotchas hit this session (already fixed or recorded)

- zsh Bash tool does not word-split `$VAR` file lists → use arrays. A failed
  `git add` before `git commit` commits whatever was staged.
- `uv run`-backed pre-commit hooks raced across parallel batches → fixed with
  `require_serial: true` in `.pre-commit-config.yaml`. If the venv is left
  inconsistent ("missing RECORD"), `uv sync --reinstall`.
- Live `get_cvp_studio_inputs` for MSS returned the **post-change** document;
  the 2.3 draft's description of the row was pre-change. Tests derive `PRE`
  from the fixture and prove the ops reproduce `POST`.

## Not done, on purpose

- Live verifies (§B/§C/§D.9) — need the deployed image.
- `client_error` logging with `exc_info`; `inputs_root_ambiguous` for multiple
  root rows per workspace; refusing string-valued `immutable` flags — listed
  as "not taken" in the review record; separate changes if wanted.
