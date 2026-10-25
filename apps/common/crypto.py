"""Cryptographic helpers shared by API keys and one-time tokens."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.utils.crypto import constant_time_compare

KEY_PREFIX_LENGTH = 12


def _fernet() -> Fernet:
    """Cipher for secrets the server must read back (provider API keys).

    API keys we only *verify* are hashed with ``hmac_digest``. Credentials we
    must replay to a third party need reversible encryption, so they use Fernet
    keyed from ``AI_CREDENTIALS_KEY`` when set, otherwise from ``SECRET_KEY``.
    Rotating either value invalidates stored ciphertext — callers treat that as
    "no credential" rather than crashing a request.
    """
    material = getattr(settings, "AI_CREDENTIALS_KEY", "") or settings.SECRET_KEY
    digest = hashlib.sha256(f"ai-credentials:{material}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    """Encrypt a secret for storage. Empty input stays empty."""
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Decrypt a stored secret. Raises ``ValueError`` if the key changed."""
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValueError("Stored credential cannot be decrypted with the current key.") from exc


def generate_secret(nbytes: int = 32) -> str:
    """Return a URL-safe, cryptographically secure random string."""
    return secrets.token_urlsafe(nbytes)


def hmac_digest(value: str) -> str:
    """Keyed digest used to store secrets without keeping the plaintext.

    A server-side pepper (``SECRET_KEY``) means a database dump alone is not
    enough to verify guesses offline.
    """
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_digest(value: str, digest: str) -> bool:
    """Constant-time comparison of a candidate secret against a stored digest."""
    return constant_time_compare(hmac_digest(value), digest)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def mask_secret(value: str, visible: int = 4) -> str:
    """Render a secret for logs/UI: ``sk_live_ab…`` -> ``sk_live_ab…****``."""
    if not value:
        return ""
    if len(value) <= visible:
        return "*" * len(value)
    return f"{value[:visible]}{'*' * 6}"
