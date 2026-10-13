#!/usr/bin/env bash
# End-to-end check of the built SPA against a running backend: every /api route the UI calls,
# with the same cookies + CSRF header the browser sends, plus the SSE stream.
# Usage: BASE=http://127.0.0.1:8000 scripts/e2e_ui.sh   (server seeded with `ofm seed-demo --known-passwords`)
set -uo pipefail
BASE="${BASE:-http://127.0.0.1:8000}"
EMAIL="${EMAIL:-admin@demo.agency}"
PASSWORD="${PASSWORD:-demo-pass-2026}"
JAR="$(mktemp)"; trap 'rm -f "$JAR" /tmp/e2e_body.$$' EXIT
pass=0; fail=0
RUN="$(date +%s | tail -c 6)$RANDOM"  # unique suffix so the script can be re-run against the same DB
ok()  { pass=$((pass+1)); echo "  ok   $1"; }
bad() { fail=$((fail+1)); echo "  FAIL $1"; }
csrf() { awk '$6=="csrf_token"{print $7}' "$JAR" | tail -1; }
# req METHOD PATH [json-body]  -> sets $CODE, body in /tmp/e2e_body.$$
req() {
  local m="$1" p="$2" d="${3:-}"
  local args=(-s -o "/tmp/e2e_body.$$" -w '%{http_code}' -b "$JAR" -c "$JAR" -X "$m" -H 'Accept: application/json')
  [ "$m" != GET ] && args+=(-H "X-CSRF-Token: $(csrf)")
  [ -n "$d" ] && args+=(-H 'Content-Type: application/json' -d "$d")
  CODE=$(curl "${args[@]}" "$BASE$p")
}
body() { cat "/tmp/e2e_body.$$"; }
jq_ok() { body | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if ($1) else 1)" 2>/dev/null; }
expect() { # name code [python-predicate-on-d]
  if [ "$CODE" = "$2" ] && { [ -z "${3:-}" ] || jq_ok "$3"; }; then ok "$1"; else bad "$1 (HTTP $CODE: $(body | head -c 200))"; fi
}

echo "SPA"
for path in / /login /app /app/pipeline /app/settings/integrations; do
  html=$(curl -s "$BASE$path")
  if echo "$html" | grep -q '<div id="root"' && echo "$html" | grep -q '/assets/index-'; then ok "SPA $path"; else bad "SPA $path"; fi
done
asset=$(curl -s "$BASE/" | grep -o '/assets/index-[^"]*\.js' | head -1)
[ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE$asset")" = 200 ] && ok "asset $asset" || bad "asset $asset"
curl -sI "$BASE/" | grep -qi 'content-security-policy' && ok "CSP header on SPA" || bad "CSP header on SPA"

echo "Auth"
req POST /api/v1/auth/login "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}"; expect "login" 200 "d['user']['role']=='admin' and d['user']['tenant']['slug']=='demo'"
[ -n "$(csrf)" ] && ok "csrf cookie issued" || bad "csrf cookie issued"
req GET /api/v1/auth/me; expect "me" 200 "'tenant' in d and 'totp_enabled' in d"
req POST /api/v1/auth/refresh; expect "refresh -> {user}" 200 "d['user']['email']=='$EMAIL'"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -X POST "$BASE/api/v1/auth/refresh"); [ "$CODE" = 403 ] && ok "refresh without CSRF -> 403" || bad "refresh without CSRF ($CODE)"

echo "Business routes"
req GET "/api/v1/kpis?range=7d"; expect "kpis 7d" 200 "all(k in d for k in ('clicks','leads','signed','subs','revenue_cents','conversion','by_channel','by_creator','timeseries'))"
req GET "/api/v1/kpis?range=90d"; expect "kpis 90d" 200 "d['range']=='90d'"
req GET "/api/v1/creators?limit=200"; expect "creators" 200 "set(d)=={'items','next_cursor'} and len(d['items'])>0"
CID=$(body | python3 -c "import json,sys; print(json.load(sys.stdin)['items'][0]['id'])")
req POST /api/v1/creators "{\"name\":\"E2E Creator\",\"handle\":\"e2e_$RUN\",\"status\":\"onboarding\",\"channels\":[\"instagram\"]}"; expect "create creator" 201 "d['handle']=='e2e_$RUN'"
NEWC=$(body | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
req PATCH "/api/v1/creators/$NEWC" '{"status":"active"}'; expect "patch creator" 200 "d['status']=='active'"
req GET /api/v1/pipeline; expect "pipeline" 200 "[s['key'] for s in d['stages']]==['new','contacted','call_booked','signed','lost']"
LID=$(body | python3 -c "import json,sys; d=json.load(sys.stdin); print(next(l['id'] for s in d['stages'] for l in s['leads']))")
req GET "/api/v1/leads?stage=new"; expect "leads?stage=new" 200 "all(l['stage']=='new' for l in d['items'])"
req PATCH "/api/v1/leads/$LID" '{"stage":"contacted"}'; expect "move lead" 200 "d['stage']=='contacted'"
req POST "/api/v1/leads/$LID/rescore"; expect "rescore lead" 200 "isinstance(d['score'],int)"
req GET /api/v1/links; expect "links" 200 "'clicks' in d['items'][0]"
req POST /api/v1/links "{\"slug\":\"e2e-$RUN\",\"creator_id\":\"$CID\",\"channel\":\"instagram\",\"destination_url\":\"https://example.com/e2e\",\"campaign\":\"e2e\"}"; expect "create link" 201 "d['slug']=='e2e-$RUN'"
NEWL=$(body | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
req PATCH "/api/v1/links/$NEWL" '{"campaign":"e2e-2"}'; expect "patch link" 200 "d['campaign']=='e2e-2'"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -A "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148" "$BASE/l/e2e-$RUN"); [ "$CODE" = 302 ] || [ "$CODE" = 200 ] && ok "redirect /l/e2e-$RUN ($CODE)" || bad "redirect /l/e2e-$RUN ($CODE)"
printf 'Day,Model,Earnings,Subs\n2026-10-01,e2e_%s,123.45,3\n' "$RUN" > /tmp/e2e_rev.$$.csv
CODE=$(curl -s -o "/tmp/e2e_body.$$" -w '%{http_code}' -b "$JAR" -c "$JAR" -H "X-CSRF-Token: $(csrf)" \
  -F "file=@/tmp/e2e_rev.$$.csv;type=text/csv" -F 'mapping={"date":"Day","creator":"Model","gross":"Earnings","new_subs":"Subs"}' "$BASE/api/v1/revenue/import")
rm -f /tmp/e2e_rev.$$.csv
expect "revenue import (UI mapping keys)" 200 "d['imported']==1 and d['revenue_cents']==12345 and 'skipped_duplicates' in d"
req GET "/api/v1/revenue?creator_id=$NEWC&from=2026-10-01&to=2026-10-31"; expect "revenue list" 200 "d['items'][0]['revenue_cents']==12345 and d['items'][0]['new_subs']==3"
req POST /api/v1/ai/copy '{"kind":"headline","brief":"We grow fitness creators","variants":2}'; expect "ai copy" 200 "len(d['variants'])==2 and d['mock'] is True"

echo "Automations / admin"
req POST /api/v1/automations/webhooks '{"url":"https://hooks.zapier.com/e2e-placeholder","events":["lead.created","link.clicked","creator.updated"]}'; expect "create webhook" 201 "d['secret'].startswith('whsec_')"
WID=$(body | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
req GET /api/v1/automations/webhooks; expect "list webhooks" 200 "any(w['id']=='$WID' for w in d['items'])"
req DELETE "/api/v1/automations/webhooks/$WID"; expect "delete webhook" 200
req GET /api/v1/automations/jobs; expect "jobs" 200 "set(d)=={'items','next_cursor'}"
req GET "/api/v1/automations/jobs?status=dead"; expect "jobs?status=dead" 200
req POST /api/v1/api-keys '{"name":"e2e"}'; expect "create api key" 201 "d['key'].startswith('ofm_')"
KID=$(body | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
req GET /api/v1/api-keys; expect "list api keys" 200 "any(k['id']=='$KID' for k in d['items'])"
req DELETE "/api/v1/api-keys/$KID"; expect "revoke api key" 200
req GET /api/v1/integrations; expect "integrations" 200 "sorted(i['provider'] for i in d)==['ghl','llm','meta_capi','tiktok']"
req POST /api/v1/integrations/llm/test; expect "integration test" 200 "d['mode']=='mock'"
req GET /api/v1/users; expect "users" 200 "len(d['items'])>=2"
req POST /api/v1/users "{\"email\":\"e2e.$RUN@example.org\",\"name\":\"E2E\",\"role\":\"member\"}"; expect "create user" 201 "bool(d.get('temporary_password'))"
req GET "/api/v1/audit?limit=100"; expect "audit" 200 "all('actor' in a and 'created_at' in a for a in d['items'])"
req POST /api/v1/auth/totp/setup; expect "totp setup" 200 "d['otpauth_uri'].startswith('otpauth://')"

echo "SSE"
curl -s -N --max-time 4 -b "$JAR" -H "Last-Event-ID: 0" "$BASE/api/v1/live/stream?max_seconds=2.5" > /tmp/e2e_sse.$$ 2>/dev/null
grep -q '^event: event' /tmp/e2e_sse.$$ && ok "SSE replay events" || bad "SSE replay events"
grep -q '^event: kpi' /tmp/e2e_sse.$$ && ok "SSE kpi delta" || bad "SSE kpi delta"
grep -q '"type":"click"' /tmp/e2e_sse.$$ && ok "SSE click from /l/ redirect" || bad "SSE click"
rm -f /tmp/e2e_sse.$$

req POST /api/v1/auth/logout; expect "logout" 200
req GET /api/v1/auth/me; expect "me after logout -> 401" 401
echo "e2e_ui: $pass passed, $fail failed"
[ "$fail" = 0 ]
