import base64
import os

import pytest
from pydantic import ValidationError

from app.config import DEV_AI_KEY, DEV_PEPPER, Settings
from app.main import create_app

PEPPER = "test-pepper-0123456789"
PROVIDER = {
    "id": "openai",
    "label": "OpenAI",
    "base_url": "https://api.openai.com/v1",
    "models": [
        {"id": "gpt-x", "label": "GPT X", "context_tokens": 128000, "max_output_tokens": 800}
    ],
}


def test_the_public_default_pepper_is_refused_outside_local_development():
    with pytest.raises(RuntimeError, match="LATTICE_SECRET_PEPPER"):
        create_app(Settings(secret_pepper=DEV_PEPPER, insecure_dev_cookies=False))
    create_app(Settings(secret_pepper=DEV_PEPPER, insecure_dev_cookies=True))


def test_listed_providers_need_a_master_key_and_no_providers_need_none():
    with pytest.raises(RuntimeError, match="LATTICE_AI_KEY_ENCRYPTION_KEYS"):
        create_app(Settings(secret_pepper=PEPPER, ai_providers=[PROVIDER]))
    create_app(Settings(secret_pepper=PEPPER))


def test_the_published_development_master_key_is_refused_outside_local_development():
    dev = {"ai_providers": [PROVIDER], "ai_key_encryption_keys": [DEV_AI_KEY]}
    with pytest.raises(RuntimeError, match="LATTICE_AI_KEY_ENCRYPTION_KEYS"):
        create_app(Settings(secret_pepper=PEPPER, insecure_dev_cookies=False, **dev))
    create_app(Settings(secret_pepper=PEPPER, insecure_dev_cookies=True, **dev))


def test_a_master_key_that_is_not_32_bytes_is_refused():
    short = base64.b64encode(os.urandom(16)).decode()
    with pytest.raises(RuntimeError, match="32 bytes"):
        create_app(
            Settings(secret_pepper=PEPPER, ai_providers=[PROVIDER], ai_key_encryption_keys=[short])
        )


def test_a_provider_over_plain_http_is_refused_unless_it_is_the_fake():
    with pytest.raises(ValidationError, match="https"):
        Settings(ai_providers=[{**PROVIDER, "base_url": "http://api.example.com/v1"}])
    Settings(ai_providers=[{**PROVIDER, "id": "fake", "kind": "fake", "base_url": ""}])


def test_duplicate_provider_ids_are_refused():
    with pytest.raises(ValidationError, match="unique"):
        Settings(ai_providers=[PROVIDER, PROVIDER])


def test_settings_never_print_the_master_keys():
    key = base64.b64encode(os.urandom(32)).decode()
    assert key not in repr(Settings(ai_key_encryption_keys=[key]))
