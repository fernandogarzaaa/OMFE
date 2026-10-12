"""Boundary / branch tests written to kill surviving testteeth mutants in the security modules.

Each test pins an exact behaviour (a boundary value, an error *message*, a branch that was only
reachable through a fallback) so that a mutated implementation fails loudly.
See quality/mutation/RESULTS.md for the before/after scores.
"""

from __future__ import annotations

import base64
import socket
import uuid
from datetime import UTC, datetime, timedelta

import pyotp
import pytest

from ofm_engine.security import crypto, passwords, signing, ssrf, tokens
from ofm_engine.settings import reset_settings_cache


# --------------------------------------------------------------------------- signing
def test_parse_header_rejects_non_integer_timestamp_with_specific_error():
    with pytest.raises(signing.SignatureError, match="invalid timestamp"):
        signing.parse_header("t=abc,v1=deadbeef")


@pytest.mark.parametrize("header", ["t=1700000000", "v1=deadbeef", "t=1700000000,v2=deadbeef", "x"])
def test_parse_header_requires_both_timestamp_and_v1(header):
    with pytest.raises(signing.SignatureError, match="malformed"):
        signing.parse_header(header)


def test_parse_header_collects_all_v1_and_ignores_unknown_parts():
    assert signing.parse_header(" t=5 , v1=aa, v0=zz, junk, v1=bb") == (5, ["aa", "bb"])


def test_verify_tolerance_boundary_is_inclusive():
    body = b"{}"
    hdr = signing.sign("k", body, timestamp=1_000)
    assert signing.verify("k", body, hdr, tolerance=300, now=1_300) == 1_000  # exactly at edge: ok
    assert signing.verify("k", body, hdr, tolerance=300, now=700) == 1_000  # past side too
    with pytest.raises(signing.SignatureError, match="tolerance"):
        signing.verify("k", body, hdr, tolerance=300, now=1_301)
    with pytest.raises(signing.SignatureError, match="tolerance"):
        signing.verify("k", body, hdr, tolerance=300, now=699)


def test_compute_signature_known_vector():
    import hashlib
    import hmac

    expected = hmac.new(b"k", b"42.payload", hashlib.sha256).hexdigest()
    assert signing.compute_signature("k", 42, b"payload") == expected
    assert signing.sign("k", b"payload", timestamp=42) == f"t=42,v1={expected}"


def test_verify_wrong_secret_is_mismatch():
    hdr = signing.sign("right", b"x", timestamp=10)
    with pytest.raises(signing.SignatureError, match="mismatch"):
        signing.verify("wrong", b"x", hdr, now=10)


# --------------------------------------------------------------------------- ssrf
def test_default_resolver_dedupes_and_sorts(monkeypatch):
    calls = []

    def fake_getaddrinfo(host, port, proto=0):
        calls.append((host, port, proto))
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.35", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800::1", 0, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)
    assert ssrf._default_resolver("example.org") == ["2606:2800::1", "93.184.216.34", "93.184.216.35"]
    assert calls == [("example.org", None, socket.IPPROTO_TCP)]


def test_webhook_check_uses_default_resolver_when_none_given(monkeypatch):
    monkeypatch.setattr(ssrf, "_default_resolver", lambda h: ["10.1.2.3"])
    with pytest.raises(ssrf.UnsafeURLError, match="10.1.2.3"):
        ssrf.check_webhook_url("https://internal.example/x")


def test_webhook_rejects_username_only_and_password_only_credentials():
    pub = lambda h: ["93.184.216.34"]  # noqa: E731
    for url in ("https://user@example.org/x", "https://:pw@example.org/x"):
        with pytest.raises(ssrf.UnsafeURLError, match="credentials"):
            ssrf.check_webhook_url(url, resolver=pub)


@pytest.mark.parametrize("port", [443, 8443])
def test_webhook_allows_standard_tls_ports(port):
    url = f"https://example.org:{port}/hook"
    assert ssrf.check_webhook_url(url, resolver=lambda h: ["93.184.216.34"]) == url


@pytest.mark.parametrize("port", [80, 444, 8080, 8444])
def test_webhook_rejects_other_ports(port):
    with pytest.raises(ssrf.UnsafeURLError, match="port"):
        ssrf.check_webhook_url(f"https://example.org:{port}/hook", resolver=lambda h: ["93.184.216.34"])


def test_webhook_returns_url_unchanged_for_each_accept_path(monkeypatch):
    url = "https://hooks.slack.com/services/x"
    assert ssrf.check_webhook_url(url, resolver=lambda h: ["10.0.0.1"]) == url  # integration host
    monkeypatch.setenv("OUTBOUND_ALLOW_PRIVATE", "true")
    reset_settings_cache()
    private = "https://intranet.example/x"
    assert ssrf.check_webhook_url(private, resolver=lambda h: ["10.0.0.1"]) == private
    monkeypatch.setenv("OUTBOUND_ALLOW_PRIVATE", "false")
    monkeypatch.setenv("OUTBOUND_ALLOWED_HOSTS", "N8N.Example")
    reset_settings_cache()
    assert ssrf.check_webhook_url("https://n8n.example/w", resolver=lambda h: ["10.0.0.1"]) == (
        "https://n8n.example/w"
    )


def test_integration_allowlist_includes_configured_base_hosts(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://LLM.Example.net/v1")
    monkeypatch.setenv("GHL_API_BASE", "https://ghl-proxy.example.net")
    reset_settings_cache()
    hosts = ssrf.allowed_integration_hosts()
    assert {"llm.example.net", "ghl-proxy.example.net", "graph.facebook.com"} <= hosts
    assert ssrf.check_integration_url("https://llm.example.net/v1/chat") == "https://llm.example.net/v1/chat"


def test_is_public_ip_accepts_public_v4_and_v6():
    assert ssrf.is_public_ip("93.184.216.34") is True
    assert ssrf.is_public_ip("2606:4700:4700::1111") is True
    assert ssrf.is_public_ip("::ffff:93.184.216.34") is True


# --------------------------------------------------------------------------- crypto
def test_empty_master_keys_raises_specific_error():
    with pytest.raises(ValueError, match="MASTER_KEYS is empty"):
        crypto.encrypt("x", tenant_id="t", name="n", master_keys=" , ")


def test_envelope_uses_96_bit_nonce():
    env = crypto.encrypt("x", tenant_id="t", name="n")
    assert len(env.nonce) == 12


def test_rewrap_str_rejects_unknown_version_even_if_parts_are_valid():
    s = crypto.encrypt_str("x", tenant_id="t", name="n")
    assert s.startswith("v1.")
    with pytest.raises(crypto.DecryptionError, match="malformed"):
        crypto.rewrap_str("v2." + s[3:])
    parts = s.split(".")
    assert len(parts) == 4 and all(base64.urlsafe_b64decode(p) for p in parts[1:])


# --------------------------------------------------------------------------- passwords
def test_email_local_part_length_boundary():
    pw = "anna-Very-Strong-Pass-99"
    assert "must not contain your email name" in passwords.password_problems(pw, "anna@x.com")  # len 4
    assert passwords.password_problems("bob-Very-Strong-Pass-99", "bob@x.com") == []  # len 3 ignored


def test_password_length_boundaries():
    assert any("at least" in p for p in passwords.password_problems("Ab1-" * 2))
    assert not any("at most" in p for p in passwords.password_problems("Zq9!" * 32))


# --------------------------------------------------------------------------- tokens
def _claims_token(**overrides):
    import jwt

    from ofm_engine.settings import get_settings

    s = get_settings()
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "sub": str(uuid.uuid4()),
        "tid": str(uuid.uuid4()),
        "role": "member",
        "typ": "access",
        "iss": s.jwt_issuer,
        "iat": now,
        "exp": now + 60,
    }
    payload.update(overrides)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, s.jwt_secret.get_secret_value(), algorithm="HS256")


def test_access_token_payload_shape():
    import jwt

    from ofm_engine.settings import get_settings

    s = get_settings()
    uid, tid = uuid.uuid4(), uuid.uuid4()
    issued = datetime(2030, 1, 1, tzinfo=UTC)
    t = tokens.create_access_token(user_id=uid, tenant_id=tid, role="member", token_version=7, now=issued)
    data = jwt.decode(t, options={"verify_signature": False})
    assert data["sub"] == str(uid) and data["tid"] == str(tid) and data["role"] == "member"
    assert data["tv"] == 7 and data["mfa"] is False and data["typ"] == "access"
    assert data["iss"] == s.jwt_issuer
    assert data["iat"] == data["nbf"] == int(issued.timestamp())
    assert data["exp"] - data["iat"] == s.access_token_ttl_seconds
    assert len(data["jti"]) == 32
    assert jwt.get_unverified_header(t)["alg"] == "HS256"


def test_access_token_defaults_for_optional_claims():
    c = tokens.decode_access_token(_claims_token())
    assert c.token_version == 0 and c.mfa is False and c.role == "member"


@pytest.mark.parametrize(
    "override",
    [
        {"typ": "refresh"},
        {"typ": None},
        {"role": "service"},
        {"iss": "someone-else"},
        {"tid": "not-a-uuid"},
        {"tv": "abc"},
        {"exp": None},
        {"iat": None},
        {"tid": None},
        {"role": None},
    ],
)
def test_access_token_rejects_bad_claims(override):
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(_claims_token(**override))


@pytest.mark.filterwarnings("ignore:The HMAC key")
def test_access_token_rejects_wrong_key_and_hs512():
    import jwt

    from ofm_engine.settings import get_settings

    s = get_settings()
    now = int(datetime.now(UTC).timestamp())
    p = {"sub": str(uuid.uuid4()), "tid": str(uuid.uuid4()), "role": "admin", "typ": "access"}
    p |= {"iss": s.jwt_issuer, "iat": now, "exp": now + 60}
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(jwt.encode(p, "x" * 40, algorithm="HS256"))
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(jwt.encode(p, s.jwt_secret.get_secret_value(), algorithm="HS512"))


def test_refresh_token_and_hash():
    raw, digest = tokens.new_refresh_token()
    assert len(raw) == 64 and digest == tokens.sha256_hex(raw) and len(digest) == 64
    assert tokens.new_refresh_token()[0] != raw
    assert tokens.sha256_hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert len(tokens.new_csrf_token()) == 43 and tokens.new_csrf_token() != tokens.new_csrf_token()


def test_csrf_matches_requires_both_values():
    assert tokens.csrf_matches("a", None) is False
    assert tokens.csrf_matches("", "") is False
    assert tokens.csrf_matches("tok", "tok") is True


def test_api_key_format_and_parse_boundaries():
    plain, prefix, digest = tokens.new_api_key()
    assert plain.startswith(f"ofm_{prefix}_") and len(prefix) == 12 and len(digest) == 64
    assert len(plain.split("_", 2)[2]) == 43
    assert tokens.parse_api_key("ofm_" + "a" * 12 + "_" + "b" * 20) == "a" * 12  # min secret length
    assert tokens.parse_api_key("ofm_" + "a" * 12 + "_" + "b" * 19) is None
    assert tokens.parse_api_key("ofm_" + "a" * 11 + "_" + "b" * 30) is None
    assert tokens.parse_api_key("ofm_" + "a" * 13 + "_" + "b" * 30) is None
    assert tokens.parse_api_key("abc_" + "a" * 12 + "_" + "b" * 30) is None
    assert tokens.parse_api_key("ofm_" + "a" * 12 + "_b_" + "c" * 30) == "a" * 12  # secret may hold "_"


def test_totp_verify_window_and_normalisation():
    sec = tokens.new_totp_secret()
    totp = pyotp.TOTP(sec)
    at = datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)
    code = totp.at(at)
    assert tokens.verify_totp(sec, code, at=at)
    assert tokens.verify_totp(sec, f" {code[:3]} {code[3:]} ", at=at)  # spaces tolerated
    assert tokens.verify_totp(sec, totp.at(at - timedelta(seconds=30)), at=at)  # one step back ok
    assert tokens.verify_totp(sec, totp.at(at + timedelta(seconds=30)), at=at)  # one step ahead ok
    assert tokens.verify_totp(sec, totp.at(at - timedelta(seconds=60)), at=at) is False  # window is 1
    assert tokens.verify_totp(sec, totp.at(at + timedelta(seconds=60)), at=at) is False
    assert tokens.verify_totp(sec, code + "0", at=at) is False
    assert tokens.verify_totp(sec, "12a456", at=at) is False
    assert tokens.verify_totp(sec, "", at=at) is False
    assert tokens.verify_totp(sec, None, at=at) is False  # type: ignore[arg-type]
    uri = tokens.totp_uri(sec, "a@b.c")
    assert f"secret={sec}" in uri and "issuer=OFM%20Engine" in uri
    assert "issuer=Acme" in tokens.totp_uri(sec, "a@b.c", issuer="Acme")
