"""Envelope encryption for integration secrets.

Scheme
------
* A fresh 256-bit data-encryption key (DEK) is generated per secret.
* The value is encrypted with AES-256-GCM using that DEK and a 96-bit random nonce. The
  associated data binds the ciphertext to its tenant + secret name, so a row copied to
  another tenant (or renamed) fails to decrypt.
* The DEK is wrapped with the key-encryption key (KEK): a `MultiFernet` built from
  `MASTER_KEYS` (comma separated; the first key is primary). Rotation = prepend a new key,
  run `ofm rotate-keys` (re-wraps every DEK with the new primary), then drop the old key.

The master keys never touch the database. Plaintext secrets are never returned by the API.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..settings import get_settings


class DecryptionError(Exception):
    """Raised when a ciphertext cannot be authenticated / decrypted."""


@dataclass(frozen=True)
class Envelope:
    wrapped_dek: bytes
    nonce: bytes
    ciphertext: bytes


def generate_master_key() -> str:
    return Fernet.generate_key().decode()


def _kek(master_keys: str | None = None) -> MultiFernet:
    raw = master_keys if master_keys is not None else get_settings().master_keys.get_secret_value()
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise ValueError("MASTER_KEYS is empty")
    return MultiFernet([Fernet(k.encode()) for k in keys])


def _aad(tenant_id: str, name: str) -> bytes:
    return f"ofm-engine:v1:{tenant_id}:{name}".encode()


def encrypt(plaintext: str, *, tenant_id: str, name: str, master_keys: str | None = None) -> Envelope:
    dek = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    ciphertext = AESGCM(dek).encrypt(nonce, plaintext.encode("utf-8"), _aad(tenant_id, name))
    wrapped = _kek(master_keys).encrypt(dek)
    return Envelope(wrapped_dek=wrapped, nonce=nonce, ciphertext=ciphertext)


def decrypt(env: Envelope, *, tenant_id: str, name: str, master_keys: str | None = None) -> str:
    try:
        dek = _kek(master_keys).decrypt(env.wrapped_dek)
        return AESGCM(dek).decrypt(env.nonce, env.ciphertext, _aad(tenant_id, name)).decode("utf-8")
    except (InvalidToken, InvalidTag, ValueError) as exc:
        raise DecryptionError("secret could not be decrypted") from exc


def rewrap(env: Envelope, master_keys: str | None = None) -> Envelope:
    """Re-wrap the DEK with the current primary KEK (ciphertext untouched)."""
    try:
        new_wrapped = _kek(master_keys).rotate(env.wrapped_dek)
    except InvalidToken as exc:
        raise DecryptionError("DEK could not be unwrapped with any configured master key") from exc
    return Envelope(wrapped_dek=new_wrapped, nonce=env.nonce, ciphertext=env.ciphertext)


# Compact string form (used for small fields like TOTP secrets / webhook secrets)
def encrypt_str(plaintext: str, *, tenant_id: str, name: str, master_keys: str | None = None) -> str:
    e = encrypt(plaintext, tenant_id=tenant_id, name=name, master_keys=master_keys)
    parts = (e.wrapped_dek, e.nonce, e.ciphertext)
    return "v1." + ".".join(base64.urlsafe_b64encode(p).decode() for p in parts)


def decrypt_str(token: str, *, tenant_id: str, name: str, master_keys: str | None = None) -> str:
    try:
        version, w, n, c = token.split(".")
        if version != "v1":
            raise ValueError("unknown version")
        env = Envelope(*(base64.urlsafe_b64decode(x.encode()) for x in (w, n, c)))
    except ValueError as exc:
        raise DecryptionError("malformed encrypted value") from exc
    return decrypt(env, tenant_id=tenant_id, name=name, master_keys=master_keys)


def rewrap_str(token: str, master_keys: str | None = None) -> str:
    try:
        version, w, n, c = token.split(".")
        if version != "v1":
            raise ValueError("unknown version")
        env = Envelope(*(base64.urlsafe_b64decode(x.encode()) for x in (w, n, c)))
    except ValueError as exc:
        raise DecryptionError("malformed encrypted value") from exc
    new = rewrap(env, master_keys)
    return "v1." + ".".join(
        base64.urlsafe_b64encode(p).decode() for p in (new.wrapped_dek, new.nonce, new.ciphertext)
    )
