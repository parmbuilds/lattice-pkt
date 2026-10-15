from datetime import timedelta
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_PEPPER = "dev-pepper-change-me"


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

    # Directory of the built UI, served same-origin (KTD12); unset in tests and development.
    static_dir: str | None = None

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
