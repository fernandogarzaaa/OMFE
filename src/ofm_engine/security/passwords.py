"""Password hashing (argon2id) and policy.

Cost parameters come from settings (ARGON2_TIME_COST / ARGON2_MEMORY_COST / ARGON2_PARALLELISM) so
tests can run cheaply while production refuses to boot below the OWASP minimum.
Hashes created with older/lower parameters are flagged by ``needs_rehash`` and upgraded on next login.
"""

from __future__ import annotations

from functools import lru_cache

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from ofm_engine.settings import get_settings

MIN_LENGTH = 12
MAX_LENGTH = 128
_COMMON = {"password1234", "123456789012", "qwertyuiop12", "letmein12345", "passwordpassword"}


@lru_cache
def _hasher_for(time_cost: int, memory_cost: int, parallelism: int) -> PasswordHasher:
    return PasswordHasher(time_cost=time_cost, memory_cost=memory_cost, parallelism=parallelism, type=Type.ID)


def _hasher() -> PasswordHasher:
    s = get_settings()
    return _hasher_for(s.argon2_time_cost, s.argon2_memory_cost, s.argon2_parallelism)


@lru_cache
def _dummy_hash(time_cost: int, memory_cost: int, parallelism: int) -> str:
    # Equalises timing when the user does not exist (prevents user enumeration).
    return _hasher_for(time_cost, memory_cost, parallelism).hash("timing-equaliser-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    s = get_settings()
    target = password_hash or _dummy_hash(s.argon2_time_cost, s.argon2_memory_cost, s.argon2_parallelism)
    try:
        ok = _hasher().verify(target, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return ok and password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    return _hasher().check_needs_rehash(password_hash)


def password_problems(password: str, email: str = "") -> list[str]:
    problems = []
    if len(password) < MIN_LENGTH:
        problems.append(f"must be at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        problems.append(f"must be at most {MAX_LENGTH} characters")
    if password.lower() in _COMMON:
        problems.append("is too common")
    local = email.split("@")[0].lower()
    if local and len(local) >= 4 and local in password.lower():
        problems.append("must not contain your email name")
    return problems
