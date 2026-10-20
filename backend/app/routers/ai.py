from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.audit import authorize, record
from app.auth import Member
from app.db import DB
from app.models import AiSettings
from app.policy import Action, decide
from app.services import ai_settings as svc

router = APIRouter(tags=["ai"])

SETTINGS_PATH = "/api/workspaces/{workspace_id}/ai/settings"


@router.get(SETTINGS_PATH)
async def get_ai_settings(subject: Member, request: Request, db: DB) -> dict:
    await authorize(db, subject, Action.VIEW_AI_SETTINGS)
    row = await svc.current(db, subject.workspace_id)
    manager = decide(subject, Action.MANAGE_AI_SETTINGS).allowed
    return svc.view(request.app.state.settings, row, manager=manager)


@router.put(SETTINGS_PATH)
async def save_ai_settings(subject: Member, request: Request, db: DB):
    decision = await authorize(db, subject, Action.MANAGE_AI_SETTINGS, defer_allowed=True)
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(422, "Send the settings as a JSON object.") from None
    wanted = svc.parse_update(payload)
    settings, workspace_id = request.app.state.settings, subject.workspace_id
    entry = svc.chosen(settings, wanted)
    # Held through the key check, so concurrent saves cannot mix one's key with another's provider.
    row = await svc.current(db, workspace_id, lock=True)

    cutoff = None
    retention = row.retention_days if row else svc.DEFAULT_RETENTION_DAYS
    if wanted.retention_days < retention:  # a shorter period applies to existing ones (R20)
        cutoff, count = await svc.expiring(db, workspace_id, wanted.retention_days)
        if count and wanted.confirm_deletions != count:
            return JSONResponse(
                {
                    "detail": f"This deletes {count} saved conversations. Confirm to continue.",
                    "conversations_to_delete": count,
                },
                status_code=409,
            )

    api_key = svc.key_to_check(settings, workspace_id, row, wanted)
    if api_key is not None:
        await svc.check_key(entry, wanted.model, api_key, request.app.state.ai_client)
    detail = {
        "op": "save",
        "changed": svc.changes(row, wanted),
        "key_replaced": wanted.api_key is not None,
    }
    await svc.save(db, settings, workspace_id, wanted, api_key)
    if cutoff is not None:
        detail["conversations_deleted"] = await svc.delete_expired(db, workspace_id, cutoff)
    await record(db, subject, decision, detail=detail)
    saved = await db.get(AiSettings, workspace_id, populate_existing=True)
    return svc.view(settings, saved, manager=True)


@router.delete(SETTINGS_PATH, status_code=204)
async def disconnect_ai(subject: Member, db: DB) -> None:
    row = await svc.current(db, subject.workspace_id)
    await authorize(
        db,
        subject,
        Action.MANAGE_AI_SETTINGS,
        detail={"op": "disconnect", "provider_id": row.provider_id if row else None},
    )
    await svc.disconnect(db, subject.workspace_id)
