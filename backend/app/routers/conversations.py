from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import retention, turns
from app.ai.answering import guard
from app.audit import authorize, not_found
from app.auth import Member
from app.db import DB
from app.models import Conversation, Turn
from app.policy import Action, Subject
from app.services.search import MAX_QUERY

router = APIRouter(prefix="/api/workspaces/{workspace_id}/conversations", tags=["conversations"])

TITLE_MAX = 200


class NewConversation(BaseModel):
    title: str


class Question(BaseModel):
    question: str


async def own_conversation(
    db: AsyncSession, subject: Subject, conversation_id: UUID
) -> Conversation:
    """The asker's own unexpired conversation, or the generic not-found for anything else."""
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, *await retention.own_unexpired(db, subject)
        )
    )
    if conversation is None:
        raise not_found()
    return conversation


def _view(conversation: Conversation) -> dict:
    return {
        "id": str(conversation.id),
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat(),
        "last_turn_at": conversation.last_turn_at.isoformat(),
    }


@router.post("", status_code=201)
async def create_conversation(body: NewConversation, subject: Member, db: DB) -> dict:
    await authorize(db, subject, Action.MANAGE_OWN_CONVERSATIONS, detail={"op": "create"})
    title = body.title.strip()
    if not 1 <= len(title) <= TITLE_MAX:
        raise HTTPException(400, f"Conversation titles need 1-{TITLE_MAX} characters.")
    conversation = Conversation(
        workspace_id=subject.workspace_id, user_id=subject.user_id, title=title
    )
    db.add(conversation)
    await db.flush()
    await db.refresh(conversation)
    return _view(conversation)


@router.get("")
async def list_conversations(subject: Member, db: DB) -> dict:
    await authorize(db, subject, Action.MANAGE_OWN_CONVERSATIONS, detail={"op": "list"})
    conversations = await db.scalars(
        select(Conversation)
        .where(*await retention.own_unexpired(db, subject))
        .order_by(Conversation.last_turn_at.desc())
    )
    return {"conversations": [_view(c) for c in conversations]}


@router.get("/{conversation_id}")
async def read_conversation(conversation_id: UUID, subject: Member, db: DB) -> dict:
    """The turns the asker may still read, and how many were left out, never which (R15)."""
    await authorize(
        db,
        subject,
        Action.MANAGE_OWN_CONVERSATIONS,
        detail={"op": "read", "conversation_id": str(conversation_id)},
    )
    conversation = await own_conversation(db, subject, conversation_id)
    memory = await guard(db, subject, conversation_id)
    outcomes = dict(
        (
            await db.execute(
                select(Turn.ordinal, Turn.outcome).where(Turn.conversation_id == conversation_id)
            )
        ).all()
    )
    approvals = await turns.approvals_of(
        db, {p.version_id for t in memory.turns for p in t.passages}
    )
    shown = [
        {
            "ordinal": t.ordinal,
            "question": t.question,
            "sentences": t.sentences,
            "outcome": outcomes[t.ordinal],
            "passages": [turns.passage_view(p, approvals) for p in t.passages],
        }
        for t in memory.turns
    ]
    return _view(conversation) | {
        "turns": shown,
        "left_out": memory.left_out,
        "summary_dropped": memory.summary_dropped,
    }


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: UUID, subject: Member, db: DB) -> None:
    await authorize(
        db,
        subject,
        Action.MANAGE_OWN_CONVERSATIONS,
        detail={"op": "delete", "conversation_id": str(conversation_id)},
    )
    result = await db.execute(
        delete(Conversation).where(
            Conversation.id == conversation_id, *await retention.own_unexpired(db, subject)
        )
    )
    if result.rowcount == 0:
        raise not_found()


@router.post("/{conversation_id}/turns")
async def ask(
    conversation_id: UUID, body: Question, subject: Member, request: Request, db: DB
) -> StreamingResponse:
    """One Ask turn, streamed as server-sent events (KTD5, KTD7)."""
    decision = await authorize(db, subject, Action.ASK, defer_allowed=True)
    question = body.question.strip()
    if not 1 <= len(question) <= MAX_QUERY:
        raise HTTPException(400, f"Questions need 1-{MAX_QUERY} characters.")
    await own_conversation(db, subject, conversation_id)
    state = request.app.state
    started = await turns.start(db, subject, decision, conversation_id, question, state)
    # The request transaction commits as this returns, before the stream starts (KTD5 step 1).
    return StreamingResponse(turns.run(state.sessionmaker, started), media_type="text/event-stream")
