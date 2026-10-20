import base64
import os
import uuid

import pytest

from app.ai import keys
from app.config import Settings

WS = uuid.uuid4()


def master() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def settings_with(*masters: str) -> Settings:
    return Settings(ai_key_encryption_keys=list(masters))


def test_a_key_decrypts_for_the_workspace_and_provider_it_was_stored_for():
    s = settings_with(master())
    blob = keys.encrypt(s, WS, "openai", "sk-live-123")
    assert keys.decrypt(s, WS, "openai", blob) == "sk-live-123"
    assert b"sk-live-123" not in blob


@pytest.mark.parametrize(
    ("workspace", "provider"),
    [(uuid.uuid4(), "openai"), (WS, "azure")],
    ids=["workspace", "provider"],
)
def test_a_key_copied_to_another_workspace_or_provider_does_not_decrypt(workspace, provider):
    s = settings_with(master())
    blob = keys.encrypt(s, WS, "openai", "sk-live-123")
    with pytest.raises(keys.KeyUnavailable):
        keys.decrypt(s, workspace, provider, blob)


def test_a_tampered_key_does_not_decrypt():
    s = settings_with(master())
    blob = bytearray(keys.encrypt(s, WS, "openai", "sk-live-123"))
    blob[-1] ^= 1
    with pytest.raises(keys.KeyUnavailable):
        keys.decrypt(s, WS, "openai", bytes(blob))


def test_an_older_master_key_still_decrypts_and_the_newest_encrypts():
    old, new = master(), master()
    stored = keys.encrypt(settings_with(old), WS, "openai", "sk-old")
    rotated = settings_with(new, old)
    assert keys.decrypt(rotated, WS, "openai", stored) == "sk-old"
    fresh = keys.encrypt(rotated, WS, "openai", "sk-new")
    assert keys.decrypt(settings_with(new), WS, "openai", fresh) == "sk-new"
    with pytest.raises(keys.KeyUnavailable):
        keys.decrypt(settings_with(new), WS, "openai", stored)  # the old key has been removed


def test_two_encryptions_of_one_key_differ():
    s = settings_with(master())
    assert keys.encrypt(s, WS, "openai", "sk") != keys.encrypt(s, WS, "openai", "sk")
