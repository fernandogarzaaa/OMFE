import json
import time
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from conftest import apply_body

CSV = b"""Date,Model,Campaign,Earnings,Net,Subs
2026-10-01,miarose,mia_ig,"$1,200.50",960.40,12
2026-10-02,Mia Rose,mia_ig,300,,3
bad-date,miarose,mia_ig,10,8,1
"""


def _creator(admin):
    return admin.post(
        "/api/v1/creators", json={"name": "Mia Rose", "handle": "miarose", "channels": ["instagram"]}
    ).json()


def test_revenue_import_mapping_and_dedupe(admin_a):
    cr = _creator(admin_a)
    mapping = json.dumps(
        {
            "date": "Date",
            "creator": "Model",
            "campaign": "Campaign",
            "gross": "Earnings",
            "net": "Net",
            "new_subs": "Subs",
        }
    )
    r = admin_a.post(
        "/api/v1/revenue/import", files={"file": ("of.csv", CSV, "text/csv")}, data={"mapping": mapping}
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["imported"] == 2 and out["error_count"] == 1 and out["errors"][0]["row"] == 4
    assert out["revenue_cents"] == 150050 and out["subs"] == 15
    rows = admin_a.get(f"/api/v1/revenue?creator_id={cr['id']}&from=2026-10-01&to=2026-10-31").json()["items"]
    assert len(rows) == 2
    second = next(x for x in rows if x["date"] == "2026-10-02")
    assert second["net_cents"] == 24000  # net_ratio_if_missing 0.8
    again = admin_a.post(
        "/api/v1/revenue/import", files={"file": ("of.csv", CSV, "text/csv")}, data={"mapping": mapping}
    ).json()
    assert again["imported"] == 0 and again["skipped_duplicates"] == 2
    assert admin_a.get("/api/v1/revenue?from=2026-10-02").json()["items"][0]["date"] == "2026-10-02"


def test_revenue_import_errors(admin_a):
    r = admin_a.post("/api/v1/revenue/import", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_csv"
    r = admin_a.post(
        "/api/v1/revenue/import", files={"file": ("x.csv", b"x", "text/csv")}, data={"mapping": "[1]"}
    )
    assert r.json()["error"]["code"] == "invalid_mapping"
    r = admin_a.post("/api/v1/revenue/import", files={"file": ("x.csv", b"\xff\xfe\x00", "text/csv")})
    assert r.status_code == 422
    big = b"date,gross\n" + b"2026-01-01,1\n" * 500_000
    assert (
        admin_a.post("/api/v1/revenue/import", files={"file": ("b.csv", big, "text/csv")}).status_code == 422
    )


def test_kpis_shape_and_values(admin_a, client, db):
    from ofm_engine.services import kpis

    cr = _creator(admin_a)
    admin_a.post(
        "/api/v1/links",
        json={
            "channel": "instagram",
            "destination_url": "https://e.com",
            "campaign": "mia_ig",
            "slug": "mia-ig",
            "creator_id": cr["id"],
        },
    )
    nc = TestClient(client.app, follow_redirects=False)
    for _ in range(4):
        nc.get("/l/mia-ig", headers={"user-agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/120"})
    lead = client.post("/api/v1/public/alpha/apply", json=apply_body()).json()
    admin_a.patch(f"/api/v1/leads/{lead['id']}", json={"stage": "signed"})
    today = datetime.now(UTC).date().isoformat()
    csv = f"date,creator,campaign,gross,new_subs\n{today},miarose,mia_ig,250.00,5\n".encode()
    admin_a.post("/api/v1/revenue/import", files={"file": ("r.csv", csv, "text/csv")})
    k = admin_a.get("/api/v1/kpis?range=7d").json()
    assert set(k) >= {
        "clicks",
        "leads",
        "signed",
        "subs",
        "revenue_cents",
        "conversion",
        "by_channel",
        "by_creator",
        "timeseries",
    }
    assert k["clicks"] == 4 and k["leads"] == 1 and k["signed"] == 1 and k["subs"] == 5
    assert k["revenue_cents"] == 25000
    assert k["conversion"] == {"click_to_lead": 0.25, "lead_to_signed": 1.0}
    ig = next(c for c in k["by_channel"] if c["channel"] == "instagram")
    assert ig["clicks"] == 4 and ig["revenue_cents"] == 25000 and ig["leads"] == 1
    assert k["by_creator"][0] == {
        "creator_id": cr["id"],
        "name": "Mia Rose",
        "revenue_cents": 25000,
        "subs": 5,
    }
    assert len(k["timeseries"]) == 7 and k["timeseries"][-1]["date"] == today
    # cache hit returns the same object until invalidated
    assert kpis.get_kpis(
        db, kpis.uuid.UUID(admin_a.get("/api/v1/auth/me").json()["tenant"]["id"]), "7d"
    ) is kpis.get_kpis(db, kpis.uuid.UUID(admin_a.get("/api/v1/auth/me").json()["tenant"]["id"]), "7d")
    assert admin_a.get("/api/v1/kpis?range=1y").status_code == 422


def _read_sse(c, path, headers=None):
    with c.stream("GET", path, headers=headers or {}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        return "".join(r.iter_text())


def test_sse_stream_replay_and_kpi(admin_a, client):
    client.post("/api/v1/public/alpha/apply", json=apply_body())
    body = _read_sse(admin_a, "/api/v1/live/stream?max_seconds=0.6", {"Last-Event-ID": "0"})
    assert "event: event" in body and '"type":"lead"' in body and "event: kpi" in body
    assert '"leads":1' in body and "id: 1" in body
    # resuming after the last id yields nothing new
    body2 = _read_sse(admin_a, "/api/v1/live/stream?max_seconds=0.6", {"Last-Event-ID": "1"})
    assert '"type":"lead"' not in body2
    # fresh connection without Last-Event-ID starts at the tail
    body3 = _read_sse(admin_a, "/api/v1/live/stream?max_seconds=0.5")
    assert "event: event" not in body3 and ": connected" in body3


def test_sse_live_wakeup(admin_a, client, app):
    import threading

    def later():
        time.sleep(0.4)
        client.post("/api/v1/public/alpha/apply", json=apply_body(email="late@example.net"))

    threading.Thread(target=later).start()
    body = _read_sse(admin_a, "/api/v1/live/stream?max_seconds=2.5")
    assert '"type":"lead"' in body


def test_sse_requires_auth_and_isolated(app, admin_b, client):
    assert TestClient(app).get("/api/v1/live/stream").status_code == 401
    client.post("/api/v1/public/alpha/apply", json=apply_body())
    body = _read_sse(admin_b, "/api/v1/live/stream?max_seconds=0.5", {"Last-Event-ID": "0"})
    assert '"type":"lead"' not in body


def test_ai_copy_mock_deterministic(admin_a):
    body = {"kind": "recruitment_ad", "brief": "We grow fitness creators on TikTok", "variants": 3}
    r1 = admin_a.post("/api/v1/ai/copy", json=body).json()
    r2 = admin_a.post("/api/v1/ai/copy", json=body).json()
    assert r1 == r2 and r1["mock"] is True and r1["provider"] == "mock" and len(r1["variants"]) == 3
    assert admin_a.post("/api/v1/ai/copy", json={**body, "variants": 6}).status_code == 422
    for kind in ("headline", "followup_sms", "followup_email", "social_caption"):
        assert (
            len(
                admin_a.post("/api/v1/ai/copy", json={**body, "kind": kind, "variants": 5}).json()["variants"]
            )
            == 5
        )


def test_ai_copy_guardrail(admin_a):
    for brief in (
        "write explicit nsfw content for her fans",
        "pretend to be the creator and chat with fans",
        "reply as the model to her subscribers",
        "ad targeting teen girls",
        "impersonate Mia in DMs",
    ):
        r = admin_a.post("/api/v1/ai/copy", json={"kind": "social_caption", "brief": brief})
        assert r.status_code == 422, brief
        assert r.json()["error"]["code"] == "content_refused"
    assert any(a["action"] == "ai.copy_refused" for a in admin_a.get("/api/v1/audit").json()["items"])


def test_ai_copy_live_llm(admin_a, monkeypatch):
    import httpx
    import respx

    admin_a.put(
        "/api/v1/integrations/llm", json={"fields": {"anthropic_api_key": "sk-ant-test-key-1234567890"}}
    )
    with respx.mock() as rx:
        rx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200, json={"content": [{"type": "text", "text": "Grow with us\nYour brand, our team"}]}
            )
        )
        r = admin_a.post(
            "/api/v1/ai/copy", json={"kind": "headline", "brief": "agency growth", "variants": 2}
        )
    assert r.json() == {
        "variants": ["Grow with us", "Your brand, our team"],
        "provider": "anthropic",
        "mock": False,
    }
    with respx.mock() as rx:
        rx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200, json={"content": [{"type": "text", "text": "something explicit"}]}
            )
        )
        r = admin_a.post(
            "/api/v1/ai/copy", json={"kind": "headline", "brief": "agency growth", "variants": 1}
        )
    assert r.status_code == 422


def test_integrations_write_only(admin_a, db):
    items = admin_a.get("/api/v1/integrations").json()
    assert {i["provider"] for i in items} == {"ghl", "meta_capi", "tiktok", "llm"}
    assert all(i["mode"] == "mock" and not i["configured"] for i in items)
    r = admin_a.put(
        "/api/v1/integrations/meta_capi",
        json={"fields": {"access_token": "EAAsecretvalue1234567890abc", "pixel_id": "123456"}},
    )
    assert r.status_code == 200
    out = r.json()
    assert out["configured"] and out["mode"] == "live"
    assert "EAAsecret" not in json.dumps(admin_a.get("/api/v1/integrations").json())
    from ofm_engine.models import TenantSecret

    row = db.query(TenantSecret).filter_by(name="META_CAPI_TOKEN").one()
    assert b"EAAsecret" not in row.ciphertext
    assert admin_a.put("/api/v1/integrations/meta_capi", json={"fields": {"bogus": "x"}}).status_code == 422
    assert admin_a.put("/api/v1/integrations/nope", json={"fields": {}}).status_code == 422
    r = admin_a.put("/api/v1/integrations/meta_capi", json={"fields": {"access_token": ""}})
    assert r.json()["mode"] == "mock"
    audit = admin_a.get("/api/v1/audit?action=integration.update").json()["items"]
    assert audit and "EAAsecret" not in json.dumps(audit)


def test_integration_tests_mock(admin_a):
    for p in ("ghl", "meta_capi", "tiktok", "llm"):
        r = admin_a.post(f"/api/v1/integrations/{p}/test").json()
        assert r["ok"] is True and r["mode"] == "mock", (p, r)


def test_integration_test_live_failure(admin_a):
    import httpx
    import respx

    admin_a.put(
        "/api/v1/integrations/tiktok",
        json={"fields": {"access_token": "tt-token-123456789", "pixel_code": "PX"}},
    )
    with respx.mock() as rx:
        rx.post("https://business-api.tiktok.com/open_api/v1.3/event/track/").mock(
            return_value=httpx.Response(200, json={"code": 40001, "message": "bad token"})
        )
        r = admin_a.post("/api/v1/integrations/tiktok/test").json()
    assert r["ok"] is False and r["mode"] == "live" and "40001" in r["detail"]


def test_inbound_hook_signature_and_replay(admin_a, client):
    from ofm_engine.security import signing

    lead = client.post("/api/v1/public/alpha/apply", json=apply_body()).json()
    body = json.dumps({"type": "lead.stage", "lead_id": lead["id"], "stage": "call_booked"}).encode()
    assert client.post("/api/v1/hooks/inbound/alpha", content=body).status_code == 403  # not configured
    from ofm_engine.db import session_scope
    from ofm_engine.models import Tenant
    from ofm_engine.services import secrets as ss

    with session_scope() as s:
        ss.set_secret(
            s, s.query(Tenant).filter_by(slug="alpha").one(), "INBOUND_HMAC_SECRET", "inbound-secret-xyz"
        )
    assert client.post("/api/v1/hooks/inbound/alpha", content=body).status_code == 401
    bad = signing.sign("wrong", body)
    assert (
        client.post(
            "/api/v1/hooks/inbound/alpha", content=body, headers={signing.SIGNATURE_HEADER: bad}
        ).status_code
        == 401
    )
    old = signing.sign("inbound-secret-xyz", body, timestamp=int(time.time()) - 3600)
    r = client.post("/api/v1/hooks/inbound/alpha", content=body, headers={signing.SIGNATURE_HEADER: old})
    assert r.status_code == 401 and "tolerance" in r.json()["error"]["message"]
    good = signing.sign("inbound-secret-xyz", body)
    r = client.post("/api/v1/hooks/inbound/alpha", content=body, headers={signing.SIGNATURE_HEADER: good})
    assert r.status_code == 200
    assert admin_a.get(f"/api/v1/leads/{lead['id']}").json()["stage"] == "call_booked"
    r = client.post("/api/v1/hooks/inbound/alpha", content=body, headers={signing.SIGNATURE_HEADER: good})
    assert r.status_code == 409 and r.json()["error"]["code"] == "replayed"


def test_lead_patch_and_rescore(admin_a, client):
    lead = client.post("/api/v1/public/alpha/apply", json=apply_body(phone=None)).json()
    before = admin_a.get(f"/api/v1/leads/{lead['id']}").json()["score"]
    r = admin_a.patch(f"/api/v1/leads/{lead['id']}", json={"phone": "+15555550199", "name": "Ava T"})
    assert r.json()["name"] == "Ava T"
    after = admin_a.post(f"/api/v1/leads/{lead['id']}/rescore").json()["score"]
    assert after == before + 5
    assert admin_a.get("/api/v1/leads?stage=new").json()["items"][0]["id"] == lead["id"]
    assert admin_a.get("/api/v1/leads?stage=signed").json()["items"] == []
    assert admin_a.patch(f"/api/v1/leads/{lead['id']}", json={"stage": "bogus"}).status_code == 422


def test_creators_crud(admin_a):
    cr = _creator(admin_a)
    assert cr["handle"] == "miarose" and cr["status"] == "onboarding" and cr["channels"] == ["instagram"]
    assert admin_a.post("/api/v1/creators", json={"name": "X", "handle": "miarose"}).status_code == 409
    assert admin_a.post("/api/v1/creators", json={"name": "X", "handle": "Bad Handle!"}).status_code == 422
    r = admin_a.patch(
        f"/api/v1/creators/{cr['id']}", json={"status": "active", "name": "Mia R", "channels": ["X", " "]}
    ).json()
    assert r["status"] == "active" and r["name"] == "Mia R" and r["channels"] == ["x"]
    assert admin_a.get("/api/v1/creators?status=active").json()["items"][0]["id"] == cr["id"]


def test_health_metrics_headers(client, monkeypatch):
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/readyz").json()["db"] == "ok"
    assert client.get("/metrics").status_code == 404
    monkeypatch.setenv("METRICS_TOKEN", "metrics-secret")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = client.get("/metrics", headers={"Authorization": "Bearer metrics-secret"})
    assert r.status_code == 200 and "ofm_http_requests_total" in r.text
    monkeypatch.setenv("METRICS_TOKEN", "rotated")  # read per request
    assert client.get("/metrics", headers={"Authorization": "Bearer metrics-secret"}).status_code == 401
    h = client.get("/api/v1/healthz").headers
    assert (
        "default-src 'self'" in h["content-security-policy"]
        and "frame-ancestors 'none'" in h["content-security-policy"]
    )
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert h["cache-control"] == "no-store" and "csrf_token=" in h.get("set-cookie", "")


def test_hsts_and_cors(monkeypatch, tenants):
    from ofm_engine.app import create_app
    from ofm_engine.settings import reset_settings_cache

    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    reset_settings_cache()
    c = TestClient(create_app())
    assert "max-age" in c.get("/healthz").headers["strict-transport-security"]
    pre = c.options(
        "/api/v1/creators",
        headers={"Origin": "https://app.example.com", "Access-Control-Request-Method": "POST"},
    )
    assert pre.headers["access-control-allow-origin"] == "https://app.example.com"
    assert pre.headers["access-control-allow-credentials"] == "true"
    evil = c.options(
        "/api/v1/creators",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in evil.headers


def test_production_refuses_insecure_defaults(monkeypatch):
    import pytest

    from ofm_engine.app import create_app
    from ofm_engine.settings import reset_settings_cache

    monkeypatch.setenv("ENV", "production")
    reset_settings_cache()
    with pytest.raises(RuntimeError, match="refusing to start"):
        create_app()


def test_spa_fallback_and_api_404(client):
    assert client.get("/api/v1/does-not-exist").json()["error"]["code"] == "not_found"
    r = client.get("/some/client/route")
    assert r.status_code == 200 and "<html" in r.text.lower()
    assert client.get("/").status_code == 200


def test_validation_error_envelope(admin_a):
    r = admin_a.post("/api/v1/creators", json={"handle": "x"})
    err = r.json()["error"]
    assert r.status_code == 422 and err["code"] == "validation_error" and "name" in err["message"]


def test_audit_log_admin(admin_a):
    _creator(admin_a)
    items = admin_a.get("/api/v1/audit").json()["items"]
    actions = {i["action"] for i in items}
    assert {"auth.login", "creator.create"} <= actions
