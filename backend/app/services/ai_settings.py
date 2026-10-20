"""A workspace's AI provider, model, write-only key and retention (R2, R4, R20, R30).

Authorization happens in the router via app.audit. The plain key exists only inside a save:
it is checked, encrypted and dropped, never returned, logged or audited.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import keys
from app.ai.provider import ProviderError, make_provider
from app.config import AiProvider, Settings
from app.models import AiSettings, Conversation

DEFAULT_RETENTION_DAYS = 30  # the column's default, for a workspace with no settings row yet
# The key check sends no workspace text (KTD9).
KEY_CHECK = [{"role": "user", "content": "Reply with the word OK."}]
KEY_CHECK_TOKENS = 16


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    provider_id: str
    model: str
    retention_days: int = Field(ge=1, le=365)
    api_key: str | None = Field(default=None, min_length=1, max_length=1000)
    confirm_deletions: int | None = None


def parse_update(payload: object) -> SettingsUpdate:
    """Validated here, not as a request schema: FastAPI's 422 echoes input (KTD14)."""
    try:
        return SettingsUpdate.model_validate(payload)
    except ValidationError as error:
        raise HTTPException(422, error.errors(include_url=False, include_input=False)) from None


async def current(db: AsyncSession, workspace_id: UUID, *, lock: bool = False) -> AiSettings | None:
    return await db.get(AiSettings, workspace_id, with_for_update=lock)


def view(settings: Settings, row: AiSettings | None, *, manager: bool) -> dict:
    """What a member sees; managers also see whether the provider rejected the key (R30)."""
    enabled = row is not None and row.provider_id is not None
    shown = {
        "providers": [
            {
                "id": p.id,
                "label": p.label,
                "models": [{"id": m.id, "label": m.label} for m in p.models],
            }
            for p in settings.ai_providers
        ],
        "enabled": enabled,
        "provider_id": row.provider_id if row else None,
        "model": row.model if row else None,
        "retention_days": row.retention_days if row else DEFAULT_RETENTION_DAYS,
    }
    if manager:
        shown["key_status"] = row.key_status if enabled else None
    return shown


def chosen(settings: Settings, wanted: SettingsUpdate) -> AiProvider:
    entry = settings.ai_provider(wanted.provider_id)
    if entry is None or entry.model(wanted.model) is None:
        raise HTTPException(400, "Choose a provider and model from the list.")
    return entry


def _expired(workspace_id: UUID, cutoff: datetime):
    return Conversation.workspace_id == workspace_id, Conversation.last_turn_at < cutoff


async def expiring(db: AsyncSession, workspace_id: UUID, days: int) -> tuple[datetime, int]:
    """The cutoff a retention of `days` sets now, and how many conversations it deletes."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    count = await db.scalar(select(func.count()).where(*_expired(workspace_id, cutoff)))
    return cutoff, count


async def delete_expired(db: AsyncSession, workspace_id: UUID, cutoff: datetime) -> int:
    # Turns, passages and summaries go with their conversation (FK cascade).
    result = await db.execute(delete(Conversation).where(*_expired(workspace_id, cutoff)))
    return result.rowcount


def key_to_check(
    settings: Settings, workspace_id: UUID, row: AiSettings | None, wanted: SettingsUpdate
) -> str | None:
    """The key a save must check with the provider, or None when nothing needs checking."""
    if wanted.api_key is not None:
        return wanted.api_key
    if row is None or row.provider_id != wanted.provider_id:
        # The key is bound to its provider (KTD2), so a new provider needs its own key.
        raise HTTPException(400, "Enter the API key for this provider.")
    if row.model == wanted.model:
        return None  # a retention-only save calls no provider (KTD9)
    try:
        return keys.decrypt(settings, workspace_id, row.provider_id, row.encrypted_key)
    except keys.KeyUnavailable:
        raise HTTPException(400, "Enter the API key again.") from None


async def check_key(entry: AiProvider, model: str, api_key: str, client: httpx.AsyncClient) -> None:
    provider = make_provider(entry, model, api_key, client)
    try:
        await provider.complete(KEY_CHECK, max_tokens=KEY_CHECK_TOKENS)
    except ProviderError as error:
        if error.kind == "key_rejected":
            raise HTTPException(400, "The provider rejected this API key.") from None
        raise HTTPException(502, "The provider did not answer the key check. Try again.") from None


async def save(
    db: AsyncSession,
    settings: Settings,
    workspace_id: UUID,
    wanted: SettingsUpdate,
    checked_key: str | None,
) -> None:
    values = {
        "provider_id": wanted.provider_id,
        "model": wanted.model,
        "retention_days": wanted.retention_days,
        "updated_at": func.now(),
    }
    if checked_key is not None:  # it just passed the check, so any earlier rejection is over
        values["key_status"] = "ok"
    if wanted.api_key is None:  # key_to_check made sure the row exists with this provider
        await db.execute(
            update(AiSettings).where(AiSettings.workspace_id == workspace_id).values(**values)
        )
        return
    values["encrypted_key"] = keys.encrypt(
        settings, workspace_id, wanted.provider_id, wanted.api_key
    )
    await db.execute(
        insert(AiSettings)
        .values(workspace_id=workspace_id, **values)
        .on_conflict_do_update(index_elements=[AiSettings.workspace_id], set_=values)
    )


async def disconnect(db: AsyncSession, workspace_id: UUID) -> None:
    """Remove the provider and its key; retention and saved conversations stay (R31)."""
    await db.execute(
        update(AiSettings)
        .where(AiSettings.workspace_id == workspace_id)
        .values(
            provider_id=None, model=None, encrypted_key=None, key_status="ok", updated_at=func.now()
        )
    )


def changes(row: AiSettings | None, wanted: SettingsUpdate) -> dict[str, list]:
    """Field -> [old, new] for the audit row; never the key (R5)."""
    old = {
        "provider_id": row.provider_id if row else None,
        "model": row.model if row else None,
        "retention_days": row.retention_days if row else DEFAULT_RETENTION_DAYS,
    }
    new = wanted.model_dump(include=set(old))
    return {field: [old[field], new[field]] for field in old if old[field] != new[field]}
