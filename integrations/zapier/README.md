# OFM Engine + Zapier

OFM Engine has no paid Zapier app; use Zapier's built-in **Webhooks by Zapier** (included in
paid Zapier plans) and **API key** auth. Everything below works against a mock-mode install.

## 1. Trigger: new creator lead → any Zapier action
1. In Zapier create a Zap with **Webhooks by Zapier → Catch Raw Hook** (raw keeps the exact body
   for signature checks). Copy the hook URL.
2. In OFM Engine: **Automations → Webhooks → Add**, paste the URL, choose `lead.created`
   (or `lead.stage_changed`, `revenue.imported`, `*`). Copy the secret — it is shown once.
   (Zapier hook hosts resolve to public IPs, so they pass the SSRF check. Deliveries are sent
   for real only when the server runs with `OUTBOUND_WEBHOOKS_LIVE=true`; otherwise they are
   signed, logged and marked delivered in mock mode.)
3. Add **Code by Zapier (JavaScript)** to verify the signature and replay window:

```js
const crypto = require('crypto');
const header = inputData.signature;            // map: Headers > X-Ofm-Signature
const raw = inputData.raw_body;                // map: Raw Body
const parts = Object.fromEntries(header.split(',').map(p => p.trim().split('=')));
const expected = crypto.createHmac('sha256', inputData.secret).update(parts.t + '.' + raw).digest('hex');
if (Math.abs(Date.now() / 1000 - Number(parts.t)) > 300 || expected !== parts.v1) throw new Error('bad signature');
output = JSON.parse(raw).data;                 // {lead_id, name, score, tier, stage, followers, ...}
```
4. Continue with any action (Google Sheets row, Gmail, Slack, HubSpot ...).

## 2. Action: call the OFM Engine API from a Zap
Use **Webhooks by Zapier → Custom Request** with header `X-API-Key: <key from Settings → API keys>`:

| Goal | Method & URL |
|---|---|
| Fetch a lead | `GET {BASE}/api/v1/leads/{id}` |
| Move a lead stage | `PATCH {BASE}/api/v1/leads/{id}` body `{"stage":"call_booked"}` |
| Re-score | `POST {BASE}/api/v1/leads/{id}/rescore` |
| KPIs | `GET {BASE}/api/v1/kpis?range=7d` |
| Retry a dead job | `POST {BASE}/api/v1/automations/jobs/{id}/retry` |

API keys never need a CSRF token. They cannot manage users, integrations, API keys or the
audit log (admin session only).

## 3. Inbound signed callback (Zapier → OFM Engine)
`POST {BASE}/api/v1/hooks/inbound/{tenant_slug}` with header
`X-OFM-Signature: t=<unix>,v1=<hex hmac_sha256(INBOUND_HMAC_SECRET, "<t>.<body>")>` and body
`{"type":"lead.stage","lead_id":"...","stage":"signed"}`. Each signature is accepted once
(replays get 409) and only within 5 minutes.
