"""Frontend <-> backend integration contract (see docs/api-contract.md, frontend/src/lib/types.ts)."""

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from conftest import apply_body
from ofm_engine.cli import app as cli_app


def _sse_events(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.split("\n\n"):
        name = re.search(r"^event: (\w+)$", block, re.M)
        data = re.search(r"^data: (.*)$", block, re.M)
        if name and data:
            out.append((name.group(1), json.loads(data.group(1))))
    return out


def _read(c, path, headers=None):
    with c.stream("GET", path, headers=headers or {}) as r:
        assert r.status_code == 200
        return "".join(r.iter_text())


def test_sse_kpi_events_are_increments_covering_only_the_batch(admin_a, client):
    """`kpi` = additive delta for exactly the `event`s sent just before it (frontend adds it)."""
    client.post("/api/v1/public/alpha/apply", json=apply_body())
    client.post("/api/v1/public/alpha/apply", json=apply_body(email="b@example.net"))
    evs = _sse_events(_read(admin_a, "/api/v1/live/stream?max_seconds=0.6", {"Last-Event-ID": "0"}))
    kpis = [d for n, d in evs if n == "kpi"]
    assert kpis and set(kpis[0]) == {"clicks", "leads", "signed", "subs", "revenue_cents"}
    assert sum(k["leads"] for k in kpis) == 2  # summing all deltas == number of lead events
    assert sum(1 for n, d in evs if n == "event" and d["type"] == "lead") == 2
    # resume after the first event: only the second lead is (re)counted, never the first
    evs2 = _sse_events(_read(admin_a, "/api/v1/live/stream?max_seconds=0.6", {"Last-Event-ID": "1"}))
    assert sum(d["leads"] for n, d in evs2 if n == "kpi") == 1


def test_sse_kpi_delta_signed_and_revenue(admin_a, client):
    from ofm_engine.api.routes.data import _kpi_delta

    class R:
        def __init__(self, kind, data):
            self.kind, self.data = kind, data

    d = _kpi_delta(
        [
            R("click", {}),
            R("lead_stage", {"stage": "signed"}),
            R("lead_stage", {"stage": "contacted"}),
            R("revenue_import", {"revenue_cents": 1234, "subs": 3}),
            R("capi_sent", {}),
        ]
    )
    assert d == {"clicks": 1, "leads": 0, "signed": 1, "subs": 3, "revenue_cents": 1234}


def test_totp_required_error_code(admin_a, app):
    import pyotp
    from fastapi.testclient import TestClient

    uri = admin_a.post("/api/v1/auth/totp/setup").json()["otpauth_uri"]
    secret = re.search(r"secret=([A-Z2-7]+)", uri).group(1)
    assert (
        admin_a.post("/api/v1/auth/totp/verify", json={"code": pyotp.TOTP(secret).now()}).status_code == 200
    )
    c = TestClient(app)
    r = c.post("/api/v1/auth/login", json={"email": "admin@alpha.test", "password": "Sup3r-Secret-Passw0rd!"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "totp_required"


def test_refresh_returns_user(admin_a):
    r = admin_a.post("/api/v1/auth/refresh")
    assert r.status_code == 200
    u = r.json()["user"]
    assert u["email"] == "admin@alpha.test" and u["tenant"]["slug"] == "alpha"


def test_revenue_mapping_aliases_and_unknown_keys(admin_a):
    admin_a.post("/api/v1/creators", json={"name": "Mia Rose", "handle": "miarose"})
    csv = b"Day,Model,Earnings,Subs\n2026-10-01,miarose,100.00,4\n"
    m = {"date": "Day", "creator": "Model", "amount": "Earnings", "subs": "Subs"}
    r = admin_a.post(
        "/api/v1/revenue/import",
        files={"file": ("x.csv", csv, "text/csv")},
        data={"mapping": json.dumps(m)},
    )
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1 and r.json()["revenue_cents"] == 10000 and r.json()["subs"] == 4
    bad = admin_a.post(
        "/api/v1/revenue/import",
        files={"file": ("x.csv", csv, "text/csv")},
        data={"mapping": json.dumps({"bogus": "x"})},
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_mapping"


def test_frontend_webhook_events_accepted_and_emitted(admin_a, client, db):
    from sqlalchemy import select

    from ofm_engine.models import OutboxJob

    events = ["lead.created", "lead.stage_changed", "link.clicked", "revenue.imported", "creator.updated"]
    r = admin_a.post(
        "/api/v1/automations/webhooks", json={"url": "https://hooks.slack.com/x", "events": events}
    )
    assert r.status_code == 201, r.text
    assert r.json()["secret"].startswith("whsec_")
    listed = admin_a.get("/api/v1/automations/webhooks").json()
    assert set(listed) == {"items", "next_cursor"} and listed["items"][0]["events"] == sorted(events)
    cr = admin_a.post("/api/v1/creators", json={"name": "Mia", "handle": "mia"}).json()
    admin_a.patch(f"/api/v1/creators/{cr['id']}", json={"status": "paused"})
    link = admin_a.post(
        "/api/v1/links",
        json={"channel": "ig", "destination_url": "https://example.com", "campaign": "c", "slug": "mia-ig"},
    ).json()
    assert client.get(f"/l/{link['slug']}", follow_redirects=False).status_code in (200, 302)
    kinds = [
        j.payload.get("event")
        for j in db.scalars(select(OutboxJob).where(OutboxJob.kind == "webhook.deliver")).all()
    ]
    assert "creator.updated" in kinds and "link.clicked" in kinds


def test_api_key_lifecycle_shapes(admin_a):
    created = admin_a.post("/api/v1/api-keys", json={"name": "n8n"}).json()
    assert {"id", "name", "prefix", "key", "revoked"} <= set(created)
    page = admin_a.get("/api/v1/api-keys").json()
    assert page["items"][0]["id"] == created["id"] and "key" not in page["items"][0]
    assert admin_a.delete(f"/api/v1/api-keys/{created['id']}").status_code == 200
    assert admin_a.get("/api/v1/api-keys").json()["items"] == []  # revoked keys drop off the list


def test_jobs_audit_users_shapes(admin_a):
    jobs = admin_a.get("/api/v1/automations/jobs").json()
    assert set(jobs) == {"items", "next_cursor"}
    u = admin_a.post("/api/v1/users", json={"email": "new.hire@example.org", "name": "New", "role": "member"})
    assert u.status_code == 201 and u.json()["temporary_password"]
    audit = admin_a.get("/api/v1/audit").json()["items"]
    assert {"id", "actor_id", "actor_type", "action", "target", "meta", "created_at"} <= set(audit[0])
    assert any(a["action"] == "user.create" and a["actor"] == "admin@alpha.test" for a in audit)


def test_seed_demo_known_passwords_match_frontend_mock(monkeypatch):
    from fastapi.testclient import TestClient

    from ofm_engine.app import create_app
    from ofm_engine.services.seed import DEMO_ADMIN_EMAIL, DEMO_KNOWN_PASSWORD, DEMO_MEMBER_EMAIL

    r = CliRunner().invoke(cli_app, ["seed-demo", "--known-passwords"])
    assert r.exit_code == 0, r.output
    c = TestClient(create_app())
    for email in (DEMO_ADMIN_EMAIL, DEMO_MEMBER_EMAIL):
        assert (
            c.post("/api/v1/auth/login", json={"email": email, "password": DEMO_KNOWN_PASSWORD}).status_code
            == 200
        )
    mock = (Path(__file__).resolve().parents[1] / "frontend/src/mock/db.ts").read_text(encoding="utf-8")
    assert DEMO_ADMIN_EMAIL in mock and DEMO_MEMBER_EMAIL in mock and DEMO_KNOWN_PASSWORD in mock
    monkeypatch.setenv("ENV", "production")
    from ofm_engine.settings import reset_settings_cache

    reset_settings_cache()
    assert CliRunner().invoke(cli_app, ["seed-demo", "--known-passwords"]).exit_code == 2
