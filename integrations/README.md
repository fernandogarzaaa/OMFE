# Automation integrations

| Folder | What | Import |
|---|---|---|
| `n8n/01_new_lead_score_ghl_alert.json` | OFM `lead.created` webhook → verify HMAC → rescore → (hot/warm) GHL tag + Discord + Slack | n8n → Workflows → Import from file |
| `n8n/02_daily_kpi_digest.json` | 09:00 daily → `GET /kpis?range=7d` → Slack/Discord digest | same |
| `n8n/03_capi_retry.json` | every 30 min → dead `meta_capi.send` jobs → `POST /automations/jobs/{id}/retry` | same |
| `make/ofm_new_lead_blueprint.json` | Custom webhook → fetch lead → Google Sheets + Slack | Make → Scenarios → Import blueprint |
| `zapier/README.md` | Webhooks by Zapier recipes (trigger, actions, inbound callback) | manual |

n8n setup: create a **Header Auth** credential named `OFM Engine API key (X-API-Key)` with
header `X-API-Key`, and set env vars `OFM_BASE_URL`, `OFM_WEBHOOK_SECRET`, `SLACK_WEBHOOK_URL`,
`DISCORD_WEBHOOK_URL` (and optionally `GHL_TOKEN`). All workflows import inactive.
