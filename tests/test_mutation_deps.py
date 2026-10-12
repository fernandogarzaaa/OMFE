"""Unit tests for api/deps.py (principal resolution, RBAC, CSRF), written to kill surviving
testteeth mutants. Calls the dependencies directly with real Starlette requests and a real DB."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from starlette.requests import Request

from ofm_engine.api import deps
from ofm_engine.api.errors import ApiError
from ofm_engine.models import ApiKey, Tenant, User
from ofm_engine.security import tokens


def _request(method="GET", headers=None, cookies=None, client=("203.0.113.9", 1234)):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if cookies:
        raw.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
    scope = {"type": "http", "method": method, "path": "/", "headers": raw, "query_string": b""}
    if client is not None:
        scope["client"] = client
    return Request(scope)


def _err(exc_info) -> tuple[int, str, str]:
    e = exc_info.value
    return e.status, e.code, e.message


def _user(db, tenant_id, role="admin") -> User:
    return db.scalar(select(User).where(User.tenant_id == tenant_id, User.role == role))


def _token(u: User, **kw) -> str:
    args = dict(user_id=u.id, tenant_id=u.tenant_id, role=u.role, token_version=u.token_version)
    args.update(kw)
    return tokens.create_access_token(**args)


def _add_key(db, tenant_id) -> tuple[str, ApiKey]:
    plain, prefix, digest = tokens.new_api_key()
    k = ApiKey(tenant_id=tenant_id, name="k", prefix=prefix, key_hash=digest)
    db.add(k)
    db.commit()
    return plain, k


# --------------------------------------------------------------------------- Principal
def test_principal_properties():
    uid, kid, tid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    admin = deps.Principal(tenant_id=tid, role="admin", kind="user", user_id=uid)
    assert admin.actor_id == uid and admin.is_admin is True and admin.mfa is False
    member = deps.Principal(tenant_id=tid, role="member", kind="user", user_id=uid)
    assert member.is_admin is False
    svc = deps.Principal(tenant_id=tid, role="admin", kind="api_key", api_key_id=kid)
    assert svc.actor_id == kid and svc.is_admin is False  # API keys are never admins
    assert deps.Principal(tenant_id=tid, role="service", kind="api_key").actor_id is None


def test_client_ip():
    assert deps.client_ip(_request()) == "203.0.113.9"
    assert deps.client_ip(_request(client=None)) == ""


# --------------------------------------------------------------------------- CSRF
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_csrf_skipped_for_safe_methods(method):
    assert deps.check_csrf(_request(method)) is None


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_csrf_enforced_for_unsafe_methods(method):
    with pytest.raises(ApiError) as ei:
        deps.check_csrf(_request(method))
    assert _err(ei)[:2] == (403, "csrf_failed")
    with pytest.raises(ApiError):
        deps.check_csrf(_request(method, {tokens.CSRF_HEADER: "a"}, {tokens.CSRF_COOKIE: "b"}))
    assert deps.check_csrf(_request(method, {tokens.CSRF_HEADER: "tok"}, {tokens.CSRF_COOKIE: "tok"})) is None


def test_safe_methods_constant():
    assert {"GET", "HEAD", "OPTIONS"} == deps.SAFE_METHODS


# --------------------------------------------------------------------------- API keys
def test_api_key_principal_happy_path(db, tenants):
    plain, key = _add_key(db, tenants["a"])
    assert key.last_used_at is None
    p = deps._principal_from_api_key(db, plain)
    assert p == deps.Principal(tenant_id=tenants["a"], role="service", kind="api_key", api_key_id=key.id)
    assert p.user_id is None and p.mfa is False
    assert key.last_used_at is not None


def test_api_key_rejections(db, tenants):
    plain, key = _add_key(db, tenants["a"])
    _, prefix, _ = plain.split("_", 2)
    cases = [
        "garbage",
        f"ofm_{'0' * 12}_{'x' * 40}",  # unknown prefix
        f"ofm_{prefix}_{'x' * 40}",  # right prefix, wrong secret
    ]
    for raw in cases:
        with pytest.raises(ApiError) as ei:
            deps._principal_from_api_key(db, raw)
        assert _err(ei)[:2] == (401, "invalid_api_key")
    key.revoked_at = datetime.now(UTC)
    db.commit()
    with pytest.raises(ApiError) as ei:
        deps._principal_from_api_key(db, plain)
    assert _err(ei)[:2] == (401, "invalid_api_key")


def test_api_key_rejected_when_tenant_inactive(db, tenants):
    plain, _ = _add_key(db, tenants["a"])
    db.get(Tenant, tenants["a"]).active = False
    db.commit()
    with pytest.raises(ApiError) as ei:
        deps._principal_from_api_key(db, plain)
    assert _err(ei)[:2] == (401, "invalid_api_key")


# --------------------------------------------------------------------------- access tokens
def test_load_user_from_access_ok(db, tenants):
    u = _user(db, tenants["a"])
    user, claims = deps.load_user_from_access(db, _token(u, mfa=True))
    assert user.id == u.id and claims.user_id == u.id and claims.mfa is True


def test_load_user_from_access_invalid_token(db, tenants):
    with pytest.raises(ApiError) as ei:
        deps.load_user_from_access(db, "not-a-jwt")
    assert _err(ei) == (401, "unauthorized", "invalid or expired session")


@pytest.mark.parametrize("mutation", ["deleted", "inactive", "other_tenant", "token_version", "missing"])
def test_load_user_from_access_revoked(db, tenants, mutation):
    u = _user(db, tenants["a"])
    tok = _token(u)
    if mutation == "deleted":
        u.deleted_at = datetime.now(UTC)
    elif mutation == "inactive":
        u.is_active = False
    elif mutation == "other_tenant":
        tok = _token(u, tenant_id=tenants["b"])
    elif mutation == "token_version":
        u.token_version += 1
    elif mutation == "missing":
        tok = _token(u, user_id=uuid.uuid4())
    db.commit()
    with pytest.raises(ApiError) as ei:
        deps.load_user_from_access(db, tok)
    assert _err(ei) == (401, "unauthorized", "session revoked")


def test_load_user_from_access_tenant_inactive(db, tenants):
    u = _user(db, tenants["a"])
    db.get(Tenant, tenants["a"]).active = False
    db.commit()
    with pytest.raises(ApiError) as ei:
        deps.load_user_from_access(db, _token(u))
    assert _err(ei) == (401, "unauthorized", "tenant inactive")


# --------------------------------------------------------------------------- get_principal
def test_get_principal_api_key_header_is_stripped(db, tenants):
    plain, key = _add_key(db, tenants["a"])
    req = _request("POST", {"X-API-Key": f"  {plain} "})  # API keys don't need CSRF
    p = deps.get_principal(req, db)
    assert p.kind == "api_key" and p.api_key_id == key.id and req.state.principal is p


def test_get_principal_cookie_session(db, tenants):
    u = _user(db, tenants["a"], "member")
    req = _request("GET", cookies={tokens.ACCESS_COOKIE: _token(u, mfa=True)})
    p = deps.get_principal(req, db)
    assert p == deps.Principal(tenant_id=u.tenant_id, role="member", kind="user", user_id=u.id, mfa=True)
    assert req.state.principal is p


def test_get_principal_cookie_session_requires_csrf_on_writes(db, tenants):
    u = _user(db, tenants["a"])
    tok = _token(u)
    with pytest.raises(ApiError) as ei:
        deps.get_principal(_request("POST", cookies={tokens.ACCESS_COOKIE: tok}), db)
    assert _err(ei)[:2] == (403, "csrf_failed")
    ok = _request("POST", {tokens.CSRF_HEADER: "c"}, {tokens.ACCESS_COOKIE: tok, tokens.CSRF_COOKIE: "c"})
    assert deps.get_principal(ok, db).user_id == u.id


def test_get_principal_anonymous(db, tenants):
    with pytest.raises(ApiError) as ei:
        deps.get_principal(_request("GET"), db)
    assert _err(ei) == (401, "unauthorized", "authentication required")


# --------------------------------------------------------------------------- RBAC
def test_require_roles():
    tid = uuid.uuid4()
    dep = deps.require_roles("admin", "member")
    member = deps.Principal(tenant_id=tid, role="member", kind="user")
    assert dep(member) is member
    with pytest.raises(ApiError) as ei:
        dep(deps.Principal(tenant_id=tid, role="service", kind="api_key"))
    assert _err(ei) == (403, "forbidden", "insufficient role")


@pytest.mark.parametrize(
    "dep,allowed",
    [
        (deps.require_admin, {"admin"}),
        (deps.require_member, {"admin", "member", "service"}),
        (deps.require_user, {"admin", "member"}),
    ],
)
def test_predefined_role_sets(dep, allowed):
    tid = uuid.uuid4()
    for role in ("admin", "member", "service", "owner"):
        p = deps.Principal(tenant_id=tid, role=role, kind="user")
        if role in allowed:
            assert dep(p) is p
        else:
            with pytest.raises(ApiError):
                dep(p)


def test_get_current_user_and_tenant_ctx(db, tenants):
    u = _user(db, tenants["a"])
    p = deps.Principal(tenant_id=u.tenant_id, role="admin", kind="user", user_id=u.id)
    assert deps.get_current_user(p, db).id == u.id
    ctx = deps.tenant_ctx(db, p)
    assert ctx.tenant.id == tenants["a"]


def test_parse_uuid():
    u = uuid.uuid4()
    assert deps.parse_uuid(str(u)) == u
    with pytest.raises(ApiError) as ei:
        deps.parse_uuid("nope", "creator")
    assert _err(ei) == (404, "not_found", "creator not found")
    with pytest.raises(ApiError) as ei:
        deps.parse_uuid("nope")
    assert _err(ei)[2] == "resource not found"
