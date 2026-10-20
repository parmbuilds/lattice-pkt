from datetime import timedelta
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_PEPPER = "dev-pepper-change-me"
# Published development master key for AI provider keys; refused like DEV_PEPPER (KTD15).
DEV_AI_KEY = "bGF0dGljZS1kZXYtYWkta2V5LW5vdC1zZWNyZXQhISE="


class AiModel(BaseModel):
    """A model the operator offers on a provider, with the limits Lattice budgets against."""

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    context_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)


class AiProvider(BaseModel):
    """One operator-approved OpenAI-compatible provider (R1); owners never enter a URL (R3)."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    label: str = Field(min_length=1)
    kind: Literal["openai", "fake"] = "openai"
    base_url: str = ""
    # A closed set, so the key can only ever travel in one of these headers (KTD9).
    auth: Literal["bearer", "api-key"] = "bearer"
    models: list[AiModel] = Field(min_length=1)

    @model_validator(mode="after")
    def _https_unless_fake(self) -> "AiProvider":
        if self.kind == "openai" and not self.base_url.startswith("https://"):
            raise ValueError(f"provider {self.id}: base_url must use https")
        return self

    def model(self, model_id: str) -> AiModel | None:
        return next((m for m in self.models if m.id == model_id), None)


class Settings(BaseSettings):
    """Runtime configuration, read from LATTICE_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="LATTICE_", env_file=".env", extra="ignore")

    # Runtime role (lattice_app): never owns tables (KTD8).
    database_url: str = "postgresql+psycopg://lattice_app:lattice_app_dev@localhost:5432/lattice"
    # Owner role (lattice_owner): migrations only.
    migration_database_url: str = (
        "postgresql+psycopg://lattice_owner:lattice_owner_dev@localhost:5432/lattice"
    )

    # Server-side secret for HMAC verifiers of sessions, tokens and invites (KTD7).
    secret_pepper: str = Field(default=DEV_PEPPER, min_length=16)

    # The single origin the browser UI and API are served from (KTD12, KTD13).
    public_origin: str = "http://localhost:8000"
    # Development opt-out for the Secure cookie flag; Secure is on by default (KTD13).
    insecure_dev_cookies: bool = False

    session_idle_minutes: int = 12 * 60
    session_absolute_days: int = 7
    token_days: int = 90
    member_invite_days: int = 7
    admin_invite_hours: int = 24

    search_limit_cap: int = 20

    # Embeddings (KTD1): "fake" is the deterministic test embedder.
    embedder: Literal["fastembed", "fake"] = "fastembed"
    # Unset means fastembed's own default, which honours FASTEMBED_CACHE_PATH.
    embedding_cache_path: str | None = None
    embed_batch_size: int = Field(default=32, ge=1)
    embedder_threads: int = Field(default=2, ge=1)

    # Derivation (KTD3): inline mode is for tests and the seed only.
    derive_inline: bool = False
    derive_poll_seconds: float = 2.0
    derive_lease_seconds: int = 300

    # Upload and extraction limits (KTD11, KTD12).
    upload_max_bytes: int = 20 * 1024 * 1024
    upload_max_inflight: int = 4
    extract_timeout_seconds: float = 30.0
    extract_memory_bytes: int = 1024 * 1024 * 1024
    extract_concurrency: int = 2
    extract_max_pdf_pages: int = 500
    extract_max_zip_members: int = 1000
    extract_max_zip_member_bytes: int = 50 * 1024 * 1024
    extract_max_zip_total_bytes: int = 50 * 1024 * 1024
    extract_max_zip_ratio: int = 100

    # Retrieval (KTD5-KTD7); the distance floor is tuned by the real-model benchmark (R24).
    # Chosen by the real-model benchmark (R24): paraphrases 0.30-0.37, off-topic 0.52-0.56.
    vector_distance_floor: float = 0.45
    vector_candidates: int = 50
    chunk_max_chars: int = 1200

    # Entity registry caps (KTD14).
    graph_max_entities: int = 200
    graph_max_aliases: int = 20
    graph_max_alias_chars: int = 100

    # Directory of the built UI, served same-origin (KTD12); unset in tests and development.
    static_dir: str | None = None

    # AI answers: the operator's provider list, and the master keys that encrypt workspace
    # keys (KTD1, KTD2). Both are JSON arrays in the environment or .env; newest key first.
    ai_providers: list[AiProvider] = Field(default_factory=list)
    ai_key_encryption_keys: list[SecretStr] = Field(default_factory=list)
    # Provider calls (KTD9): connect also bounds writes and pool waits; read bounds each chunk.
    ai_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    ai_read_timeout_seconds: float = Field(default=30.0, gt=0)

    @field_validator("ai_providers")
    @classmethod
    def _unique_provider_ids(cls, providers: list[AiProvider]) -> list[AiProvider]:
        ids = [p.id for p in providers]
        if len(ids) != len(set(ids)):
            raise ValueError("provider ids must be unique")
        return providers

    def ai_provider(self, provider_id: str) -> AiProvider | None:
        return next((p for p in self.ai_providers if p.id == provider_id), None)

    @property
    def session_idle(self) -> timedelta:
        return timedelta(minutes=self.session_idle_minutes)

    @property
    def session_absolute(self) -> timedelta:
        return timedelta(days=self.session_absolute_days)

    def invite_lifetime(self, role: str) -> timedelta:
        # KTD7: admin invites carry more power, so they expire sooner.
        return {
            "admin": timedelta(hours=self.admin_invite_hours),
            "member": timedelta(days=self.member_invite_days),
        }[role]


@lru_cache
def get_settings() -> Settings:
    return Settings()
