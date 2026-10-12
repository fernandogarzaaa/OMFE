# GoHighLevel sub-account blueprint

This is the GoHighLevel (GHL) setup OFM Engine expects, built by hand in a sub-account (or saved
as a GHL snapshot once built). Every name below matches what the code sends or reads — see
`src/ofm_engine/adapters/ghl.py` and the `ghl.*` handlers in `src/ofm_engine/services/jobs.py`.

## What OFM Engine does in GHL

For every new application (job `ghl.upsert_lead`), using the LeadConnector v2 API
(`https://services.leadconnectorhq.com`, header `Version: 2021-07-28`):

1. `POST /contacts/upsert` — `firstName`, `lastName`, `name`, `email`, `phone`, `timezone`,
   `source` (default **`OFM Engine funnel`**), `tags`, and `customFields` for any mapped field.
2. `POST /contacts/{id}/tags` — the tenant's base tags plus **`tier-hot`**, **`tier-warm`** or
   **`tier-cold`** (the add-tags endpoint is used so existing tags are never overwritten).
3. `POST /contacts/{id}/notes` — *"OFM Engine score NN/100 (tier)."* followed by the score
   breakdown, one reason per line.
4. `POST /contacts/{id}/workflow/{workflowId}` — only if `workflow_id` is configured.
5. Job `ghl.opportunity`: `POST /opportunities/` in the configured pipeline at the stage mapped
   from the lead's stage, named *"<name> — creator application"*, status `open`.

On every later stage change (UI, API, or signed inbound callback) the opportunity is moved with
`PUT /opportunities/{id}`: `pipelineStageId` from the stage map and status `won` for `signed`,
`lost` for `lost`, `open` otherwise.

The connection test (Settings → Integrations → Test, or `ofm send-test-event --provider ghl`)
calls `GET /locations/{locationId}`.

## 1. Private Integration

Sub-account → **Settings → Private Integrations → Create new integration**. Grant at least the
scopes for contacts (read/write), contact tags and notes, opportunities (read/write), workflows
(add contact to workflow), locations (read) and, if you want to list them, calendars and custom
fields (read). Copy the token (`pit-…`) once and paste it into **Settings → Integrations →
GoHighLevel → token** in OFM Engine; it is stored encrypted and never shown again.

Location ID → **Settings → Integrations → GoHighLevel → location_id**. GHL is live as soon as
both are set.

## 2. Pipeline and stages

Create one pipeline, e.g. **Creator Recruitment**, with these stages in order:

| OFM Engine stage key | Suggested GHL stage name | Meaning |
|---|---|---|
| `new` | New Application | Applied through the funnel |
| `contacted` (alias `qualified`) | Contacted / Qualified | First touch made, profile reviewed |
| `call_booked` (alias `booked`) | Intro Call Booked | Discovery call scheduled |
| `signed` | Signed | Contract signed (opportunity → **won**) |
| `lost` | Lost | Not a fit / no response (opportunity → **lost**) |

Put the pipeline id in **Settings → Integrations → GoHighLevel → pipeline_id**, and the stage ids
in the agency YAML (stage ids are not editable from the Settings page):

```yaml
# clients/<slug>.yaml
ghl:
  location_id: ""          # or set it in Settings
  pipeline_id: ""          # or set it in Settings
  stages:
    new: "<stage id>"
    contacted: "<stage id>"
    call_booked: "<stage id>"
    signed: "<stage id>"
    lost: "<stage id>"
  base_tags: [ofm-recruit]
  source: OFM Engine funnel
```

Stage and pipeline ids can be read from `GET /opportunities/pipelines?locationId=…` with the
same token, or from the pipeline URL/settings in GHL. A stage without a mapping falls back to a
mock id and the live call fails, so map all five.

## 3. Custom fields (optional)

Create these **contact** custom fields (Settings → Custom Fields) and map their **field ids** in
YAML. Only mapped fields are sent.

| OFM key | Suggested field name / merge key | Type | Value sent |
|---|---|---|---|
| `lead_score` | Lead Score — `contact.lead_score` | Number | 0–100 |
| `total_followers` | Total Followers — `contact.total_followers` | Number | sum across platforms |
| `earnings_range` | Earnings Range — `contact.earnings_range` | Single line text (or dropdown with the values below) | `none`, `0-1k`, `1k-5k`, `5k-10k`, `10k-25k`, `25k+` (default funnel options) |

```yaml
ghl:
  custom_fields:
    lead_score: "<field id>"
    total_followers: "<field id>"
    earnings_range: "<field id>"
```

The hot-lead sequence uses `{{contact.lead_score}}`, so keep that merge key if you use it.

Recommended extra field, written by your automation rather than by the engine:
**OFM Lead ID** (`contact.ofm_lead_id`, text). OFM Engine v1.0 does not write its own lead id to
GHL; store it from your `lead.created` automation (the webhook payload and `GET /leads/{id}`
both carry `lead_id`, and the lead object carries `ghl_contact_id`) if you want GHL workflows to
move leads back (see §6).

## 4. Tags

| Tag | Added by | Use |
|---|---|---|
| `ofm-recruit` (plus any `ghl.base_tags`) | OFM Engine, every applicant | trigger the "New applicant" workflow |
| `tier-hot` · `tier-warm` · `tier-cold` | OFM Engine, from the score | trigger the hot-lead fast-track; segment follow-up |
| `n8n-hot` · `n8n-warm` | the bundled n8n workflow `01_new_lead_score_ghl_alert` (optional) | marks leads that triggered an alert |

The demo tenant also adds `demo`.

## 5. Custom values, calendar and workflows

- **Custom value** `booking_link` (Settings → Custom Values) = your intro-call booking URL. The
  sequences use `{{custom_values.booking_link}}`; also put the same URL in
  `funnel.booking_url` so the funnel shows a "Book your intro call" button after applying.
- **Calendar**: one round-robin or personal calendar for the 15-minute intro call.
- **Workflows** — build these from the SFW templates in [`sequences/`](../sequences/), each with
  the opt-out line kept on every SMS:

| Workflow | Trigger | Steps (from the template) |
|---|---|---|
| New applicant | Contact tag added: `ofm-recruit` | SMS now → email +5 min → SMS +1 day if no appointment → email +3 days if no appointment |
| Hot lead fast-track | Contact tag added: `tier-hot` | internal alert now → SMS +2 min → manual call task +4 h |
| Missed intro call | Appointment status: no-show | SMS +10 min → email +1 day |
| Stage sync (optional) | Appointment booked / opportunity stage changed | Webhook action → your n8n/Make/Zapier scenario (see §6) |

If you also set `workflow_id` in OFM Engine, the contact is enrolled directly in that workflow;
use either a tag trigger **or** `workflow_id` for the same workflow, not both, to avoid double
enrolment. The `workflow_webhook_url` field (a workflow's "Inbound Webhook" trigger URL) is stored
encrypted for future use; v1.0 does not call it.

## 6. Syncing stage changes back to OFM Engine

GHL cannot compute an HMAC itself, so stage changes made in GHL reach OFM Engine through an
automation tool:

1. GHL workflow (e.g. *Appointment booked*) → **Webhook** action → n8n/Make/Zapier, sending
   `{{contact.ofm_lead_id}}` and the new stage.
2. The automation signs and posts to
   `POST {PUBLIC_BASE_URL}/api/v1/hooks/inbound/{tenant_slug}` with
   `X-OFM-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>.<body>")>` and body
   `{"type":"lead.stage","lead_id":"<uuid>","stage":"call_booked"}`.
3. The secret is the tenant's `INBOUND_HMAC_SECRET`, set as the environment variable
   `CLIENT_<SLUG>_INBOUND_HMAC_SECRET` (e.g. `CLIENT_ACME_AGENCY_INBOUND_HMAC_SECRET`). Without
   it the endpoint answers `403 inbound_disabled`.

Valid stages: `new`, `contacted`, `call_booked`, `signed`, `lost`. Signatures older than 5 minutes
or seen before are rejected (`401` / `409 replayed`). Alternatively, call
`PATCH /api/v1/leads/{id}` with an `X-API-Key` (see [integrations/zapier/README.md](../integrations/zapier/README.md)).

## 7. Location settings to check

- **Allow Duplicate Contact** (Settings → Business Profile / General): the upsert endpoint honours
  this setting; leave duplicates **off** so re-applications update the same contact.
- Phone numbers / A2P registration and STOP handling for SMS — see
  [compliance.md](compliance.md#4-sms-and-email-follow-up-tcpa-can-spam-and-similar).
- Rate limits: GHL allows 100 requests per 10 seconds burst and 200,000 per day per app per
  sub-account; the worker retries 429s with backoff and honours `Retry-After`.

## 8. Verify

1. Paste token + location id → **Test** → `location <your sub-account name>`.
2. Submit a test application at `/apply/<slug>` with your own email.
3. In GHL: contact exists with `ofm-recruit` + `tier-*` tags, the score note, mapped custom
   fields, and an opportunity in **New Application**.
4. Move the lead to *Intro Call Booked* in the OFM Engine pipeline → the opportunity moves.
5. **Automations → Jobs** in OFM Engine should show the `ghl.*` jobs as `succeeded`; a `dead` job
   shows the provider's error and can be retried.
