# Onboarding checklist — taking a new agency live

A practical plan for the first day and the first week with a new agency. Each step names the
screen or command that does it. Everything can be rehearsed in mock mode first; integrations
switch to live one at a time as credentials are added.

Roles: **Ops** = whoever runs OFM Engine (tech & automations), **Agency** = the agency owner or
recruitment lead.

## Before day 1 — collect access

| Need | From | Used for |
|---|---|---|
| GoHighLevel sub-account admin (or a Private Integration token + location id) | Agency | CRM sync — [ghl-snapshot.md](ghl-snapshot.md) |
| Meta Business Manager access to the ad account and dataset/Pixel | Agency | Pixel + Conversions API |
| TikTok Ads Manager access (if they run TikTok) | Agency | Events API |
| Brand basics: name, tagline, colour (`#RRGGBB`), logo URL, support email, privacy-policy URL | Agency | funnel and bio pages |
| Intro-call booking link (GHL calendar) | Agency | thank-you page and follow-ups |
| Roster: creator display names, handles, the channels they promote on | Agency | creators + tracking links |
| A recent revenue export (OnlyFans statement, Infloww, Supercreator …) | Agency | CSV mapping |
| Who needs logins, and who is admin | Agency | Team |

Never ask for creators' OnlyFans passwords — OFM Engine does not need or store them
([compliance.md](compliance.md#3-no-onlyfans-scraping-or-account-automation--by-design)).

## Day 1 — stand up the tenant (mock mode)

- [ ] **Deploy** (or reuse an existing deployment) with production secrets set and `ofm doctor`
      clean — [deploy.md](deploy.md).
- [ ] **Create the tenant and first admin**:
      `ofm init-client acme-agency --name "Acme Agency" --admin-email owner@acme.example`
      (prints a one-time password; the YAML is written to `clients/acme-agency.yaml`).
- [ ] **Fill the agency YAML** (`clients/<slug>.yaml`, no secrets): `brand.*`, funnel variants
      A/B (`funnel.variants`), `funnel.benefits`, `funnel.booking_url`, `funnel.privacy_url`,
      `target_timezones`, scoring tiers, and the `revenue_mapping` that matches their export.
      Commit it so config changes are reviewed.
- [ ] **Admin signs in**, changes the password and turns on **2FA** (Settings → Security).
- [ ] **Invite the team** (Settings → Team): admins only for people who manage integrations,
      members for recruiters and chatters' managers.
- [ ] **Add creators** (Creators page) with display name, handle, bio and links — their
      link-in-bio pages go live at `/c/<slug>/<handle>`.
- [ ] **Create tracking links** (Links page) per creator × channel × campaign (e.g.
      `mia-ig-bio`, `mia-tt-bio`, `recruit-meta-oct`). Leave **CAPI on click off** for links that
      land on adult content.
- [ ] **Walk the funnel** at `/apply/<slug>` (try `?v=A` and `?v=B`) and submit a test
      application; confirm it appears on the Pipeline and in the live feed.
- [ ] **Import one revenue export** (Revenue page, or
      `ofm import-revenue export.csv --tenant <slug> --mapping '{"date":"…","creator":"…","gross":"…"}'`).
      Creator values are matched to creators by handle or display name (case-insensitive);
      re-importing the same file skips duplicates.

Exit criteria: the agency team can sign in, the funnel and bio pages render with their brand,
and the dashboard shows their real revenue.

## Day 2–3 — connect the CRM

- [ ] Build the GHL pipeline, custom fields, tags, custom value `booking_link`, calendar and
      workflows exactly as in [ghl-snapshot.md](ghl-snapshot.md).
- [ ] Settings → Integrations → GoHighLevel: `token`, `location_id`, `pipeline_id`
      (optional `workflow_id`); put the five stage ids and any custom-field ids in the YAML.
- [ ] **Test** → `location <name>`; submit a test application; verify contact, tags, note and
      opportunity in GHL; move the lead on the Pipeline and watch the opportunity follow.
- [ ] Check **Automations → Jobs**: everything `succeeded`; fix and retry anything `dead`.
- [ ] Load the follow-up sequences from [`sequences/`](../sequences/) into GHL workflows; confirm
      every SMS keeps "Reply STOP to opt out." and that STOP replies set DND.

## Day 3–4 — tracking and attribution

- [ ] Settings → Integrations → Meta CAPI: `pixel_id`, `access_token`, and a `test_event_code`.
- [ ] Submit a test application; in Meta Events Manager → Test events, confirm one browser and
      one server `Lead` with the same event id (deduplicated).
- [ ] Clear `test_event_code`; check Event Match Quality after real traffic arrives.
- [ ] TikTok (if used): `access_token`, `pixel_code`, `features.tiktok_events: true`; test.
- [ ] Put the tracking links in every bio, story and campaign; confirm clicks appear on the
      dashboard (bot clicks are filtered out of the counts).
- [ ] Review recruitment ad creatives against [compliance.md](compliance.md#2-meta-advertising-rules-for-adult-adjacent-agencies)
      before any spend; ad set minimum age 18.

## Day 4–5 — automations and alerts

- [ ] Alerts: set `CLIENT_<SLUG>_SLACK_WEBHOOK_URL` and/or `CLIENT_<SLUG>_DISCORD_WEBHOOK_URL`;
      a hot or warm test lead should post a message.
- [ ] Create an API key (Automations → API keys) for n8n/Make/Zapier; store it in the tool's
      credential store, not in a workflow body.
- [ ] Import the n8n workflows / Make blueprint / Zapier recipes from
      [`integrations/`](../integrations/README.md); add the outbound webhook (Automations →
      Webhooks) and save the signing secret it shows once.
- [ ] Set `OUTBOUND_WEBHOOKS_LIVE=true` on the server once receivers are ready.
- [ ] Optional GHL → OFM stage sync: set `CLIENT_<SLUG>_INBOUND_HMAC_SECRET` and wire the GHL
      workflow webhook through n8n/Make/Zapier ([ghl-snapshot.md §6](ghl-snapshot.md#6-syncing-stage-changes-back-to-ofm-engine)).
- [ ] Optional AI copy: paste an Anthropic or OpenAI key (Settings → Integrations → LLM); draft
      recruitment ads and follow-ups, then have a human review them.

## Week 1 — run it, then tune

- [ ] **Daily**: glance at the dashboard and the 09:00 KPI digest (n8n `02_daily_kpi_digest`);
      check Automations → Jobs for `dead` jobs; respond to hot leads within 15 minutes.
- [ ] **Mid-week**: compare funnel variants A vs B. Every lead stores its `variant` (also sent
      in the `lead.created` webhook and to Meta as custom data), so a sheet fed by the Make/Zapier
      template shows lead volume and lead → signed per variant; retire the loser or write a new
      challenger in the YAML.
- [ ] **Scoring review** with the recruitment lead: are "hot" leads actually the ones being
      signed? Adjust `scoring.tiers` / `scoring.weights` in the YAML and rescore if needed
      (`POST /leads/{id}/rescore` or the n8n workflow).
- [ ] **Revenue cadence**: agree who exports and imports revenue, and how often (weekly is a good
      default), so creator and channel ROI stays current.
- [ ] **Access review**: remove unused accounts and API keys; confirm every admin has 2FA.
- [ ] **Data retention**: agree how long unsuccessful applicants are kept and how erasure
      requests are handled ([compliance.md](compliance.md#5-privacy-gdpr--uk-gdpr--ccpa-style-obligations)).
- [ ] **Backups**: confirm the Postgres backup/export schedule (mandatory on free tiers —
      [deploy.md](deploy.md)).

## Go-live sign-off

| Check | Done |
|---|---|
| `ofm doctor` clean, `ENV=production`, Postgres, real secrets | ☐ |
| All admins on 2FA | ☐ |
| GHL: contact + tags + note + opportunity verified; jobs succeeding | ☐ |
| Meta: browser + server `Lead` deduplicated; `test_event_code` removed | ☐ |
| Funnel copy, consent text/version and privacy link approved by the agency | ☐ |
| Ad creatives reviewed against Meta policy; ads target 18+ | ☐ |
| SMS opt-out verified end to end | ☐ |
| Webhooks live and signature-verified by receivers | ☐ |
| Backups scheduled; restore tested once | ☐ |
