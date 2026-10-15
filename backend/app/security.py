"""Secrets (KTD7): high-entropy values stored only as HMAC verifiers; passwords use Argon2id."""

import asyncio
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from pwdlib import PasswordHash

_passwords = PasswordHash.recommended()
# Hashed once so unknown-email sign-ins spend the same Argon2 time as wrong passwords.
_DUMMY_HASH = _passwords.hash(secrets.token_urlsafe(16))


@dataclass(frozen=True)
class IssuedSecret:
    prefix: str  # non-secret lookup key
    value: str  # shown once, never stored
    verifier: bytes


def issue(kind: str, pepper: str) -> IssuedSecret:
    """Create a `lat_<kind>_<prefix>_<secret>` value with 256 bits of secret entropy."""
    prefix = secrets.token_hex(6)
    value = f"lat_{kind}_{prefix}_{secrets.token_urlsafe(32)}"
    return IssuedSecret(prefix=prefix, value=value, verifier=verifier(value, pepper))


def verifier(value: str, pepper: str) -> bytes:
    # A fast keyed hash is enough: the input is random, unlike a password.
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).digest()


def parse_prefix(value: str, kind: str) -> str | None:
    parts = value.split("_", 3)
    if len(parts) != 4 or parts[0] != "lat" or parts[1] != kind or not parts[2]:
        return None
    return parts[2]


def matches(value: str, stored: bytes, pepper: str) -> bool:
    return hmac.compare_digest(verifier(value, pepper), stored)


# Argon2 is deliberately slow; run it in a thread so one sign-in does not stall the event loop.
async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_passwords.hash, password)


async def check_password(password: str, password_hash: str | None) -> bool:
    if password_hash is None:
        await asyncio.to_thread(_passwords.verify, password, _DUMMY_HASH)
        return False
    return await asyncio.to_thread(_passwords.verify, password, password_hash)
