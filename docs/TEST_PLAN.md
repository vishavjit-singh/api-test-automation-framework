# Test plan: Orders API regression

This plan covers the bundled demo service. The structure is the same for any REST API the framework is pointed at.

## Scope

| In scope | Out of scope |
|---|---|
| Authentication (JWT validation) and authorisation (roles) | Load and soak testing (the latency chart is diagnostic only) |
| Tenant isolation on every tenant-owned resource | UI and end-to-end business flows across services |
| Catalogue, orders, cancellation, shipping quotes | The identity provider itself (tokens are minted by the framework) |
| Request validation, error contract, request tracing | Penetration testing beyond the auth negative cases |
| Degraded mode: upstream carrier timeouts, errors, bad payloads, circuit breaker | |

## Test tiers

| Tier | Question it answers | Examples |
|---|---|---|
| **positive** | Does the happy path return the right data in the right shape? | create, read back, cancel, idempotent replay, pagination walk |
| **negative** | Is every bad input or caller refused with the right status and error code, and with no side effects? | expired, forged and malformed tokens, wrong tenant, missing role, bad JSON, insufficient stock |
| **edge** | Are the boundaries exactly where the contract says? | `qty` 0/1/100/101, 1–20 lines, `limit` 0/1/50/51, 64 KB body, weight 1/30000 |
| **fallback** | Does the service degrade gracefully when a dependency fails? | carrier timeout / 5xx / malformed → flat rate, orders still accepted, breaker opens |

`smoke` is a cross-tier subset (9 items) for post-deploy checks: `make smoke ENV=staging`.

## Environments

| Target | How the suite reaches it | Fault hooks | Used by |
|---|---|---|---|
| `inproc` | ASGI in the test process | yes | `make test`, CI matrix |
| `local` | HTTP to uvicorn on 127.0.0.1:8000 | yes | `make demo`, CI `http` job |
| `docker` | HTTP to the `api` container | yes | `make up`, CI `docker` job |
| `staging` | HTTP to `STAGING_API_URL` with `API_JWT_SECRET` from CI secrets | no: the fallback tier skips itself | manual `staging.yml` workflow |

## Entry and exit criteria

- **Entry:** the target answers `GET /health`. On hook-enabled targets the suite resets service state once per run.
- **Exit:** no failed items. Known defects must be marked `@pytest.mark.known_defect("<KEY>", "<summary>")`, which makes them a *strict* xfail. When the defect is fixed the test XPASSes, the run fails, and the marker has to be removed. This stops a known-defect list from quietly going stale.

## Traceability

- Each test function has a stable `@pytest.mark.case("API-TC-###")`. The plugin rejects missing, malformed or duplicated IDs and missing tier markers at collection time.
- `xray/mapping.yaml` maps case IDs to Xray test issues. Parametrised tests become one Xray test with iterations.
- Each run writes `reports/xray-results.json` (Xray JSON import), `reports/junit.xml`, `reports/results.json` and `reports/requests.jsonl`. `python -m apitest xray-upload --send` imports the run into Xray Cloud.
- [`TEST_CATALOGUE.md`](TEST_CATALOGUE.md) lists every case with its Xray key, tier and purpose. It is generated from the code by `make catalogue`.

## Defect log (demo)

| Key | Summary | Found by | Status |
|---|---|---|---|
| DEMO-301 | `GET /v1/products?limit=0` returns 500 instead of 422 | API-TC-021 (edge) | Open. Seeded on purpose to demonstrate the known-defect flow |
