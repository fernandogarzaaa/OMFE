# Compliance

OFM Engine sits next to an adult platform, so the product is designed to keep an agency on the
right side of platform rules and privacy law by default. This page explains what the software
enforces, what the agency still has to do, and why some features are deliberately absent.

> This is engineering documentation, not legal advice. Policies change; the sources below were
> checked on 2026-10-13. Have counsel review your funnel copy, consent wording and privacy notice
> for the jurisdictions you recruit in.

## 1. 18+ verification and consent in the funnel

What the code enforces:

- The funnel (`templates/apply.html`) has two **required, unticked** checkboxes: *"I confirm I am
  18 years of age or older"* and the tenant's consent text (default: *"I confirm I am 18 or older
  and agree to be contacted by SMS/email about management services. Msg & data rates may apply.
  Reply STOP to opt out."*), with a link to the agency's privacy policy when `funnel.privacy_url`
  is set.
- The server rejects any application without both flags — `422 age_and_consent_required` from
  `POST /api/v1/public/{slug}/apply`, and the same validation on the no-JS form post — and the
  service layer re-checks them (`services/leads.submit_application`).
- Every lead stores `age_confirmed`, `consent`, the **consent text version**
  (`funnel.consent_version`) and the **consent timestamp**, so you can prove what the applicant
  agreed to and when. Change `consent_version` whenever you change the wording.
- Pages say "18+ creators only"; the AI copy assistant refuses any brief or output that
  references minors, teens or school settings.

What the agency must still do:

- **Self-attestation is not identity verification.** Before signing a creator, verify age and
  identity with government ID (and the platform's own creator onboarding/KYC). Never collect ID
  documents through this funnel.
- Do not target recruitment ads at under-18 audiences; set the ad set minimum age to 18.

## 2. Meta advertising rules for adult-adjacent agencies

Checked against Meta's Transparency Center on 2026-10-13 (both policies show a change-log entry
dated **May 14, 2025**):

- **Adult Sexual Solicitation and Sexually Explicit Language** —
  <https://transparency.meta.com/policies/ad-standards/objectionable-content/adult-sexual-solicitation-and-sexually-explicit-language/>.
  *When targeting people aged 18 or older*, ads **may** contain usernames, links to or logos of
  Adult Subscription Websites (and sexually suggestive language referring to sexual encounters, or
  explicit language in satirical/humorous contexts). Ads still **must not** facilitate sexual
  encounters or commercial sexual services, ask for or offer pornographic content, or discuss
  sexual practices or experiences.
- **Adult Nudity and Sexual Activity** —
  <https://transparency.meta.com/policies/ad-standards/objectionable-content/adult-nudity-and-sexual-activity>.
  Ads **must not** depict nudity (including near-nudity behind overlays), sexual activity,
  sexually suggestive gestures or activities (simulated sex, sexual dancing), logos or clips of
  known pornographic websites, or audio of sexual activity. Imagery focused on sexualised body
  parts, sexually suggestive poses or revealing clothing must be targeted 18+.
- **Relevance** — <https://transparency.meta.com/policies/ad-standards/>: *"The products and
  services promoted in an ad must match those promoted on the landing page."*

How OFM Engine applies this:

- The Pixel and CAPI are used on the **SFW recruitment funnel** (an agency hiring creators) and
  SFW link-in-bio pages. A recruitment ad therefore matches its landing page: management
  services, not adult content.
- Tracking links that send fan traffic to an adult destination **do not** fire the Pixel or CAPI
  unless an admin explicitly enables `capi_on_click` on that link. Leave it off for adult
  destinations: Meta reviews landing pages, and an ad that lands on adult content will be
  rejected and can get the ad account restricted.
- The AI copy assistant refuses explicit terms, minors and impersonation (checked on the brief
  and on every generated line). It is a drafting aid; a human must still review every ad against
  the policies above.
- Applicant email, phone and name are normalised and SHA-256 hashed before they are sent to Meta
  or TikTok (`hashing.py`); IP and user agent are sent unhashed as Meta requires.
- Keep `test_event_code` only while validating; remove it before go-live.

## 3. No OnlyFans scraping or account automation — by design

OFM Engine never logs into, scrapes, or automates OnlyFans accounts, and never sends messages
to fans. Revenue comes in through **CSV exports** that the agency downloads itself.

Why:

- **Platform rules.** OnlyFans' Acceptable Use Policy (last updated September 2025,
  <https://onlyfans.com/acceptable-use-policy>) prohibits "*Copying content including scraping,
  downloading, sharing, or gathering information from OnlyFans or any Creator for any reason*",
  activity that interferes with OnlyFans' software or network, and "*Inaccurate information
  including misleading descriptions of media or account information*". Breaches can lead to
  deactivation of content or the account.
- **Creator risk.** A banned or restricted account costs the creator their income; an agency
  tool must never be the reason.
- **Security.** Storing creators' platform passwords or session cookies would make this system
  a high-value target. It holds none.
- **Honesty to fans.** The AI assistant refuses to write *as* a creator, chat with fans as a
  creator, or create fake personas; it only drafts the agency's own marketing and recruitment
  copy.

## 4. SMS and email follow-up (TCPA, CAN-SPAM and similar)

- The funnel collects **express written consent** to be contacted by SMS/email, with the
  disclosure text, a version and a timestamp stored per lead (see §1).
- Every SMS template in [`sequences/`](../sequences/) and the built-in (mock) `followup_sms`
  templates of the AI assistant end with **"Reply STOP to opt out."** Live LLM output is not
  post-processed, so check that each SMS you send keeps the opt-out line.
- Sending happens in GoHighLevel (or your own tools), not in OFM Engine. Configure GHL so STOP
  replies mark the contact as do-not-disturb for SMS, respect quiet hours in the recipient's
  timezone (the funnel collects `timezone` for this), and complete any carrier registration
  your sending numbers require (for US long codes, A2P 10DLC).
- Emails should identify the agency, include a physical address and an unsubscribe link where
  the law requires it.
- Never buy lists or message people who did not apply. A lead imported from elsewhere has no
  consent record in OFM Engine.

## 5. Privacy (GDPR / UK GDPR / CCPA-style obligations)

Data minimisation built in:

| Data | Stored as | Notes |
|---|---|---|
| Applicant name, email, phone, socials, follower counts, timezone, stated earnings range | plaintext columns in `leads` | needed to contact and qualify the applicant |
| Email lookup key | keyed hash (`email_hash`, HMAC with `PII_HASH_SALT`) | de-duplication without exposing the email |
| Visitor on tracking links | keyed hash of IP + user agent (`visitor_hash`) | raw IP is not stored for clicks |
| Audit-log IP | keyed hash (`ip_hash`) | never returned by the API |
| Data sent to Meta/TikTok | SHA-256 of normalised email/phone/name | per the providers' matching rules |
| Integration secrets | AES-256-GCM envelope encryption | never returned by the API |

Logs redact emails, phone numbers and tokens. The live dashboard feed carries no PII.

What the agency (as controller) must do:

- Publish a privacy notice and set `funnel.privacy_url`; name GoHighLevel, Meta, TikTok and any
  LLM provider you enable as processors/recipients.
- Decide a lawful basis (consent is captured; legitimate interest may also apply to recruitment
  follow-up) and a **retention period** for unsuccessful applicants.
- Handle access and erasure requests. In v1.0 erasure is a manual operation: delete or anonymise
  the lead row (and its `events` rows), delete the contact in GoHighLevel, and remove it from
  any automation destination that received webhooks. A self-service erasure endpoint is on the
  roadmap.
- Sign data-processing agreements with your hosting and integration providers; prefer EU
  hosting regions for EU applicants where required.
- Never change `PII_HASH_SALT` after go-live (hashes would stop matching) and never share it
  between environments.

## 6. Content guardrails summary

| Rule | Enforced in |
|---|---|
| Applicants must confirm 18+ and consent | `api/routes/public.py`, `services/leads.py`, `templates/apply.html` |
| No explicit content in generated copy | `services/ai_copy.check_content` (brief and output) |
| Nothing referencing minors | same |
| No impersonating creators / fake personas / sexting | same |
| SMS templates carry opt-out text | `sequences/*.yaml`, mock templates in `services/ai_copy.py` |
| No Pixel/CAPI on fan redirects unless opted in | `services/tracking.py` (`capi_on_click`) |
| No OnlyFans credentials, scraping or messaging | absent by design |
