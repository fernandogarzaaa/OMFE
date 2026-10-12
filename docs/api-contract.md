# OFM Engine REST contract (v1) — shared by backend and frontend
Base: /api/v1. JSON. Auth: httpOnly cookies `access_token` (15 min JWT) + `refresh_token` (rotating); CSRF: double-submit cookie `csrf_token` echoed in header `X-CSRF-Token` on non-GET. API keys: header `X-API-Key` (automation tools). Errors: {"error":{"code":str,"message":str}}. Lists: ?limit=&cursor= -> {"items":[...],"next_cursor":str|null}. All business data scoped to the caller's tenant.

Auth: POST /auth/login {email,password,totp?} -> {user} | 401 | {"error":{"code":"totp_required"}}; POST /auth/refresh; POST /auth/logout; GET /auth/me -> {id,email,name,role:"admin"|"member",tenant:{id,name},totp_enabled}; POST /auth/totp/setup -> {otpauth_uri}; POST /auth/totp/verify {code}.
Users (admin): GET/POST /users, PATCH/DELETE /users/{id}.
Creators: GET/POST /creators, GET/PATCH/DELETE /creators/{id}. Creator {id,name,handle,status:"active"|"paused"|"onboarding",channels:[str],created_at}.
Recruitment leads: GET /leads?stage=, GET/PATCH /leads/{id}, POST /leads/{id}/rescore. Lead {id,name,email,phone,socials:{},followers:int,earnings_range,timezone,score:int,score_reasons:[str],stage:"new"|"contacted"|"call_booked"|"signed"|"lost",ghl_contact_id,created_at}. Public: POST /public/{tenant_slug}/apply (rate-limited, requires age_confirmed=true & consent=true else 422).
Pipeline: GET /pipeline -> {stages:[{key,label,count,leads:[Lead]}]}.
Tracking links: GET/POST /links, PATCH/DELETE /links/{id}. Link {id,slug,creator_id,channel,destination_url,campaign,clicks:int,created_at}. Public redirect GET /l/{slug} (outside /api).
Revenue: POST /revenue/import (multipart csv + mapping json), GET /revenue?creator_id=&from=&to=.
KPIs: GET /kpis?range=7d|30d|90d -> {clicks,leads,signed,subs,revenue_cents,conversion:{click_to_lead,lead_to_signed},by_channel:[{channel,clicks,leads,revenue_cents}],by_creator:[{creator_id,name,revenue_cents,subs}],timeseries:[{date,clicks,leads,revenue_cents}]}.
Live: GET /live/stream (SSE). Events: `kpi` (same shape as /kpis delta), `event` {type:"click"|"lead"|"capi_sent"|"revenue_import",payload,at}.
Automations: GET /automations/webhooks, POST /automations/webhooks {url,events:[str]} -> {id,secret(shown once)}, DELETE /automations/webhooks/{id}; GET /automations/jobs?status=; GET/POST/DELETE /api-keys (key shown once).
AI copy: POST /ai/copy {kind:"recruitment_ad"|"headline"|"followup_sms"|"followup_email"|"social_caption",brief,variants:int<=5} -> {variants:[str],provider,mock:bool} | 422 {"error":{"code":"content_refused"}}.
Integrations (admin): GET /integrations -> [{provider:"ghl"|"meta_capi"|"tiktok"|"llm",configured:bool,mode:"mock"|"live",fields:[{name,set:bool}]}]; PUT /integrations/{provider} {fields:{...}} (write-only, never returned); POST /integrations/{provider}/test -> {ok,mode,detail}.
Audit (admin): GET /audit.
Health: GET /healthz, /readyz, /metrics (token).
