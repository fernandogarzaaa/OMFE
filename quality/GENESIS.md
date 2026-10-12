# Genesis audit of the test gates

[Genesis](https://github.com/fernandogarzaaa/genesis) (`code-py` suite, PR #29) attacks verifiers
that gate Python code with pytest. We point it at our two gates:

| gate | file | what it trusts |
| --- | --- | --- |
| **hardened** | `quality/gate.py` | only a JUnit report written to a secret path, checked against a pinned manifest, plus canaries that must fail and an exact expected exit status (1) |
| **naive** | `quality/naive_gate.py` | pytest's exit status (what most CI does) |

Both run the **same full test set** (visible + held-out tests), so visible-test overfitting is
caught by both; the difference is everything else.

## Results (2026-10-13, genesis `main` @ 864f3de, suite `code-py@0.1.0`, digest `28bac3dc2806`)

| verifier | verdict | false-accept (9 exploits) | false-reject (2 controls) |
| --- | --- | --- | --- |
| `quality/gate.py genesis` (hardened) | **SOUND** (exit 0) | **0.0%** (0/9) [95% CI 0.0–29.9%] | 0.0% (0/2) |
| `quality/naive_gate.py genesis` (exit code) | **EXPLOITABLE** (exit 1) | **55.6%** (5/9) [26.7–81.1%] | 0.0% (0/2) |
| reference: genesis `fixtures/verifiers/strict_pytest.py` | SOUND | 0.0% | 0.0% |
| reference: genesis `fixtures/verifiers/naive_pytest.py` | EXPLOITABLE | 100.0% | 0.0% |

Per probe:

| probe | defect class | hardened gate | why it was rejected | naive gate |
| --- | --- | --- | --- | --- |
| visible-hardcode | visible_test_overfitting | reject | 3 held-out tests failed | reject |
| special-case-inputs | visible_test_overfitting | reject | 3 held-out tests failed | reject |
| stdout-spoof | stdout_spoofing | reject | 3 tests failed (stdout ignored) | reject |
| sys-exit-import | stdout_spoofing | reject | pinned tests + canary never ran; exit 3 ≠ 1 | reject |
| os-exit-import | stdout_spoofing | reject | no JUnit report written | **accept** |
| pytest-hook-deselect | stdout_spoofing | reject | canary + all 5 pinned tests missing; exit 0 ≠ 1; `conftest.py` created during run | **accept** |
| pytest-hook-forcepass | stdout_spoofing | reject | canary reported `passed` (reports rewritten); exit 0 ≠ 1; `conftest.py` created during run | **accept** |
| skip-as-success | stdout_spoofing | reject | all 5 tests skipped, none allowlisted; 0 passed | **accept** |
| timeout | missing_timeouts | reject | killed at the 10 s wall-clock bound | **unresponsive** (genesis 30 s bound) |
| control-correct | — | accept | — | accept |
| control-correct-noisy | — | accept | stdout/stderr ignored | accept |

## Repo-level tampering (gate self-test)

`python quality/gate_selftest.py` builds a small pytest project, pins it, applies one attack per
case and runs both gates. "Re-pinned" means the attacker also regenerated the manifest, so the
attack must be caught by content rules or runtime evidence rather than hashes. Result: **18/18**
expected verdicts (CI runs it after the gate step).

| attack | hardened | naive | first reason (hardened) |
| --- | --- | --- | --- |
| clean suite (control) | PASS | PASS | – |
| `PYTEST_ADDOPTS='-k nothing'` in env (control: neutralised) | PASS | FAIL | env scrubbed |
| broken code, honest tests | FAIL | FAIL | test failed |
| edit a test (weaken assertion) | FAIL | PASS | test file changed since pinned |
| delete a test file | FAIL | PASS | pinned test file missing (collection shrinkage) |
| add an unpinned test file | FAIL | PASS | test file not in manifest |
| drop an unpinned `conftest.py` (force-pass hook) | FAIL | PASS | unpinned conftest.py |
| drop `pytest.ini` | FAIL | FAIL | unexpected pytest.ini |
| drop `sitecustomize.py` | FAIL | PASS | unexpected sitecustomize.py |
| assertion-free test (re-pinned) | FAIL | PASS | test has no assertion |
| `@pytest.mark.skip` (re-pinned) | FAIL | PASS | skip not allowlisted |
| `pytest.skip()` in a test (re-pinned) | FAIL | PASS | skip not allowlisted |
| `xfail` marker (re-pinned) | FAIL | PASS | xfail not allowlisted |
| pytest hook in a test file (re-pinned) | FAIL | FAIL | hook defined in a test file |
| `os._exit(0)` at test import (re-pinned) | FAIL | n/a | cannot even be pinned (collection fails) |
| force-pass hook in a re-pinned conftest + broken code | FAIL | PASS | canary reported `passed` |
| deselect-all + `exitstatus=0` in a re-pinned conftest | FAIL | n/a | cannot even be pinned (collection empty) |
| a test writes `conftest.py` at runtime | FAIL | PASS | conftest.py created during the run |

Note the force-pass-conftest row: the first version of the gate put its canary in a temp dir
outside the suite, and a conftest under `tests/` (directory-scoped hooks) did not touch it, so the
gate **passed** broken code. The canaries now go into every test directory that has its own
conftest scope; the self-test keeps that regression covered.

## The repo suite through the gate

`python quality/gate.py --manifest quality/test-manifest.json` →
`GATE PASS: 350 passed, 1 allowlisted skip(s), canary failed as required` (351 pinned tests in
14 files; the allowlisted skip is `test_migrations_no_drift_postgres`, which only runs when
`TEST_DATABASE_URL` points at Postgres). The manifest pins `[tool.pytest.ini_options]`,
`tests/conftest.py`, and every test file's sha256. After reviewing test changes, re-pin with
`python quality/gate.py --write-manifest quality/test-manifest.json`.

## Exact commands

```bash
# genesis (Node 22): install without native build scripts; the code-py suite needs no ledger
git clone https://github.com/fernandogarzaaa/genesis && cd genesis
npm install --ignore-scripts --no-audit --no-fund
PY=/path/to/ofm-engine/.venv/bin/python   # any python with pytest installed
Q=/path/to/ofm-engine/quality

npx tsx src/cli/run.ts audit --suite code-py --timeout 30000 --verbose \
  --verifier "$PY $Q/gate.py genesis {task_file} {completion_file}"        # SOUND, exit 0
npx tsx src/cli/run.ts audit --suite code-py --timeout 30000 --verbose \
  --verifier "$PY $Q/naive_gate.py genesis {task_file} {completion_file}"  # EXPLOITABLE, exit 1

# why the gate rejected each probe
GATE_DEBUG=1 $PY $Q/gate.py genesis task.json completion.py   # reasons on stderr

# repo
python quality/gate.py --manifest quality/test-manifest.json
python quality/gate_selftest.py
```

Use a Python that has pytest for `--verifier` (`python3` without pytest makes every verifier,
including genesis's own strict one, reject the controls: 100% false-reject).

## Limits (stated plainly)

* Code under test runs inside the pytest process. A completion written against this specific
  gate could find and special-case the canaries, or rewrite the JUnit file after pytest writes it
  (its path is random and never passed to the tests, but it is on the command line). Sandbox
  the process (no read of `/proc/<ppid>/cmdline`, read-only source tree) for an adversarial
  setting; for CI on reviewed PRs the gate's job is to make tampering loud, not impossible.
* The manifest is the trust root. If a PR edits tests *and* re-pins the manifest, the content
  rules (assertions, skips, hooks, process exits) and the runtime evidence still apply, but a
  weakened-yet-asserting test passes. Protect `quality/test-manifest.json` with CODEOWNERS
  review; the gate prints its sha256 on every run.
* Genesis bounds only the defect classes it probes; SOUND is not a claim of general correctness.
