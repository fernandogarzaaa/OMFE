import pytest

from ofm_engine.security import passwords as pw
from ofm_engine.settings import get_settings, reset_settings_cache


@pytest.fixture(autouse=True)
def _fresh_settings():
    reset_settings_cache()
    yield
    reset_settings_cache()


def test_hash_uses_configured_cost():
    h = pw.hash_password("correct horse battery staple")
    s = get_settings()
    assert h.startswith("$argon2id$")
    assert f"m={s.argon2_memory_cost},t={s.argon2_time_cost},p={s.argon2_parallelism}" in h


def test_verify_roundtrip_and_mismatch():
    h = pw.hash_password("correct horse battery staple")
    assert pw.verify_password(h, "correct horse battery staple") is True
    assert pw.verify_password(h, "wrong horse battery staple") is False


def test_verify_missing_user_is_false_even_for_dummy_password():
    assert pw.verify_password(None, "timing-equaliser-not-a-real-password") is False
    assert pw.verify_password(None, "anything") is False


def test_verify_garbage_hash_is_false():
    assert pw.verify_password("not-a-hash", "x" * 12) is False


def test_needs_rehash_when_cost_changes(monkeypatch):
    h = pw.hash_password("correct horse battery staple")
    assert pw.needs_rehash(h) is False
    monkeypatch.setenv("ARGON2_TIME_COST", "2")
    reset_settings_cache()
    assert pw.needs_rehash(h) is True


@pytest.mark.parametrize(
    ("password", "email", "expected"),
    [
        ("short", "", "must be at least 12 characters"),
        ("x" * 129, "", "must be at most 128 characters"),
        ("PASSWORD1234", "", "is too common"),
        ("my-fernando-pass-123", "fernando@example.com", "must not contain your email name"),
    ],
)
def test_password_problems(password, email, expected):
    assert expected in pw.password_problems(password, email)


def test_password_boundaries_ok():
    assert pw.password_problems("a" * 12) == []
    assert pw.password_problems("a" * 128) == []
    assert pw.password_problems("abc-long-password", "abc@example.com") == []  # local part < 4 chars ignored


def test_production_rejects_weak_argon2(monkeypatch):
    monkeypatch.setenv("ARGON2_TIME_COST", "1")
    reset_settings_cache()
    assert any("ARGON2" in p for p in get_settings().production_problems())
    monkeypatch.setenv("ARGON2_TIME_COST", "3")
    monkeypatch.setenv("ARGON2_MEMORY_COST", "65536")
    reset_settings_cache()
    assert not any("ARGON2" in p for p in get_settings().production_problems())
