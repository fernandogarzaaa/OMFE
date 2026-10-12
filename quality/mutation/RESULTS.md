# Mutation testing results (testteeth)

Tool: [testteeth](https://github.com/fernandogarzaaa/testteeth) 0.2.0, run by
`quality/mutation/run.sh before|after [module ...]`. Each module is mutated against the test
files that target it, with at most 60 evenly-sampled mutants (`MUTANT_CAP`), so **before and
after grade the identical mutant set**; only the test files differ:

* **before** = the original suite (`tests/test_security_units.py`, `test_passwords.py`,
  `test_auth.py`, `test_rbac_tenancy.py`, `test_domain_units.py`, `test_public.py`)
* **after** = the same files + the mutation-hardening tests `tests/test_mutation_security.py`,
  `tests/test_mutation_deps.py`, `tests/test_mutation_domain.py` (151 new test cases; backend suite 200 → 351)

Raw reports: `quality/mutation/results/<module>.<phase>.{json,txt}`. Run date 2026-10-13
(Asia/Manila), Python 3.12.15, pytest 9.1.1, SQLite.

## Scores

Score = killed / (total − excluded); "no cov" = mutant on a line no selected test executes.

| module | file | before | after | survivors after |
| --- | --- | --- | --- | --- |
| crypto | `security/crypto.py` | 89.3% (25/28; 2 surv, 1 no cov) | **100.0%** (28/28) | – |
| passwords | `security/passwords.py` | 92.6% (25/27; 2 surv) | **100.0%** (27/27) | – |
| signing | `security/signing.py` | 84.6% (22/26; 4 surv) | **100.0%** (26/26) | – |
| ssrf | `security/ssrf.py` | 82.2% (37/45; 4 surv, 4 no cov) | **100.0%** (45/45) | – |
| tokens | `security/tokens.py` | 74.1% (40/54; 14 surv) | **98.1%** (53/54) | 1 equivalent |
| deps (RBAC / tenant / CSRF) | `api/deps.py` | 66.7% (34/51; 10 surv, 7 no cov) | **98.0%** (50/51) | 1 equivalent |
| hashing | `hashing.py` | 84.2% (32/38; 4 surv, 2 no cov) | **100.0%** (38/38) | – |
| meta_capi (payload builder) | `adapters/meta_capi.py` | 60.0% (36/60; 17 surv, 7 no cov) | **100.0%** (60/60) | – |
| scoring | `scoring.py` | 37.5% (21/56; 35 surv) | **98.2%** (55/56) | 1 equivalent |
| attribution | `attribution.py` | 78.3% (47/60; 12 surv, 1 no cov) | **100.0%** (60/60) | – |
| **total** | | **71.7%** (319/445) | **99.3%** (442/445) | 3, all equivalent |

## Remaining survivors (all equivalent: no input can tell them apart)

1. **tokens.py:139** `verify_totp`: `not code.isdigit() or len(code) != 6` → `and`. The
   check is a fast-path filter in front of `pyotp.TOTP.verify`, which itself only matches the
   exact 6-digit string, so every code the filter would have rejected is rejected anyway. Kept
   as defence in depth (cheap rejection of junk before HMAC work).
2. **deps.py:58** `_principal_from_api_key`: removing the `raise` after `parse_api_key()`
   returns `None`. The next statement looks the key up by `prefix == None`, finds nothing and
   raises the identical `ApiError(401, "invalid_api_key", "invalid API key")`. Kept so malformed
   keys never reach the database.
3. **scoring.py:43** `follower_points`: `total_followers <= 0` → `< 0`. At exactly 0 the formula
   returns `max_points * log10(1) / … = 0.0`, the same value the guard returns.

## What the new tests pin (examples of what used to survive)

* **Boundaries**: signature tolerance is inclusive at ±300 s; webhook ports 443/8443 allowed,
  444/8444 refused; CAPI `event_name` ≤ 50, `event_time` ≤ now+60 s and ≥ now−7 d, batch ≤ 1000;
  API-key secret ≥ 20 chars and prefix exactly 12; TOTP window exactly ±1 step; scoring tiers at
  70/40 and the platform threshold at 1 000 followers; phone country-code prefixing at ≤ 10 digits.
* **Error identity**: assertions on the error *message* (`invalid timestamp`, `malformed`,
  `MASTER_KEYS is empty`, `session revoked` vs `tenant inactive` vs `authentication required`),
  so a removed `raise` that falls through to a different failure is caught.
* **Exact payloads**: the full Meta CAPI server-event dict, the JWT claim set
  (`tv`, `mfa`, `iat == nbf`, `exp − iat == ttl`, 32-char `jti`, HS256 header), the mock Graph
  handler's responses, scoring breakdown dicts and the earnings/weights/tier tables.
* **Previously unexecuted branches**: the default DNS resolver (dedupe + sort, monkeypatched
  `getaddrinfo`), `OUTBOUND_ALLOW_PRIVATE`, configured integration hosts, `Principal.is_admin`
  for API keys, inactive-tenant paths for both API keys and sessions, `tenant_ctx`, unknown
  envelope version in `rewrap_str`.
* **Randomness/clock sizes**: 96-bit AES-GCM nonce, 64-char refresh tokens, 43-char CSRF and
  API-key secrets, millisecond `fbc`/`fbp` timestamps and the `2**31` `fbp` random bound.

No production code was changed: every killed survivor was a test gap, not a bug.

## Reproduce

```bash
pip install "testteeth>=0.2,<0.3"
quality/mutation/run.sh before            # original suite
quality/mutation/run.sh after             # + mutation-hardening tests
quality/mutation/run.sh after deps tokens # subset
MUTANT_CAP=200 quality/mutation/run.sh after scoring   # bigger sample
```

CI: `.github/workflows/mutation.yml` (manual `workflow_dispatch`, `continue-on-error`, uploads
`results/*.after.*` and writes the scores to the job summary). Wall time here was 4–8 min per
module on 2 vCPUs (deps is the slowest: its tests drive the full app).
