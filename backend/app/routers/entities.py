from dataclasses import replace
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.audit import authorize, not_found, record
from app.auth import Member
from app.db import DB, CommitThenRaise
from app.models import Entity, EntityAlias
from app.policy import Action, decide
from app.services.graph import mentions

router = APIRouter(prefix="/api/workspaces/{workspace_id}/entities", tags=["entities"])


class EntityBody(BaseModel):
    name: str
    aliases: list[str] = []


class EntityPatch(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None


def _terms(request: Request, name: str | None, aliases: list[str] | None) -> None:
    """Enforce the KTD14 caps on a write."""
    settings = request.app.state.settings
    if aliases is not None and len(aliases) > settings.graph_max_aliases:
        raise HTTPException(422, f"At most {settings.graph_max_aliases} aliases.")
    for term in ([name] if name is not None else []) + (aliases or []):
        if not term.strip() or len(term.strip()) > settings.graph_max_alias_chars:
            raise HTTPException(
                422, f"Names and aliases are 1-{settings.graph_max_alias_chars} characters."
            )


async def _registry(db, subject) -> tuple[list[Entity], dict[UUID, list[str]]]:
    entities = list(
        await db.scalars(
            select(Entity).where(Entity.workspace_id == subject.workspace_id).order_by(Entity.name)
        )
    )
    aliases: dict[UUID, list[str]] = {}
    for eid, alias in await db.execute(
        select(EntityAlias.entity_id, EntityAlias.alias)
        .where(EntityAlias.workspace_id == subject.workspace_id)
        .order_by(EntityAlias.alias)
    ):
        aliases.setdefault(eid, []).append(alias)
    return entities, aliases


def _view(entity: Entity, aliases: list[str], found: list[dict], manager: bool) -> dict:
    """Members see only the aliases with a readable mention (KTD14)."""
    if not manager:
        seen = {m["term"] for m in found if m["entity_id"] == entity.id and m["alias"]}
        aliases = [a for a in aliases if a in seen]
    return {"id": str(entity.id), "name": entity.name, "aliases": aliases}


@router.get("")
async def list_entities(subject: Member, db: DB) -> dict:
    """Managers see the whole registry; members see entities with a readable mention (R22)."""
    await authorize(db, subject, Action.READ_GRAPH)
    manager = decide(subject, Action.MANAGE_ENTITIES).allowed
    entities, aliases = await _registry(db, subject)
    found = await mentions(db, subject)
    visible = {m["entity_id"] for m in found}
    return {
        "entities": [
            _view(e, aliases.get(e.id, []), found, manager)
            for e in entities
            if manager or e.id in visible
        ]
    }


@router.get("/{entity_id}")
async def get_entity(entity_id: UUID, subject: Member, db: DB) -> dict:
    """Readable mentions grouped by version, with spans, and the entities co-mentioned there."""
    decision = await authorize(
        db, subject, Action.READ_GRAPH, detail={"entity_id": str(entity_id)}, defer_allowed=True
    )
    manager = decide(subject, Action.MANAGE_ENTITIES).allowed
    entities, aliases = await _registry(db, subject)
    entity = next((e for e in entities if e.id == entity_id), None)
    found = await mentions(db, subject)
    own = [m for m in found if m["entity_id"] == entity_id]
    if entity is None or not (manager or own):
        # No readable evidence: indistinguishable from a missing entity (R29).
        denied = replace(decision, allowed=False, rule="read_graph.no_readable_mention")
        await record(db, subject, denied, detail={"entity_id": str(entity_id)})
        raise CommitThenRaise(not_found())
    versions: dict[UUID, dict] = {}
    for m in own:
        entry = versions.setdefault(
            m["version_id"],
            {
                "version_id": str(m["version_id"]),
                "document_id": str(m["document_id"]),
                "title": m["title"],
                "spans": [],
            },
        )
        entry["spans"].append(
            {"start": m["start"], "end": m["end"], "term": m["term"], "snippet": m["snippet"]}
        )
    co_ids = {m["entity_id"] for m in found if m["version_id"] in versions} - {entity_id}
    await record(
        db,
        subject,
        decision,
        returned_version_ids=list(versions),
        detail={
            "entity_id": str(entity_id),
            "returned_spans": [
                [v["version_id"], s["start"], s["end"]]
                for v in versions.values()
                for s in v["spans"]
            ],
        },
    )
    return {
        **_view(entity, aliases.get(entity.id, []), found, manager),
        "mentions": list(versions.values()),
        "co_mentions": [{"id": str(e.id), "name": e.name} for e in entities if e.id in co_ids],
    }


@router.post("", status_code=201)
async def create_entity(body: EntityBody, subject: Member, db: DB, request: Request) -> dict:
    await authorize(db, subject, Action.MANAGE_ENTITIES, detail={"op": "create"})
    _terms(request, body.name, body.aliases)
    cap = request.app.state.settings.graph_max_entities
    count = await db.scalar(
        select(func.count()).select_from(Entity).where(Entity.workspace_id == subject.workspace_id)
    )
    if count >= cap:
        raise HTTPException(422, f"A workspace holds at most {cap} entities.")
    entity = Entity(workspace_id=subject.workspace_id, name=body.name.strip())
    aliases = [a.strip() for a in body.aliases]
    try:
        async with db.begin_nested():
            db.add(entity)
            await db.flush()
            db.add_all(
                EntityAlias(workspace_id=subject.workspace_id, entity_id=entity.id, alias=a)
                for a in aliases
            )
    except IntegrityError:
        raise HTTPException(409, "That name or alias is already in use.") from None
    return {"id": str(entity.id), "name": entity.name, "aliases": sorted(aliases)}


@router.patch("/{entity_id}")
async def update_entity(
    entity_id: UUID, body: EntityPatch, subject: Member, db: DB, request: Request
) -> dict:
    await authorize(
        db, subject, Action.MANAGE_ENTITIES, detail={"op": "update", "entity_id": str(entity_id)}
    )
    _terms(request, body.name, body.aliases)
    entity = await _entity(db, subject, entity_id)
    try:
        async with db.begin_nested():
            if body.name is not None:
                entity.name = body.name.strip()
            if body.aliases is not None:  # the alias list is replaced as a whole
                await db.execute(delete(EntityAlias).where(EntityAlias.entity_id == entity.id))
                db.add_all(
                    EntityAlias(
                        workspace_id=subject.workspace_id, entity_id=entity.id, alias=a.strip()
                    )
                    for a in body.aliases
                )
    except IntegrityError:
        raise HTTPException(409, "That name or alias is already in use.") from None
    aliases = await db.scalars(
        select(EntityAlias.alias).where(EntityAlias.entity_id == entity.id).order_by("alias")
    )
    return {"id": str(entity.id), "name": entity.name, "aliases": list(aliases)}


@router.delete("/{entity_id}", status_code=204)
async def delete_entity(entity_id: UUID, subject: Member, db: DB) -> None:
    await authorize(
        db, subject, Action.MANAGE_ENTITIES, detail={"op": "delete", "entity_id": str(entity_id)}
    )
    await db.delete(await _entity(db, subject, entity_id))  # aliases cascade


async def _entity(db, subject, entity_id: UUID) -> Entity:
    entity = await db.scalar(
        select(Entity).where(Entity.id == entity_id, Entity.workspace_id == subject.workspace_id)
    )
    if entity is None:
        raise not_found()
    return entity
