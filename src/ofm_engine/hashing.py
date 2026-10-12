"""PII normalisation + SHA-256 hashing per Meta Conversions API customer-information rules.

Reference: https://developers.facebook.com/docs/marketing-api/conversions-api/parameters/customer-information-parameters/
* em  — trim, lowercase
* ph  — digits only, include country code, strip leading zeros
* fn/ln — lowercase, trim, strip punctuation (UTF-8 kept)
* ct  — lowercase, no spaces/punctuation
* st  — 2-letter lowercase code
* zp  — lowercase, no spaces/dash; US: first 5 digits
* country — ISO-3166 alpha-2 lowercase
* db  — YYYYMMDD
* ge  — "f" | "m"
* external_id — hashing recommended
fbc / fbp / client_ip_address / client_user_agent are NOT hashed.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import string
import unicodedata

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_PUNCT = set(string.punctuation)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_hashed(value: str) -> bool:
    return bool(_SHA256_RE.fullmatch(value))


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_phone(value: str, default_country_code: str = "") -> str:
    digits = re.sub(r"\D", "", value)
    had_plus = value.strip().startswith("+")
    digits = digits.lstrip("0")
    needs_cc = (
        not had_plus and default_country_code and digits and not digits.startswith(default_country_code)
    )
    if needs_cc and len(digits) <= 10:
        digits = default_country_code + digits
    return digits


def normalize_name(value: str) -> str:
    v = unicodedata.normalize("NFC", value.strip().lower())
    return "".join(ch for ch in v if ch not in _PUNCT and not ch.isspace())


def normalize_city(value: str) -> str:
    return normalize_name(value)


def normalize_state(value: str) -> str:
    v = normalize_name(value)
    return v[:2]


def normalize_zip(value: str, country: str = "") -> str:
    v = value.strip().lower().replace(" ", "").replace("-", "")
    if country.lower() == "us":
        v = v[:5]
    return v


def normalize_country(value: str) -> str:
    return normalize_name(value)[:2]


def normalize_dob(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return digits if len(digits) == 8 else ""


def normalize_gender(value: str) -> str:
    v = value.strip().lower()
    if v.startswith("f"):
        return "f"
    if v.startswith("m"):
        return "m"
    return ""


_NORMALIZERS = {
    "em": normalize_email,
    "ph": normalize_phone,
    "fn": normalize_name,
    "ln": normalize_name,
    "ct": normalize_city,
    "st": normalize_state,
    "zp": normalize_zip,
    "country": normalize_country,
    "db": normalize_dob,
    "ge": normalize_gender,
    "external_id": lambda v: v.strip(),
}

HASHED_FIELDS = frozenset(_NORMALIZERS)


def hash_field(field: str, value: str | None) -> str | None:
    """Normalise then SHA-256 a single customer field. Pre-hashed values pass through."""
    if value is None:
        return None
    raw = str(value)
    if is_hashed(raw.strip().lower()):
        return raw.strip().lower()
    normalizer = _NORMALIZERS.get(field)
    if normalizer is None:
        raise ValueError(f"field {field!r} is not a hashed CAPI field")
    normalized = normalizer(raw)
    if not normalized:
        return None
    return sha256(normalized)


def hash_user_data(raw: dict[str, str | None]) -> dict[str, list[str]]:
    """Hash every hashed field in `raw`; returns Meta's array form ({"em": ["<hash>"]})."""
    out: dict[str, list[str]] = {}
    for key, value in raw.items():
        if key in HASHED_FIELDS:
            h = hash_field(key, value)
            if h:
                out[key] = [h]
    return out


def keyed_hash(value: str, salt: str) -> str:
    """HMAC-SHA256 for internal pseudonymisation (IPs, visitor ids). Not reversible without salt."""
    return hmac.new(salt.encode(), value.encode(), hashlib.sha256).hexdigest()
