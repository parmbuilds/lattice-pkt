"""Workspace provider keys, encrypted at rest with AES-GCM under a master key (KTD2).

A stored key is `v1 | key id | nonce | ciphertext`. The key id names the master key that
encrypted it, so listing a new master key first keeps older keys readable until each owner
re-saves theirs. The workspace and provider ids are bound as associated data, so a ciphertext
copied to another workspace, or used with another provider, fails to decrypt.
"""

import base64
import binascii
import hashlib
import os
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import DEV_AI_KEY, Settings

VERSION = b"v1"
KID_BYTES = 8
NONCE_BYTES = 12


class KeyUnavailable(Exception):
    """The stored key cannot be decrypted: wrong workspace or provider, tampered, or the master
    key that encrypted it is no longer listed."""


def _master_keys(settings: Settings) -> list[bytes]:
    try:
        raw = [
            base64.b64decode(k.get_secret_value(), validate=True)
            for k in settings.ai_key_encryption_keys
        ]
    except (binascii.Error, ValueError):
        raise RuntimeError("LATTICE_AI_KEY_ENCRYPTION_KEYS entries must be base64.") from None
    if any(len(k) != 32 for k in raw):
        raise RuntimeError("LATTICE_AI_KEY_ENCRYPTION_KEYS entries must decode to 32 bytes.")
    return raw


def check_master_keys(settings: Settings) -> None:
    """Startup check: listed providers need a private master key (KTD2, KTD15)."""
    if not settings.ai_providers:
        return
    if not settings.ai_key_encryption_keys:
        raise RuntimeError("Set LATTICE_AI_KEY_ENCRYPTION_KEYS to encrypt workspace AI keys.")
    _master_keys(settings)
    dev = any(k.get_secret_value() == DEV_AI_KEY for k in settings.ai_key_encryption_keys)
    if dev and not settings.insecure_dev_cookies:
        raise RuntimeError("Set LATTICE_AI_KEY_ENCRYPTION_KEYS to a private value.")


def _kid(key: bytes) -> bytes:
    return hashlib.sha256(key).digest()[:KID_BYTES]


def _aad(workspace_id: UUID, provider_id: str) -> bytes:
    return f"lattice-ai-key|{workspace_id}|{provider_id}".encode()


def encrypt(settings: Settings, workspace_id: UUID, provider_id: str, plaintext: str) -> bytes:
    key = _master_keys(settings)[0]
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(key).encrypt(nonce, plaintext.encode(), _aad(workspace_id, provider_id))
    return VERSION + _kid(key) + nonce + sealed


def decrypt(settings: Settings, workspace_id: UUID, provider_id: str, blob: bytes) -> str:
    if blob[: len(VERSION)] != VERSION:
        raise KeyUnavailable
    kid = blob[len(VERSION) : len(VERSION) + KID_BYTES]
    nonce_at = len(VERSION) + KID_BYTES
    nonce, sealed = blob[nonce_at : nonce_at + NONCE_BYTES], blob[nonce_at + NONCE_BYTES :]
    key = next((k for k in _master_keys(settings) if _kid(k) == kid), None)
    if key is None:
        raise KeyUnavailable
    try:
        return AESGCM(key).decrypt(nonce, sealed, _aad(workspace_id, provider_id)).decode()
    except InvalidTag:
        raise KeyUnavailable from None
