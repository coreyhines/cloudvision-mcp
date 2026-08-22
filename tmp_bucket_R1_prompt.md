# Bucket R1 — API facts, dual config APIs, token/auth

Review **Bucket R1 only**. Read-only. Do not edit the spec or product code.

## Context

- **Feature:** Studios support spec review
- **Owner:** ollama-local
- **Model:** qwen3.8:27b-mlx (`think: false`)
- **Exec:** farm (`farm_ollama_bucket.sh`, review / ask mode)

## Read first

1. `docs/studios-support-spec.md` — Why, Verified environment facts, Endpoint access matrix, Write access table
2. `cvp_mcp/grpc/config.py` — configstatus vs compliance fallback (context only)
3. `cvp_mcp/grpc/config_async_flow.py` — GetConfig payload (`RUNNING_CONFIG` / `DESIGNED_CONFIG`)

## Deliverables (ONLY these files)

| File | Purpose |
|------|---------|
| `docs/research/studios-support-review-R1.md` | Findings |

## Review questions

1. Are the 2026-08-19 API facts internally consistent (token lengths, JWT claims, 200 vs 403 vs PERMISSION_DENIED)?
2. Is the dual-path story (configstatus Resource API vs compliance GetConfig) stated clearly enough to implement against?
3. Any remaining claims that still imply a missing role checkbox after the re-verification?
4. Hostname vs serial guidance — is it operationally sufficient?

## Severity

Use Critical / Important / Minor. Cite spec section headings. Do not invent live probes.

## Do NOT

- Edit `docs/studios-support-spec.md` or any Python
- Review Phase 1 tool APIs in depth (R2) or Phase 2 write gates (R3/R4)
- Enable Ollama thinking mode

## Report back

```
Bucket R1: <success|failed>
Files: docs/research/studios-support-review-R1.md
Notes: <top 3 findings>
```
