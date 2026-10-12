import time
from datetime import UTC, datetime, timedelta

import pyotp
import pytest

from ofm_engine.security import crypto, signing, ssrf, tokens
from ofm_engine.security.ratelimit import MemoryBackend, RateLimiter, make_limiter


# --------------------------------------------------------------------------- crypto
def test_crypto_roundtrip_and_aad_binding():
    env = crypto.encrypt("pit-secret-value", tenant_id="t1", name="GHL_TOKEN")
    assert crypto.decrypt(env, tenant_id="t1", name="GHL_TOKEN") == "pit-secret-value"
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(env, tenant_id="t2", name="GHL_TOKEN")  # copied to another tenant
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(env, tenant_id="t1", name="META_CAPI_TOKEN")  # renamed


def test_crypto_tamper_detected():
    env = crypto.encrypt("hello", tenant_id="t", name="n")
    flipped = bytes([env.ciphertext[0] ^ 1]) + env.ciphertext[1:]
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(crypto.Envelope(env.wrapped_dek, env.nonce, flipped), tenant_id="t", name="n")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(crypto.Envelope(b"garbage", env.nonce, env.ciphertext), tenant_id="t", name="n")
    s = crypto.encrypt_str("x", tenant_id="t", name="n")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt_str("v2." + s[3:], tenant_id="t", name="n")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt_str("nonsense", tenant_id="t", name="n")
    with pytest.raises(crypto.DecryptionError):
        crypto.rewrap_str("nonsense")


def test_crypto_key_rotation():
    old = crypto.generate_master_key()
    new = crypto.generate_master_key()
    s = crypto.encrypt_str("rotate-me", tenant_id="t", name="n", master_keys=old)
    both = f"{new},{old}"
    rotated = crypto.rewrap_str(s, master_keys=both)
    assert crypto.decrypt_str(rotated, tenant_id="t", name="n", master_keys=new) == "rotate-me"
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt_str(s, tenant_id="t", name="n", master_keys=new)  # old wrap needs old key
    with pytest.raises(crypto.DecryptionError):
        crypto.rewrap(crypto.Envelope(b"bad", b"n", b"c"), master_keys=new)
    with pytest.raises(ValueError):
        crypto.encrypt("x", tenant_id="t", name="n", master_keys=" , ")


def test_rotate_all_secrets_in_db(db, tenants, monkeypatch):
    from ofm_engine.models import Tenant, User
    from ofm_engine.services import secrets as ss
    from ofm_engine.settings import get_settings, reset_settings_cache

    old = get_settings().master_keys.get_secret_value()
    t = db.get(Tenant, tenants["a"])
    ss.set_secret(db, t, "GHL_TOKEN", "pit-abcdefghijklmnop")
    u = db.query(User).first()
    u.totp_secret_enc = crypto.encrypt_str("JBSWY3DPEHPK3PXP", tenant_id=str(u.tenant_id), name="totp")
    db.commit()
    new = crypto.generate_master_key()
    monkeypatch.setenv("MASTER_KEYS", f"{new},{old}")
    reset_settings_cache()
    assert ss.rotate_all(db) == 2
    db.commit()
    monkeypatch.setenv("MASTER_KEYS", new)
    reset_settings_cache()
    assert ss.get_secret(db, t, "GHL_TOKEN") == "pit-abcdefghijklmnop"
    statuses = {s.name: s for s in ss.secret_statuses(db, t)}
    assert statuses["GHL_TOKEN"].source == "vault" and statuses["GHL_TOKEN"].hint == "op"
    assert ss.delete_secret(db, t, "GHL_TOKEN") is True and ss.delete_secret(db, t, "GHL_TOKEN") is False
    with pytest.raises(ss.UnknownSecretError):
        ss.get_secret(db, t, "NOPE")
    with pytest.raises(ValueError):
        ss.set_secret(db, t, "GHL_TOKEN", "  ")


def test_env_secret_fallback(db, tenants, monkeypatch):
    from ofm_engine.models import Tenant
    from ofm_engine.services import secrets as ss

    monkeypatch.setenv("CLIENT_ALPHA_META_CAPI_TOKEN", "from-env")
    t = db.get(Tenant, tenants["a"])
    assert ss.get_secret(db, t, "META_CAPI_TOKEN") == "from-env"
    assert {s.name: s.source for s in ss.secret_statuses(db, t)}["META_CAPI_TOKEN"] == "env"


# --------------------------------------------------------------------------- signing
def test_webhook_signature_and_replay_window():
    body = b'{"a":1}'
    hdr = signing.sign("s3cret", body, timestamp=1_700_000_000)
    assert signing.verify("s3cret", body, hdr, now=1_700_000_100) == 1_700_000_000
    with pytest.raises(signing.SignatureError, match="tolerance"):
        signing.verify("s3cret", body, hdr, now=1_700_000_000 + 301)
    with pytest.raises(signing.SignatureError, match="mismatch"):
        signing.verify("s3cret", b'{"a":2}', hdr, now=1_700_000_000)
    with pytest.raises(signing.SignatureError):
        signing.verify("s3cret", body, None)
    with pytest.raises(signing.SignatureError):
        signing.verify("s3cret", body, "t=abc,v1=x")
    with pytest.raises(signing.SignatureError):
        signing.verify("s3cret", body, "garbage")
    multi = hdr + ",v1=deadbeef"
    assert signing.verify("s3cret", body, multi, now=1_700_000_000)


# --------------------------------------------------------------------------- SSRF
@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1", "169.254.169.254",
                                "100.64.0.1", "::1", "fd00::1", "0.0.0.0", "::ffff:127.0.0.1", "224.0.0.1", "nope"])
def test_ssrf_blocks_private(ip):
    assert ssrf.is_public_ip(ip) is False


def test_ssrf_webhook_checks():
    pub = lambda h: ["93.184.216.34"]  # noqa: E731
    assert ssrf.check_webhook_url("https://example.org/hook", resolver=pub)
    for url in ("http://example.org", "https://u:p@example.org", "https://example.org:22/x", "https:///x"):
        with pytest.raises(ssrf.UnsafeURLError):
            ssrf.check_webhook_url(url, resolver=pub)
    with pytest.raises(ssrf.UnsafeURLError, match="non-public"):
        ssrf.check_webhook_url("https://rebind.example/x", resolver=lambda h: ["93.184.216.34", "10.0.0.1"])
    with pytest.raises(ssrf.UnsafeURLError):
        ssrf.check_webhook_url("https://empty.example/x", resolver=lambda h: [])

    def err(h):
        raise OSError("nx")

    with pytest.raises(ssrf.UnsafeURLError, match="resolve"):
        ssrf.check_webhook_url("https://nx.example/x", resolver=err)


def test_ssrf_operator_allowlist(monkeypatch):
    from ofm_engine.settings import reset_settings_cache

    monkeypatch.setenv("OUTBOUND_ALLOWED_HOSTS", "n8n.internal.example, make.example")
    reset_settings_cache()
    assert ssrf.check_webhook_url("https://n8n.internal.example/webhook/1", resolver=lambda h: ["10.0.0.1"])


def test_ssrf_integration_allowlist():
    assert ssrf.check_integration_url("https://graph.facebook.com/v25.0/1/events")
    with pytest.raises(ssrf.UnsafeURLError):
        ssrf.check_integration_url("https://evil.example/x")
    with pytest.raises(ssrf.UnsafeURLError):
        ssrf.check_integration_url("http://graph.facebook.com/x")


# --------------------------------------------------------------------------- tokens
def test_access_token_roundtrip_and_rejections():
    import uuid

    import jwt

    from ofm_engine.settings import get_settings

    uid, tid = uuid.uuid4(), uuid.uuid4()
    t = tokens.create_access_token(user_id=uid, tenant_id=tid, role="admin", token_version=3, mfa=True)
    c = tokens.decode_access_token(t)
    assert (c.user_id, c.tenant_id, c.role, c.token_version, c.mfa) == (uid, tid, "admin", 3, True)
    expired = tokens.create_access_token(user_id=uid, tenant_id=tid, role="admin", token_version=0,
                                         now=datetime.now(UTC) - timedelta(hours=1))
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(expired)
    none_alg = jwt.encode({"sub": str(uid), "tid": str(tid), "role": "admin", "typ": "access"}, None, algorithm="none")
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(none_alg)
    s = get_settings()
    now = int(time.time())
    wrong_role = jwt.encode({"sub": str(uid), "tid": str(tid), "role": "root", "typ": "access", "iss": s.jwt_issuer,
                             "iat": now, "exp": now + 60}, s.jwt_secret.get_secret_value(), algorithm="HS256")
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(wrong_role)
    bad_sub = jwt.encode({"sub": "x", "tid": str(tid), "role": "admin", "typ": "access", "iss": s.jwt_issuer,
                          "iat": now, "exp": now + 60}, s.jwt_secret.get_secret_value(), algorithm="HS256")
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(bad_sub)


def test_api_key_and_csrf_and_totp_helpers():
    plain, prefix, digest = tokens.new_api_key()
    assert tokens.parse_api_key(plain) == prefix and tokens.sha256_hex(plain) == digest
    assert tokens.parse_api_key("ofm_short_x") is None and tokens.parse_api_key("nope") is None
    assert tokens.csrf_matches("a", "a") and not tokens.csrf_matches("a", "b") and not tokens.csrf_matches(None, "a")
    sec = tokens.new_totp_secret()
    assert tokens.verify_totp(sec, pyotp.TOTP(sec).now())
    assert not tokens.verify_totp(sec, "abc") and not tokens.verify_totp(sec, "12345")
    assert "otpauth://totp/" in tokens.totp_uri(sec, "a@b.c")


def test_rate_limiter():
    rl = RateLimiter(MemoryBackend())
    assert all(rl.allow("k", 3) for _ in range(3)) and not rl.allow("k", 3)
    assert rl.allow("other", 3) and rl.allow("k", 0)
    assert isinstance(make_limiter(None), RateLimiter)


# --------------------------------------------------------------------------- logging redaction
def test_log_redaction():
    import json
    import logging

    from ofm_engine.logging_setup import JsonFormatter, RedactingTextFormatter, redact, redact_text

    assert "[REDACTED" in redact_text("Authorization: Bearer abcdefghijklmnop")
    assert "pit-" not in redact_text("token pit-12345678-abcd")
    assert "a@b.com" not in redact_text("mail a@b.com")
    assert redact({"password": "x", "nested": [{"token": "y"}], "ok": 1}) == {
        "password": "[REDACTED]", "nested": [{"token": "[REDACTED]"}], "ok": 1}
    rec = logging.LogRecord("t", logging.INFO, "f", 1, "user %s", ("x@y.com",), None)
    rec.api_key = "secret"
    out = json.loads(JsonFormatter().format(rec))
    assert out["api_key"] == "[REDACTED]" and "x@y.com" not in out["msg"]
    txt = RedactingTextFormatter("%(asctime)s %(message)s").format(rec)
    assert "x@y.com" not in txt and txt[:4].isdigit()


def test_password_policy():
    from ofm_engine.security.passwords import needs_rehash, password_problems

    assert password_problems("short") and password_problems("password1234")
    assert password_problems("johnsmith-long-pass", "johnsmith@x.com")
    assert password_problems("x" * 200)
    assert password_problems("A-Very-Strong-Pass-99") == []
    assert needs_rehash("$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2hoYXNo")
