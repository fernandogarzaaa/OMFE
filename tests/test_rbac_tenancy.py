import pytest
from fastapi.testclient import TestClient

from conftest import apply_body


def _mk_creator(c, handle="mia"):
    r = c.post(
        "/api/v1/creators", json={"name": "Mia", "handle": handle, "channels": ["instagram", "TikTok"]}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _api_key(admin):
    r = admin.post("/api/v1/api-keys", json={"name": "n8n"})
    assert r.status_code == 201
    return r.json()


ADMIN_ONLY = [
    ("get", "/api/v1/users", None),
    ("post", "/api/v1/users", {"email": "x@y.test"}),
    ("get", "/api/v1/integrations", None),
    ("put", "/api/v1/integrations/ghl", {"fields": {}}),
    ("post", "/api/v1/integrations/ghl/test", None),
    ("get", "/api/v1/audit", None),
    ("get", "/api/v1/api-keys", None),
    ("post", "/api/v1/api-keys", {"name": "k"}),
    ("post", "/api/v1/automations/webhooks", {"url": "https://hooks.slack.com/x", "events": ["*"]}),
]
MEMBER_OK = [
    ("get", "/api/v1/creators"),
    ("get", "/api/v1/leads"),
    ("get", "/api/v1/pipeline"),
    ("get", "/api/v1/links"),
    ("get", "/api/v1/kpis"),
    ("get", "/api/v1/revenue"),
    ("get", "/api/v1/automations/jobs"),
    ("get", "/api/v1/automations/webhooks"),
]


@pytest.mark.parametrize("method,path,body", ADMIN_ONLY)
def test_member_denied_admin_routes(member_a, method, path, body):
    r = getattr(member_a, method)(path, **({"json": body} if body is not None else {}))
    assert r.status_code == 403, (path, r.text)
    assert r.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("method,path,body", ADMIN_ONLY)
def test_api_key_denied_admin_routes(app, admin_a, method, path, body):
    key = _api_key(admin_a)["key"]
    c = TestClient(app)
    r = getattr(c, method)(path, headers={"X-API-Key": key}, **({"json": body} if body is not None else {}))
    assert r.status_code == 403, (path, r.text)


@pytest.mark.parametrize("method,path,body", ADMIN_ONLY)
def test_anonymous_denied(app, tenants, method, path, body):
    r = getattr(TestClient(app), method)(path, **({"json": body} if body is not None else {}))
    assert r.status_code == 401


@pytest.mark.parametrize("method,path", MEMBER_OK)
def test_member_and_api_key_allowed(app, admin_a, member_a, method, path):
    assert getattr(member_a, method)(path).status_code == 200
    key = _api_key(admin_a)["key"]
    assert TestClient(app).get(path, headers={"X-API-Key": key}).status_code == 200


def test_member_cannot_delete_creators_or_links(admin_a, member_a):
    cr = _mk_creator(member_a)
    assert member_a.delete(f"/api/v1/creators/{cr['id']}").status_code == 403
    lk = member_a.post(
        "/api/v1/links", json={"channel": "ig", "destination_url": "https://example.com", "campaign": "c1"}
    ).json()
    assert member_a.delete(f"/api/v1/links/{lk['id']}").status_code == 403
    assert admin_a.delete(f"/api/v1/links/{lk['id']}").status_code == 200
    assert admin_a.delete(f"/api/v1/creators/{cr['id']}").status_code == 200
    assert admin_a.get(f"/api/v1/creators/{cr['id']}").status_code == 404


def test_api_key_cannot_use_session_only_routes(app, admin_a):
    key = _api_key(admin_a)["key"]
    c = TestClient(app, headers={"X-API-Key": key})
    assert c.get("/api/v1/auth/me").status_code == 403
    assert c.post("/api/v1/auth/totp/setup").status_code == 403


def test_invalid_and_revoked_api_key(app, admin_a):
    k = _api_key(admin_a)
    c = TestClient(app)
    assert c.get("/api/v1/leads", headers={"X-API-Key": "ofm_bad"}).status_code == 401
    assert c.get("/api/v1/leads", headers={"X-API-Key": k["key"][:-2] + "zz"}).status_code == 401
    assert admin_a.delete(f"/api/v1/api-keys/{k['id']}").status_code == 200
    assert c.get("/api/v1/leads", headers={"X-API-Key": k["key"]}).status_code == 401
    assert (
        "key" not in admin_a.get("/api/v1/api-keys").json()["items"][0]
        if admin_a.get("/api/v1/api-keys").json()["items"]
        else True
    )


def test_api_key_writes_skip_csrf(app, admin_a):
    key = _api_key(admin_a)["key"]
    c = TestClient(app, headers={"X-API-Key": key})
    assert c.post("/api/v1/creators", json={"name": "K", "handle": "kk"}).status_code == 201


def test_tenant_isolation(app, admin_a, admin_b, client):
    cr = _mk_creator(admin_a)
    lk = admin_a.post(
        "/api/v1/links",
        json={"channel": "ig", "destination_url": "https://example.com", "campaign": "c", "slug": "alpha-ig"},
    ).json()
    lead = client.post("/api/v1/public/alpha/apply", json=apply_body()).json()
    sub = admin_a.post(
        "/api/v1/automations/webhooks", json={"url": "https://hooks.slack.com/services/x", "events": ["*"]}
    ).json()
    # beta sees nothing of alpha
    for path in (
        "/api/v1/creators",
        "/api/v1/leads",
        "/api/v1/links",
        "/api/v1/automations/webhooks",
        "/api/v1/users",
        "/api/v1/audit",
        "/api/v1/automations/jobs",
    ):
        items = admin_b.get(path).json()["items"]
        ids = {i["id"] for i in items}
        assert not ids & {cr["id"], lk["id"], lead["id"], sub["id"]}, path
    assert all(u["email"].endswith("@beta.test") for u in admin_b.get("/api/v1/users").json()["items"])
    for path in (f"/api/v1/creators/{cr['id']}", f"/api/v1/leads/{lead['id']}"):
        assert admin_b.get(path).status_code == 404
        assert admin_b.patch(path, json={"name": "pwned"}).status_code == 404
    assert admin_b.patch(f"/api/v1/links/{lk['id']}", json={"campaign": "x"}).status_code == 404
    assert admin_b.delete(f"/api/v1/links/{lk['id']}").status_code == 404
    assert admin_b.delete(f"/api/v1/automations/webhooks/{sub['id']}").status_code == 404
    assert admin_b.post(f"/api/v1/leads/{lead['id']}/rescore").status_code == 404
    uid = admin_a.get("/api/v1/auth/me").json()["id"]
    assert admin_b.patch(f"/api/v1/users/{uid}", json={"name": "x"}).status_code == 404
    # link creator_id from another tenant rejected
    r = admin_b.post(
        "/api/v1/links",
        json={"channel": "ig", "destination_url": "https://e.com", "campaign": "c", "creator_id": cr["id"]},
    )
    assert r.status_code == 422
    k = admin_b.get("/api/v1/kpis?range=7d").json()
    assert k["leads"] == 0 and k["clicks"] == 0
    assert admin_b.get("/api/v1/pipeline").json()["stages"][0]["count"] == 0


def test_csrf_enforced_for_cookie_auth(app, admin_a):
    token = admin_a.headers.pop("X-CSRF-Token")
    r = admin_a.post("/api/v1/creators", json={"name": "N", "handle": "nn"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = admin_a.post(
        "/api/v1/creators", json={"name": "N", "handle": "nn"}, headers={"X-CSRF-Token": "wrong"}
    )
    assert r.status_code == 403
    r = admin_a.post("/api/v1/creators", json={"name": "N", "handle": "nn"}, headers={"X-CSRF-Token": token})
    assert r.status_code == 201
    assert admin_a.get("/api/v1/creators").status_code == 200  # GET needs no token


def test_users_admin_crud(admin_a, app):
    r = admin_a.post("/api/v1/users", json={"email": "new@alpha-agency.com", "name": "New", "role": "member"})
    assert r.status_code == 201
    u = r.json()
    assert u["temporary_password"]
    assert admin_a.post("/api/v1/users", json={"email": "new@alpha-agency.com"}).status_code == 409
    assert (
        admin_a.post("/api/v1/users", json={"email": "w@alpha-agency.com", "password": "short"}).status_code
        == 422
    )
    r = admin_a.patch(
        f"/api/v1/users/{u['id']}",
        json={"role": "admin", "name": "Renamed", "password": "An0ther-Long-Passw0rd"},
    )
    assert r.json()["role"] == "admin" and r.json()["name"] == "Renamed"
    me = admin_a.get("/api/v1/auth/me").json()
    assert admin_a.delete(f"/api/v1/users/{me['id']}").json()["error"]["code"] == "cannot_delete_self"
    assert admin_a.delete(f"/api/v1/users/{u['id']}").status_code == 200
    assert (
        admin_a.patch(f"/api/v1/users/{me['id']}", json={"role": "member"}).json()["error"]["code"]
        == "last_admin"
    )
    assert admin_a.patch("/api/v1/users/not-a-uuid", json={"name": "x"}).status_code == 404


def test_pagination_cursor(admin_a):
    for i in range(5):
        _mk_creator(admin_a, f"c{i}")
    p1 = admin_a.get("/api/v1/creators?limit=2").json()
    assert len(p1["items"]) == 2 and p1["next_cursor"]
    p2 = admin_a.get(f"/api/v1/creators?limit=2&cursor={p1['next_cursor']}").json()
    p3 = admin_a.get(f"/api/v1/creators?limit=2&cursor={p2['next_cursor']}").json()
    ids = [i["id"] for i in p1["items"] + p2["items"] + p3["items"]]
    assert len(set(ids)) == 5 and p3["next_cursor"] is None
    assert admin_a.get("/api/v1/creators?cursor=@@@").status_code == 400
