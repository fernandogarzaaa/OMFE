#!/usr/bin/env bash
# End-to-end smoke test: boots the server in MOCK mode on a throwaway SQLite DB and exercises the
# public funnel, tracking redirect, apply (incl. under-18 rejection), login, KPIs, CSRF, integrations, SSE.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-.venv/bin/python}
OFM=${OFM:-.venv/bin/ofm}
PORT=${PORT:-8765}
BASE="http://127.0.0.1:${PORT}"
WORK=$(mktemp -d)
JAR="$WORK/cookies.txt"
export DATABASE_URL="sqlite:///${WORK}/smoke.db" ENV=development LOG_JSON=false LOG_LEVEL=WARNING \
  RUN_WORKER_IN_PROCESS=true WORKER_POLL_INTERVAL=0.2 RATE_LIMIT_ENABLED=true \
  ARGON2_TIME_COST=${ARGON2_TIME_COST:-2} ARGON2_MEMORY_COST=${ARGON2_MEMORY_COST:-19456} ARGON2_PARALLELISM=1
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  ✔ $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  ✘ $1"; }
check(){ if [ "$2" = "$3" ]; then ok "$1 ($2)"; else bad "$1 (got $2, want $3)"; fi; }
cleanup(){ [ -n "${SRV:-}" ] && kill "$SRV" 2>/dev/null || true; rm -rf "$WORK"; }
trap cleanup EXIT

echo "→ seeding demo tenant"
ADMIN_PW="Smoke-Admin-Passw0rd-1"
$OFM seed-demo --admin-password "$ADMIN_PW" --member-password "Smoke-Member-Passw0rd-1" >"$WORK/seed.txt"
grep -q "admin@example.com  password:" "$WORK/seed.txt" && ok "seed-demo printed credentials" || bad "seed-demo output"

echo "→ booting server on $BASE"
$PY -m uvicorn ofm_engine.app:create_app --factory --host 127.0.0.1 --port "$PORT" --log-level warning \
  >"$WORK/server.log" 2>&1 &
SRV=$!
for _ in $(seq 1 50); do curl -fs "$BASE/healthz" >/dev/null 2>&1 && break; sleep 0.2; done
check "GET /healthz" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/healthz")" 200
check "GET /readyz" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/readyz")" 200

echo "→ public funnel"
FUNNEL=$(curl -s "$BASE/apply/demo?v=B&utm_source=smoke")
echo "$FUNNEL" | grep -q 'data-variant="B"' && ok "funnel page renders variant B" || bad "funnel variant"
echo "$FUNNEL" | grep -q "18 years of age or older" && ok "funnel shows 18+ confirmation" || bad "funnel 18+ text"
HDRS=$(curl -s -D - -o /dev/null "$BASE/apply/demo")
echo "$HDRS" | grep -qi "content-security-policy: default-src 'self'" && ok "CSP header present" || bad "CSP header"
check "link-in-bio page" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/c/demo/miarose")" 200

echo "→ tracking redirect"
R=$(curl -s -D - -o /dev/null -A "Mozilla/5.0 (iPhone) Mobile Safari" "$BASE/l/miarose-instagram?fbclid=SMOKE123&utm_medium=bio")
echo "$R" | grep -q "^HTTP/1.1 302" && ok "redirect 302" || bad "redirect status"
echo "$R" | grep -qi "^location: https://onlyfans.com/miarose?.*utm_source=instagram" && ok "redirect destination + UTM" || bad "redirect location"
echo "$R" | grep -qi "referrer-policy: no-referrer" && ok "Referrer-Policy no-referrer" || bad "referrer policy"
echo "$R" | grep -qi "set-cookie: _fbc=fb.1." && ok "_fbc cookie from fbclid" || bad "_fbc cookie"
P=$(curl -s -A "Mozilla/5.0 (iPhone) Mobile Safari" "$BASE/l/demo-recruit")
echo "$P" | grep -q 'data-event-id=' && ok "pixel interstitial carries event_id" || bad "interstitial"

echo "→ apply endpoint"
APPLY='{"name":"Smoke Test","email":"smoke@example.net","followers":{"instagram":42000},"earnings_range":"1k-5k","age_confirmed":true,"consent":true,"variant":"B"}'
check "apply 18+ & consent" "$(curl -s -o "$WORK/apply.json" -w '%{http_code}' -H 'Content-Type: application/json' -d "$APPLY" "$BASE/api/v1/public/demo/apply")" 201
UNDER='{"name":"Too Young","email":"young@example.net","age_confirmed":false,"consent":true}'
check "apply under-18 -> 422" "$(curl -s -o "$WORK/u18.json" -w '%{http_code}' -H 'Content-Type: application/json' -d "$UNDER" "$BASE/api/v1/public/demo/apply")" 422
grep -q age_and_consent_required "$WORK/u18.json" && ok "under-18 error code" || bad "under-18 error code"

echo "→ auth"
check "login wrong password -> 401" "$(curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d '{"email":"admin@example.com","password":"nope"}' "$BASE/api/v1/auth/login")" 401
check "login admin" "$(curl -s -c "$JAR" -b "$JAR" -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d "{\"email\":\"admin@example.com\",\"password\":\"$ADMIN_PW\"}" "$BASE/api/v1/auth/login")" 200
CSRF=$(awk '$6=="csrf_token"{print $7}' "$JAR" | tail -1)
[ -n "$CSRF" ] && ok "csrf_token cookie issued" || bad "csrf cookie"
check "GET /auth/me" "$(curl -s -b "$JAR" -o /dev/null -w '%{http_code}' "$BASE/api/v1/auth/me")" 200

echo "→ KPIs"
KPI=$(curl -s -b "$JAR" "$BASE/api/v1/kpis?range=30d")
echo "$KPI" | $PY -c 'import json,sys; k=json.load(sys.stdin); assert k["clicks"]>0 and k["revenue_cents"]>0 and k["leads"]>0 and len(k["timeseries"])==30' \
  && ok "KPIs populated" || bad "KPIs"

echo "→ CSRF enforcement"
check "POST without CSRF header -> 403" "$(curl -s -b "$JAR" -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d '{"name":"X","handle":"smoke-x"}' "$BASE/api/v1/creators")" 403
check "POST with CSRF header -> 201" "$(curl -s -b "$JAR" -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" -d '{"name":"X","handle":"smoke-x"}' "$BASE/api/v1/creators")" 201

echo "→ integrations"
IT=$(curl -s -b "$JAR" -X POST -H "X-CSRF-Token: $CSRF" "$BASE/api/v1/integrations/meta_capi/test")
echo "$IT" | grep -q '"ok":true' && echo "$IT" | grep -q '"mode":"mock"' && ok "integration test (mock)" || bad "integration test: $IT"
AI=$(curl -s -b "$JAR" -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" -d '{"kind":"social_caption","brief":"explicit nsfw pics"}' "$BASE/api/v1/ai/copy")
echo "$AI" | grep -q content_refused && ok "AI guardrail refuses explicit" || bad "AI guardrail"

echo "→ worker (in-process) drains outbox"
sleep 1.5
JOBS=$(curl -s -b "$JAR" "$BASE/api/v1/automations/jobs?status=succeeded")
echo "$JOBS" | grep -q '"kind":"meta_capi.send"' && ok "CAPI job succeeded (mock)" || bad "jobs: $JOBS"

echo "→ SSE live stream"
( sleep 0.6; curl -s -o /dev/null -H 'Content-Type: application/json' \
   -d '{"name":"Live Lead","email":"live@example.net","age_confirmed":true,"consent":true}' "$BASE/api/v1/public/demo/apply" ) &
SSE=$(curl -s -N -b "$JAR" --max-time 6 "$BASE/api/v1/live/stream?max_seconds=3" || true)
echo "$SSE" | grep -q '^event: event' && echo "$SSE" | grep -q '"type":"lead"' && ok "SSE delivered live lead event" || bad "SSE: ${SSE:0:200}"
echo "$SSE" | grep -q '^event: kpi' && ok "SSE kpi delta" || bad "SSE kpi"

echo
echo "smoke: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || { echo "--- server log ---"; tail -40 "$WORK/server.log"; exit 1; }
