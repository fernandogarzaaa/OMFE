import os

# Cheap argon2 cost for the test suite (production refuses these values).
os.environ.setdefault("ARGON2_TIME_COST", "1")
os.environ.setdefault("ARGON2_MEMORY_COST", "8")
os.environ.setdefault("ARGON2_PARALLELISM", "1")
os.environ.setdefault("ENV", "test")
os.environ["LOG_JSON"] = "false"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ.pop("METRICS_TOKEN", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PASSWORD = "Sup3r-Secret-Passw0rd!"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    from ofm_engine import db as dbmod
    from ofm_engine.services import kpis
    from ofm_engine.settings import reset_settings_cache

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("CLIENTS_DIR", str(tmp_path / "clients"))
    reset_settings_cache()
    dbmod.dispose_engine()
    dbmod.create_all()
    kpis._cache.clear()
    yield
    dbmod.dispose_engine()
    reset_settings_cache()


@pytest.fixture
def db():
    from ofm_engine.db import session_factory

    s = session_factory()()
    yield s
    s.close()


def make_tenant(slug, name=None):
    from ofm_engine.db import session_scope
    from ofm_engine.models import Tenant, User
    from ofm_engine.security.passwords import hash_password

    with session_scope() as s:
        t = Tenant(slug=slug, name=name or slug.title())
        s.add(t)
        s.flush()
        for role in ("admin", "member"):
            s.add(User(tenant_id=t.id, email=f"{role}@{slug}.test", name=f"{slug} {role}", role=role,
                       password_hash=hash_password(PASSWORD)))
        return t.id


@pytest.fixture
def tenants():
    return {"a": make_tenant("alpha"), "b": make_tenant("beta")}


@pytest.fixture
def app():
    from ofm_engine.app import create_app

    return create_app()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def login(app, email, password=PASSWORD, totp=None):
    c = TestClient(app)
    c.__enter__()
    r = c.post("/api/v1/auth/login", json={"email": email, "password": password, **({"totp": totp} if totp else {})})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = c.cookies.get("csrf_token")
    return c


@pytest.fixture
def admin_a(app, tenants):
    return login(app, "admin@alpha.test")


@pytest.fixture
def member_a(app, tenants):
    return login(app, "member@alpha.test")


@pytest.fixture
def admin_b(app, tenants):
    return login(app, "admin@beta.test")


def apply_body(**kw):
    body = {"name": "Ava Test", "email": "ava@example.net", "phone": "+1 555 555 0101",
            "socials": {"instagram": "@ava"}, "followers": {"instagram": 120000, "tiktok": 50000},
            "earnings_range": "5k-10k", "timezone": "America/New_York", "age_confirmed": True, "consent": True,
            "variant": "A", "attribution": {"utm_source": "instagram", "fbc": "fb.1.1700000000000.abc"}}
    body.update(kw)
    return body
