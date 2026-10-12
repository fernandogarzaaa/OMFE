#!/usr/bin/env bash
# testteeth mutation runs on the security/core modules (github.com/fernandogarzaaa/testteeth).
#   quality/mutation/run.sh before|after [module-name ...]
# Each module is mutated against the test files that target it (fast feedback); mutants are
# evenly sampled with a fixed cap so "before" and "after" grade the same mutant set.
set -uo pipefail
cd "$(dirname "$0")/../.."
PHASE="${1:-after}"; shift || true
CAP="${MUTANT_CAP:-60}"
OUT=quality/mutation/results
mkdir -p "$OUT"
# "before" = the original suite only; "after" adds the mutation-hardening test files.
SEC=""; DEPS=""; DOM=""
if [ "$PHASE" != before ]; then
  SEC=" tests/test_mutation_security.py"; DEPS=" tests/test_mutation_deps.py"; DOM=" tests/test_mutation_domain.py"
fi
declare -A TARGET=(
  [crypto]="src/ofm_engine/security/crypto.py|tests/test_security_units.py${SEC}"
  [passwords]="src/ofm_engine/security/passwords.py|tests/test_passwords.py${SEC}"
  [signing]="src/ofm_engine/security/signing.py|tests/test_security_units.py${SEC}"
  [ssrf]="src/ofm_engine/security/ssrf.py|tests/test_security_units.py${SEC}"
  [tokens]="src/ofm_engine/security/tokens.py|tests/test_security_units.py tests/test_auth.py${SEC}"
  [deps]="src/ofm_engine/api/deps.py|tests/test_rbac_tenancy.py tests/test_auth.py${DEPS}"
  [hashing]="src/ofm_engine/hashing.py|tests/test_domain_units.py${DOM}"
  [meta_capi]="src/ofm_engine/adapters/meta_capi.py|tests/test_domain_units.py${DOM}"
  [scoring]="src/ofm_engine/scoring.py|tests/test_domain_units.py${DOM}"
  [attribution]="src/ofm_engine/attribution.py|tests/test_domain_units.py tests/test_public.py${DOM}"
)
ORDER=(crypto passwords signing ssrf tokens deps hashing meta_capi scoring attribution)
[ "$#" -gt 0 ] && ORDER=("$@")
for name in "${ORDER[@]}"; do
  IFS='|' read -r path tests <<<"${TARGET[$name]}"
  echo "== $name ($path) vs $tests"
  testteeth run "$path" --pytest-args "$tests -p no:cacheprovider" --max-mutants "$CAP" -q \
    --json "$OUT/$name.$PHASE.json" > "$OUT/$name.$PHASE.txt" 2>&1
  grep -E "^Mutants:|^Mutation score:" "$OUT/$name.$PHASE.txt"
done
