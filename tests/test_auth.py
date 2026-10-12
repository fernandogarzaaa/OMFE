from datetime import UTC, datetime, timedelta

import pyotp
from fastapi.testclient import TestClient

from conftest import PASSWORD, login


def test_login_me_logout(app, tenants):
    c = login(app, "admin@alpha.test")
    me = c.get("/api/v1/auth/me").json()
    assert me["email"] == "admin@alpha.test" and me["role"] == "admin"
    assert me["tenant"]["name"] == "Alpha" and me["totp_enabled"] is False
    r = c.post("/api/v1/auth/logout")
    assert r.status_code == 200
    assert c.get("/api/v1/auth/me").status_code == 401


def test_login_cookies_flags(app, tenants):
    c = TestClient(app)
    r = c.post("/api/v1/auth/login", json={"email": "admin@alpha.test", "password": PASSWORD})
    cookies = r.headers.get_list("set-cookie")
    at = next(x for x in cookies if x.startswith("access_token="))
    rt = next(x for x in cookies if x.startswith("refresh_token="))
    csrf = next(x for x in cookies if x.startswith("csrf_token="))
    assert "HttpOnly" in at and "SameSite=lax" in at
    assert "HttpOnly" in rt and "Path=/api/v1/auth" in rt
    assert "HttpOnly" not in csrf


def test_bad_password_401_and_unknown_user(client, tenants):
    r = client.post("/api/v1/auth/login", json={"email": "admin@alpha.test", "password": "nope"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"
    r = client.post("/api/v1/auth/login", json={"email": "ghost@alpha.test", "password": "nope"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"


def test_lockout_after_max_attempts(client, tenants, db):
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": "member@alpha.test", "password": "wrong"})
    r = client.post("/api/v1/auth/login", json={"email": "member@alpha.test", "password": PASSWORD})
    assert r.status_code == 401 and r.json()["error"]["code"] == "account_locked"
    from ofm_engine.models import AuditLog, User

    u = db.query(User).filter_by(email="member@alpha.test").one()
    assert u.locked_until is not None
    assert db.query(AuditLog).filter_by(action="auth.login_locked").count() == 1
    # lock expires
    u.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    r = client.post("/api/v1/auth/login", json={"email": "member@alpha.test", "password": PASSWORD})
    assert r.status_code == 200


def test_refresh_rotation_and_reuse_detection(app, tenants):
    c = login(app, "admin@alpha.test")
    old_rt = c.cookies.get("refresh_token", path="/api/v1/auth")
    r = c.post("/api/v1/auth/refresh")
    assert r.status_code == 200, r.text
    new_rt = c.cookies.get("refresh_token", path="/api/v1/auth")
    assert new_rt and new_rt != old_rt
    # attacker replays the old (rotated) token
    evil = TestClient(app)
    evil.cookies.set("refresh_token", old_rt, path="/api/v1/auth")
    evil.cookies.set("csrf_token", "x")
    r = evil.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": "x"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "refresh_reused"
    # the whole family is revoked: legitimate holder's newest token no longer works
    c.headers["X-CSRF-Token"] = c.cookies.get("csrf_token")
    r = c.post("/api/v1/auth/refresh")
    assert r.status_code == 401


def test_refresh_requires_csrf_and_token(app, tenants):
    c = login(app, "admin@alpha.test")
    del c.headers["X-CSRF-Token"]
    assert c.post("/api/v1/auth/refresh").status_code == 403
    anon = TestClient(app)
    anon.cookies.set("csrf_token", "y")
    r = anon.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": "y"})
    assert r.status_code == 401


def test_refresh_expired(app, tenants, db):
    from ofm_engine.models import RefreshToken

    c = login(app, "admin@alpha.test")
    for rt in db.query(RefreshToken).all():
        rt.expires_at = datetime.now(UTC) - timedelta(seconds=5)
    db.commit()
    assert c.post("/api/v1/auth/refresh").status_code == 401


def test_totp_flow(app, tenants):
    c = login(app, "admin@alpha.test")
    uri = c.post("/api/v1/auth/totp/setup").json()["otpauth_uri"]
    secret = pyotp.parse_uri(uri).secret
    assert c.post("/api/v1/auth/totp/verify", json={"code": "000000"}).status_code == 401
    r = c.post("/api/v1/auth/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200
    c.headers["X-CSRF-Token"] = c.cookies.get("csrf_token")
    assert c.get("/api/v1/auth/me").json()["totp_enabled"] is True
    # cannot re-setup while enabled
    assert c.post("/api/v1/auth/totp/setup").status_code == 409
    anon = TestClient(app)
    r = anon.post("/api/v1/auth/login", json={"email": "admin@alpha.test", "password": PASSWORD})
    assert r.status_code == 401 and r.json()["error"]["code"] == "totp_required"
    r = anon.post("/api/v1/auth/login", json={"email": "admin@alpha.test", "password": PASSWORD, "totp": "123456"})
    assert r.json()["error"]["code"] == "invalid_totp"
    login(app, "admin@alpha.test", totp=pyotp.TOTP(secret).now())


def test_totp_verify_without_setup(app, tenants):
    c = login(app, "member@alpha.test")
    r = c.post("/api/v1/auth/totp/verify", json={"code": "123456"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "totp_not_setup"


def test_tampered_or_missing_access_token(app, tenants):
    c = login(app, "admin@alpha.test")
    tok = c.cookies.get("access_token")
    c.cookies.set("access_token", tok[:-3] + "abc")
    assert c.get("/api/v1/auth/me").status_code == 401


def test_deactivated_user_session_revoked(app, tenants):
    admin = login(app, "admin@alpha.test")
    member = login(app, "member@alpha.test")
    uid = member.get("/api/v1/auth/me").json()["id"]
    assert admin.patch(f"/api/v1/users/{uid}", json={"is_active": False}).status_code == 200
    assert member.get("/api/v1/auth/me").status_code == 401
