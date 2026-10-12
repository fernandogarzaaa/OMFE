from fastapi.testclient import TestClient

from conftest import apply_body


def test_apply_creates_lead_and_jobs(client, admin_a):
    r = client.post("/api/v1/public/alpha/apply", json=apply_body())
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["ok"] and out["event_id"] and out["duplicate"] is False
    lead = admin_a.get(f"/api/v1/leads/{out['id']}").json()
    assert lead["followers"] == 170000 and lead["score"] > 0 and lead["score_reasons"]
    assert lead["stage"] == "new"
    kinds = {j["kind"] for j in admin_a.get("/api/v1/automations/jobs").json()["items"]}
    assert {"ghl.upsert_lead", "meta_capi.send"} <= kinds
    # idempotent resubmission
    r2 = client.post("/api/v1/public/alpha/apply", json=apply_body(email="AVA@example.net "))
    assert r2.status_code == 200 and r2.json()["id"] == out["id"] and r2.json()["duplicate"] is True


def test_apply_requires_age_and_consent(client, tenants):
    for kw in ({"age_confirmed": False}, {"consent": False}, {"age_confirmed": False, "consent": False}):
        r = client.post("/api/v1/public/alpha/apply", json=apply_body(**kw))
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "age_and_consent_required"
    body = apply_body()
    del body["age_confirmed"]
    assert client.post("/api/v1/public/alpha/apply", json=body).status_code == 422


def test_apply_validation(client, tenants):
    assert client.post("/api/v1/public/alpha/apply", json=apply_body(email="nope")).status_code == 422
    assert client.post("/api/v1/public/alpha/apply", json=apply_body(followers=-5)).status_code == 422
    assert client.post("/api/v1/public/alpha/apply", json=apply_body(earnings_range="1m+")).status_code == 422
    assert client.post("/api/v1/public/nope/apply", json=apply_body()).status_code == 404
    assert client.post("/api/v1/public/BAD!/apply", json=apply_body()).status_code == 404
    r = client.post("/api/v1/public/alpha/apply", json=apply_body(followers=5000))
    assert r.status_code == 201


def test_apply_honeypot(client, admin_a):
    r = client.post("/api/v1/public/alpha/apply", json=apply_body(website="http://spam"))
    assert r.status_code == 201
    assert admin_a.get("/api/v1/leads").json()["items"] == []


def test_apply_rate_limited(app, tenants, monkeypatch):
    from ofm_engine.app import create_app
    from ofm_engine.settings import reset_settings_cache

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_PUBLIC_PER_MIN", "3")
    reset_settings_cache()
    c = TestClient(create_app())
    codes = [
        c.post("/api/v1/public/alpha/apply", json=apply_body(email=f"a{i}@example.net")).status_code
        for i in range(5)
    ]
    assert codes[:3] == [201, 201, 201] and codes[3] == 429
    assert c.post("/api/v1/public/alpha/apply", json=apply_body()).json()["error"]["code"] == "rate_limited"


def test_funnel_page_variants(client, tenants, tmp_path):
    (tmp_path / "clients").mkdir(exist_ok=True)
    (tmp_path / "clients" / "alpha.yaml").write_text(
        "slug: alpha\nname: Alpha Agency\nmeta:\n  pixel_id: '1234567890'\nfunnel:\n  variants:\n"
        "    - {key: A, headline: Headline Alpha}\n    - {key: B, headline: Headline Bravo}\n"
    )
    r = client.get("/apply/alpha?v=B&utm_source=ig")
    assert r.status_code == 200
    html = r.text
    assert "Headline Bravo" in html and 'data-variant="B"' in html
    assert "I confirm I am 18 years of age or older" in html
    assert 'value="ig"' in html and "fbq('init'" in html
    nonce = r.headers["content-security-policy"].split("'nonce-")[1].split("'")[0]
    assert f'nonce="{nonce}"' in html
    assert "ofm_v_alpha=B" in r.headers.get("set-cookie", "")
    # sticky variant via cookie
    r2 = client.get("/apply/alpha")
    assert 'data-variant="B"' in r2.text
    assert client.get("/apply/missing").status_code == 404


def test_funnel_form_post_fallback(client, admin_a):
    form = {
        "name": "Form Person",
        "email": "form@example.net",
        "age_confirmed": "true",
        "consent": "true",
        "earnings_range": "1k-5k",
        "instagram": "@f",
        "instagram_followers": "2500",
        "attr_utm_source": "x",
    }
    r = client.post("/apply/alpha", data=form)
    assert r.status_code == 200 and "Thank you" in r.text
    leads = admin_a.get("/api/v1/leads").json()["items"]
    assert leads[0]["followers"] == 2500 and leads[0]["attribution"]["utm_source"] == "x"
    r = client.post("/apply/alpha", data={**form, "age_confirmed": ""})
    assert r.status_code == 422 and "18+" in r.text
    r = client.post("/apply/alpha", data={**form, "email": "bad"})
    assert r.status_code == 422


def _link(admin, **kw):
    body = {
        "channel": "instagram",
        "destination_url": "https://example.com/landing?x=1",
        "campaign": "spring",
        "slug": "spring-ig",
    }
    body.update(kw)
    r = admin.post("/api/v1/links", json=body)
    assert r.status_code == 201, r.text
    return r.json()


UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Instagram 300"


def test_redirect_captures_attribution(app, admin_a, db):
    _link(admin_a)
    c = TestClient(app, follow_redirects=False)
    r = c.get(
        "/l/spring-ig?utm_medium=bio&fbclid=ABC123&ttclid=TT9",
        headers={"user-agent": UA, "referer": "https://instagram.com/x"},
    )
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("https://example.com/landing?") and "x=1" in loc and "utm_source=instagram" in loc
    assert "utm_campaign=spring" in loc
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "_fbc=fb.1." in r.headers.get("set-cookie", "") and "_fbp=fb.1." in r.headers.get("set-cookie", "")
    from ofm_engine.models import Click

    click = db.query(Click).one()
    assert click.fbclid == "ABC123" and click.fbc.endswith(".ABC123") and click.ttclid == "TT9"
    assert click.utm_medium == "bio" and click.device == "mobile" and click.browser == "instagram"
    assert click.referrer_host == "instagram.com"
    links = admin_a.get("/api/v1/links").json()["items"]
    assert links[0]["clicks"] == 1
    assert c.get("/l/nope").status_code == 404


def test_redirect_bot_not_counted(app, admin_a):
    _link(admin_a)
    c = TestClient(app, follow_redirects=False)
    c.get("/l/spring-ig", headers={"user-agent": "facebookexternalhit/1.1"})
    assert admin_a.get("/api/v1/links").json()["items"][0]["clicks"] == 0


def test_redirect_pixel_interstitial_shares_event_id(app, admin_a, db, tmp_path):
    (tmp_path / "clients").mkdir(exist_ok=True)
    (tmp_path / "clients" / "alpha.yaml").write_text(
        "slug: alpha\nname: Alpha\nmeta:\n  pixel_id: '99887766'\n"
    )
    _link(admin_a, slug="recruit", capi_on_click=True)
    c = TestClient(app, follow_redirects=False)
    r = c.get("/l/recruit", headers={"user-agent": UA})
    assert r.status_code == 200 and "fbq('init', \"99887766\")" in r.text
    eid = r.headers["x-ofm-event-id"]
    assert f'eventID: "{eid}"' in r.text and r.headers["referrer-policy"] == "no-referrer"
    from ofm_engine.models import OutboxJob

    job = db.query(OutboxJob).filter_by(kind="meta_capi.send").one()
    assert job.payload["event_id"] == eid and job.payload["event_name"] == "ViewContent"


def test_inactive_link_404(app, admin_a):
    lk = _link(admin_a)
    admin_a.patch(f"/api/v1/links/{lk['id']}", json={"active": False})
    assert TestClient(app, follow_redirects=False).get("/l/spring-ig").status_code == 404


def test_link_validation(admin_a):
    assert (
        admin_a.post(
            "/api/v1/links", json={"channel": "x", "destination_url": "javascript:alert(1)", "campaign": "c"}
        ).status_code
        == 422
    )
    _link(admin_a)
    assert (
        admin_a.post(
            "/api/v1/links",
            json={"channel": "x", "destination_url": "https://e.com", "campaign": "c", "slug": "spring-ig"},
        ).status_code
        == 409
    )
    auto = admin_a.post(
        "/api/v1/links", json={"channel": "x", "destination_url": "https://e.com", "campaign": "c"}
    ).json()
    assert len(auto["slug"]) >= 6
    r = admin_a.patch(
        f"/api/v1/links/{auto['id']}", json={"destination_url": "https://new.example", "channel": "TikTok"}
    )
    assert r.json()["channel"] == "tiktok"
    assert (
        admin_a.patch(f"/api/v1/links/{auto['id']}", json={"destination_url": "ftp://x"}).status_code == 422
    )


def test_link_in_bio(client, admin_a):
    admin_a.post(
        "/api/v1/creators",
        json={
            "name": "Mia Rose",
            "handle": "miarose",
            "status": "active",
            "bio": "Fitness & travel",
            "bio_links": [{"label": "My page", "url": "/l/spring-ig"}],
        },
    )
    r = client.get("/c/alpha/miarose")
    assert r.status_code == 200 and "Mia Rose" in r.text and 'href="/l/spring-ig"' in r.text
    assert client.get("/c/alpha/nobody").status_code == 404
    assert (
        admin_a.post(
            "/api/v1/creators",
            json={"name": "B", "handle": "bb", "bio_links": [{"label": "x", "url": "javascript:1"}]},
        ).status_code
        == 422
    )
