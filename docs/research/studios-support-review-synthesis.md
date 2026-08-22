# Studios spec review — Wave 2 synthesis

**Date:** 2026-08-19  
**Spec:** `docs/studios-support-spec.md`  
**Inputs:** R1–R5 findings on `feat/studios-support-spec`  
**Coordinator:** Cursor (inline). No product-code edits.

## Cross-review verdict

The spec is a strong investigation log and a usable **Phase 2 API map**, but it is **not yet an implementation spec**. Five independent reviewers (Ollama qwen3.8, Cursor auto, Cursor opus, gpt-5.6-sol, Cursor sonnet) agree on that shape even though they scoped different sections.

| Question | Answer |
| --- | --- |
| Ship Phase 1 reads first? | **Yes**, after tightening contracts (GetConfig POST body, NDJSON `/all`, envelope). |
| Ship Phase 2 writes as written? | **No.** Sequence/URLs match Arista; the safety model is unsafe. |
| Is the 403 a missing role checkbox? | **No.** Dual API (compliance vs configstatus Resource API) is the right story; leftover IAM framing should be cut. |
| Does Phase 1 close the original “logging host” question? | **No** at line granularity. Device-level studio `sources` yes; configlets and per-line provenance unspecified. |

## Shared Critical / Important themes

1. **Line-level provenance vs Why** (R2, echoed by R5 inventory gaps). `search_cvp_studio_templates` does not emit designed CLI; `get_cvp_designed_config` is device-level studios only. Either add an attribution algorithm + configlet read, or rewrite Why to the question Phase 1 can actually answer.

2. **Compliance GetConfig is the read path but not a coding contract** (R1, R2). Parameterize `type` (`RUNNING_CONFIG` / `DESIGNED_CONFIG`); paste one live DESIGNED_CONFIG `sources` fixture; do not reuse first-line JSON helpers for `/all`.

3. **Phase 2 REST shapes are right; field semantics are the hazard** (R3). Same ChangeControlConfig endpoint can `start` a CC. Inputs at root path and a single tag `query` are **full replacements**. Submit gated only by a model-settable `allow_submit`. `^builtin-` on delete does not block writing into builtin workspaces.

4. **Write gates vs live `tool_enabled`** (R4, R5). Spec invents `writes=True`; code only has `CVP_MCP_DISABLED_TOOLS`. Submit HTTP 200 must be `accepted` + poll via **read** tools, not success inside the write tool. Need `CLOUDVISION_MCP_ALLOW_WRITES` plus a **separate** submit env (R3) and UUID `request_id`s (R4).

5. **Spec process contradiction** (R5). Open question 3 asks whether Phase 2 should ship at all, while ~150 lines already specify every write tool. Gate the write section on that decision.

6. **Missing Phase 1 reads that Phase 2 assumes** (R2, R5): `get_cvp_studio_inputs`, keyed Workspace/WorkspaceBuild probes in the access matrix, `fetch_uri_with_bearer` cannot parse NDJSON streams.

## Recommended spec edit order (do not implement tools yet)

1. Collapse IAM vs Resource-API 403 narrative (R1/R5).
2. Phase 1 contracts: GetConfig table, NDJSON rules, envelope, build poll state machine, `get_cvp_studio_inputs`, configlets in or out of Why.
3. Phase 2: drop or tightly schema-validate CC create; document input/tag replace semantics + dry-run target preview; `CLOUDVISION_MCP_ALLOW_SUBMIT`; no `REQUEST_SUBMIT_FORCE` / `start`; refuse `builtin-` on **all** writes; immutable/from_package studios.
4. Move “should Phase 2 ship?” to the top of Phase 2; shrink Open questions.

## Out of scope for this synthesis

Applying the spec patches (ask if you want a follow-up edit pass). Live CVP re-probes. Product implementation.
