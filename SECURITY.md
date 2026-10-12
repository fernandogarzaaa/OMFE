# Security Policy

OFM Engine handles applicant contact details, integration tokens and revenue data for
management agencies, so security reports are taken seriously and handled privately.

## Supported versions

| Version | Supported |
|---|---|
| 1.0.x | ✅ security fixes |
| < 1.0 | ❌ |

## Reporting a vulnerability

**Please do not open a public issue, pull request or discussion for a vulnerability.**

Report it privately through GitHub's private vulnerability reporting:

1. Go to <https://github.com/fernandogarzaaa/OMFE/security/advisories/new>
   (repository **Security** tab → **Report a vulnerability**).
2. Include the affected version or commit, a description of the issue and its impact, and
   step-by-step reproduction (a proof of concept against a local mock-mode install is ideal).
3. If the issue involves a third-party service (GoHighLevel, Meta, TikTok, an LLM provider),
   say so; do not test against accounts you do not own.

What to expect:

- Acknowledgement within **3 business days**.
- An initial assessment (severity, affected versions) within **7 days**.
- A fix or mitigation for confirmed high/critical issues as quickly as practical, coordinated
  with you before public disclosure. Credit is given in the advisory unless you prefer otherwise.

## Scope

In scope: the `ofm_engine` Python package, the React frontend in `frontend/`, the Docker image
`ghcr.io/fernandogarzaaa/omfe`, the bundled automation templates in `integrations/`, and the
deployment manifests (`Dockerfile`, `docker-compose.yml`, `render.yaml`).

Out of scope: vulnerabilities in GoHighLevel, Meta, TikTok, Anthropic, OpenAI, n8n, Make or
Zapier themselves (report those to the vendor); findings that require a malicious tenant admin
with access to the server environment; denial of service by volumetric traffic; reports from
automated scanners without a demonstrated impact; the intentionally weak throwaway secrets in
`docker-compose.yml` and the documented demo password, which production mode refuses.

## Safe harbour

Good-faith research that respects this policy, avoids privacy violations and service
disruption, and uses only your own data and accounts will not be pursued.

## Hardening references

See [docs/security.md](docs/security.md) for the threat model and the OWASP ASVS L2 mapping,
and the **Go-live checklist** in the [README](README.md#go-live-checklist) for required
production secrets.
