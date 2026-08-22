# Follow-on R2 — safety

## Critical

None. ChangeControlConfig still not allowlisted. Submit MCP tool absent.

## Important

| ID | Issue |
|----|--------|
| R2-I1 | Empty tag `query` → `empty_query_forbidden`. |
| R2-I2 | Studio templates lint `shutdown` / `no shutdown` before HTTP. |
| R2-I3 | Generic inputs refuse enabled/vlan/poe/profile/mode-style leaves. |
| R2-I4 | `REQUEST_SUBMIT` POST still `submit_disabled` while staleness field is None. |

## Minor

| ID | Issue |
|----|--------|
| R2-M1 | Homelab writes still off (`CLOUDVISION_MCP_ALLOW_WRITES` absent on strongpod). Do not enable until this branch is deployed. |
