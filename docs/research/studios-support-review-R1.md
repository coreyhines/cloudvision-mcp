# Bucket R1 — API facts, dual config APIs, token/auth

Status: **success**
Owner: ollama-local
Model: qwen3.8:27b-mlx (`think: false`)
Scope: `docs/studios-support-spec.md` "Why", "Implementation phases",
"Verified environment facts", "Endpoint access matrix", "Write access",
"Phase 1 — Read tools" (config-status/compliance facts only), "Open questions".
Context-only reads (not reviewed in depth): `cvp_mcp/grpc/config.py`,
`cvp_mcp/grpc/config_async_flow.py`.
Out of scope (per bucket brief): Phase 1 tool contracts (R2), Phase 2 write gates
(R3/R4), testing/open-questions sweep (R5).

No live probes performed. Findings are about *internal consistency* and
*stated clearly enough to implement against*, not fresh CVP calls.

---

## Review question answers

### Q1 — Are the 2026-08-19 API facts internally consistent? **Yes (with two Minor caveats).**

Checked token lengths, JWT claims, and the 200/403/401/400/404/PERMISSION_DENIED
grid for cross-contradiction. They cohere.

- **Token lengths.** "Two different tokens exist and only one works" table
  (`Verified environment facts`): `~/.env` = 1031 chars → 401 everywhere; container
  = 398 chars → works. The "Service account vs user role (important)" section
  independently states the 398-char token is the service-account JWT and the
  1031-char token is the unexpired user JWT (`dsn=cvpadmin`, `exp=2029-06-03`).
  The two numbers and the two identities are used consistently throughout
  (endpoint matrix probed "with the container token" = the 398-char one). No
  place swaps them. **Consistent.**
- **JWT claims.** SA token claims `sid, ogn, dsl, lbac` ("no embedded role name");
  user token claims `dsn=cvpadmin, exp=2029-06-03`. Different claim vocabularies
  is exactly what you'd expect for a service-account JWT vs a user (cvpadmin) JWT,
  and matches the "permissions evaluated server-side from roles bound to that
  service account" claim. **Consistent.**
- **200 vs 403 vs PERMISSION_DENIED.** Within the *same* Resource API,
  `inventory/studio/workspace/changecontrol` reads = 200 but `configstatus`
  REST = 403 and gRPC = `PERMISSION_DENIED` ("same principal as REST"). The spec
  explicitly reconciles this as "a separate Resource API auth boundary on this
  staging instance," not a permission gap. The 403↔PERMISSION_DENIED pairing
  (HTTP 403 ≈ gRPC authz failure) is the standard mapping and is stated as
  "same principal." **Consistent.**
- **Status-code distinctions are sharp, not muddled.** Write table correctly
  separates `404` (studio missing on `remove: true`, "not 403") from `403`
  (configstatus), and `400` (`ConfigletConfig/all` "nil workspace key") from the
  `403`/`200` cases. `401` is reserved for the wrong (1031-char) token only.
  No endpoint is given two different status codes in two places.

**Minor M1 — 398-char JWT length is asserted but not independently checkable
here.** A compact SA JWT carrying `sid` (36-char UUID) + `ogn/dsl/lbac` + standard
`iss/sub/exp/iat` is plausibly ~398 chars, so this is not a contradiction, but
the number is taken on faith. Do not treat a length mismatch in a later probe as
a spec bug without re-decoding; leave to RS/R5. (Not a live probe.)

**Minor M2 — `Configlet/all` = 200 but `ConfigletConfig/all` = 400, while
`StudioConfig/all` = 200.** The asymmetry (a Configlet*Config* needing a workspace
key) is real and consistent with the "use keyed GET, not `/all`" note and
Open question 2, but a reader skimming the matrix may find "Configlet `/all`
works but ConfigletConfig `/all` fails" confusing. The spec *does* explain it;
just flag that the explanation lives one row below the surprising one.

### Q2 — Is the dual-path story clear enough to implement against? **Mostly; one Important gap.**

The narrative is clear at the *fact* level:
- "Why" + line 58: `get_cvp_device_config` tries configstatus first, and on
  homelab's 403 falls back to compliance GetConfig for **running** config;
  **designed-config** tooling should use compliance `DESIGNED_CONFIG` "not
  configstatus URIs."
- Matrix line 50: compliance GetConfig `200`, "RUNNING_CONFIG and DESIGNED_CONFIG
  both work for EOS serials."
- `get_cvp_designed_config` (Phase 1): "Use compliance GetConfig with
  `type: DESIGNED_CONFIG` (confirmed 200 on homelab)," `data_source:
  service_api:compliancecheck.getconfig`.
- `Endpoint access matrix` lines 105–107 restate that configstatus
  (`ConfigDiff/all`, `Configuration?key.deviceId=<serial>`, gRPC GetOne) is the
  403/PERMISSION_DENIED path — i.e., the URI-fetch path is unusable here.

So "running via configstatus-with-fallback; designed via compliance
DESIGNED_CONFIG; never configstatus URIs on homelab" is stated three times in
compatible ways. **Good.**

**Important I1 — the story does not warn that the existing compliance helper is
single-type (`RUNNING_CONFIG`).** `cvp_mcp/grpc/config_async_flow.py:206` hardcodes
`"type": "RUNNING_CONFIG"` in the GetConfig payload, and
`cvp_mcp/grpc/config.py:_fetch_running_config_from_compliance_rest` only ever
asks for running config. An implementer who assumes "`get_cvp_designed_config`
reuses the same GetConfig helper, just swap the type" would need to
generalize the type parameter first — the spec's "use compliance
`DESIGNED_CONFIG`" reads as if the path already exists. Recommend RS add one line
to "Phase 1 — Read tools / `get_cvp_designed_config`": the shared async
GetConfig helper must be parameterized on `type` (RUNNING vs DESIGNED) before
designed-config can flow through it. (Tool-contract detail itself is R2; this is
the fact/story gap that is R1's.)

**Minor M3 — running-config compliance fallback is gated on `include_running_config`
and `query_configstatus`.** In `config.py`, the compliance REST fallback only runs
when `include_running_config and query_configstatus`. A caller who asks only for a
config *summary* (no running body) on homelab gets an empty summary with no
compliance fallback, because configstatus GetOne is 403. The "Why" framing
("falls back to compliance when configstatus fails") is true for the body path but
reads broader than the code. Not a spec error; note so the writer is not surprised.

### Q3 — Any remaining claim implying a missing role checkbox? **No. Clean.**

The re-verification explicitly and repeatedly *negates* the "missing role"
framing:
- Line 49: configstatus 403 is "Resource API layer only; **not** a missing UI
  'config read' toggle."
- Line 54: "configstatus Resource API remains 403 — likely a separate Resource API
  auth boundary on this staging instance, **not an absent network-admin
  checkbox**."
- Line 53–54: "Config read does **not** require fixing a configstatus permission
  in the role editor."
- "Implementation phases": Phase 2 "gated in MCP by policy, **not by missing
  CloudVision permissions**"; "token in homelab **already has write access**."
- "Open questions": Open question 1 ("which service account display name
  corresponds to `sid=019d4bab-...`") is an *identification* question, not a
  "grant this role" action. No open question asks to add a role/checkbox.

No lingering implication that a UI permission must be toggled. The spec is
self-consistent that everything is a *policy/host-boundary* matter, not a role gap.
**No finding.**

### Q4 — Is hostname-vs-serial guidance operationally sufficient? **Directionally correct but thin; one Important.**

What the spec says:
- Line 57–58: "use **device serial** when hostname lookups 504."
- `get_cvp_designed_config` parameters (line 182): "`device_id` (serial preferred;
  resolve hostname via inventory first)."
- Concrete mapping present: line 53, "~19 KB for `JPE19151499` / 720xp-24"
  (serial `JPE19151499` ↔ hostname `720xp-24`), and 720xp-24 recurs in "Why."

**Important I2 — the 504 origin and the resolution chain are under-specified.**
The guidance "use serial when hostname lookups 504" is the right operational
instinct, but it does not say *which* lookup 504s. From the code, hostname
resolution runs two ways: REST inventory `/all` (matrix: 200) and a gRPC
`grpc_one_device_by_hostname` fallback (`config.py`), the latter being the
plausible 504 source. As written a reader can't tell whether "hostname lookups 504"
means the REST inventory or the gRPC-by-hostname path. Recommend RS add a
one-liner: the 504s come from the gRPC/by-hostname path; resolve hostname→serial
via the (200) REST inventory first, then pass the serial to compliance
GetConfig. The "resolve hostname via inventory first" hint already points there,
but the causal link (inventory=200 vs gRPC-hostname=504) is not stated.

**Minor M4 — "serial preferred" vs "resolve hostname via inventory first" can read
as a chicken-and-egg.** If a caller only has a hostname, they must hit inventory
first to get the serial; the spec implies this but doesn't say what to do if that
inventory lookup itself is unavailable. Given matrix shows inventory `/all` = 200,
the risk is low; just make the fallback explicit (warn + surface
`device_id_input`, as the code already does in `device_facts`).

The concrete `JPE19151499`↔`720xp-24` pairing (Q1/Q4 cross-check) is a nice
example and is internally consistent with the "Why" motivation. **Keep it.**

---

## Summary of findings

| ID | Severity | Topic | One-line |
|----|----------|-------|----------|
| I1 | Important | Q2 dual-path | Spec says "use compliance DESIGNED_CONFIG" as if the helper exists; the shared GetConfig helper hardcodes `RUNNING_CONFIG` (`config_async_flow.py:206`) and must be type-parameterized first. |
| I2 | Important | Q4 hostname/serial | "Use serial when hostname lookups 504" is right but doesn't say the 504 is the gRPC/by-hostname path vs 200 REST inventory; make the causal link + resolution chain explicit. |
| M1 | Minor | Q1 tokens | 398-char SA JWT length is asserted, not verifiable from the doc; don't treat a future length mismatch as a spec bug. |
| M2 | Minor | Q1 matrix | `Configlet/all`=200 vs `ConfigletConfig/all`=400 asymmetry is correct but the explanation sits one row below the surprising entry. |
| M3 | Minor | Q2 dual-path | Running-config compliance fallback is gated on `include_running_config`+`query_configstatus`; "Why" framing reads broader than the code path. |
| M4 | Minor | Q4 | "Serial preferred / resolve via inventory first" — state the fallback if inventory lookup itself is unavailable. |

**Q3 (missing role checkbox): no finding — clean.** The re-verification consistently
frames all 403s as a Resource-API auth boundary / MCP policy gate, explicitly
denying a missing UI role/config-read toggle; no open question asks to grant a
role.

No Critical findings. The 2026-08-19 API facts are internally consistent
(Q1 yes); the dual-path story is implementable with one added caveat (Q2 I1);
no lingering role-checkbox implication (Q3 clean); hostname/serial guidance is
correct but should pin down the 504 origin (Q4 I2).
