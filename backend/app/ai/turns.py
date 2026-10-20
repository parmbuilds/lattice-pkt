"""Ask turns in three steps, with no transaction held across a provider call (KTD5, KTD7, KTD10).

Step 1 (`start`) runs in the request transaction, which commits as the handler returns its
streaming response, before the stream starts. Steps 2 and 3 (`run`) open their own sessions.
Whoever clears a turn's in-flight mark writes its outcome row, under the conversation's row
lock, so every "sent" row gets exactly one outcome row (R23).
"""

import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import anyio
from fastapi import HTTPException
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai import keys
from app.ai.answering import Memory, Passage, Prompt, build_prompt, guard, prepare, search_passages
from app.ai.citations import check, complete_lines, outcome
from app.ai.provider import ChatProvider, ProviderError, make_provider
from app.audit import not_found, record
from app.auth import load_subject
from app.config import AiModel, Settings
from app.models import (
    AiSettings,
    Conversation,
    ConversationSummary,
    Document,
    DocumentVersion,
    SummarySource,
    Turn,
    TurnPassage,
    User,
    VersionDecision,
)
from app.policy import Action, Decision, Subject, readable_versions
from app.services import ai_settings
from app.services.search import search

AI_OFF = "AI answers are off for this workspace."
IN_PROGRESS = "An answer is already in progress"
UNAVAILABLE = "Answers are unavailable right now."
NOT_COVERED = "Your documents don't cover this"
NOT_SAVED = "This answer was not saved because a document it used is no longer available to you"
CONVERSATION_CHANGED = "This answer was not saved because its conversation changed."
ASK_RESULTS = 10  # the search Ask runs today
TURN_GRACE_SECONDS = 30


def turn_timeout(settings: Settings) -> timedelta:
    """An in-flight mark older than this counts as clear (KTD10)."""
    return timedelta(seconds=settings.ai_read_timeout_seconds + TURN_GRACE_SECONDS)


@dataclass(frozen=True)
class Started:
    """What step 1 settled for steps 2 and 3."""

    subject: Subject  # as authorized: outcome rows are written as this asker even if removed
    decision: Decision
    conversation_id: UUID
    number: int
    question: str
    memory: Memory
    new: list[Passage]
    sent: list[Passage]  # before any condensing; condensing only drops passages
    approvals: dict[UUID, tuple[str, str]]  # version -> (approved_by, approved_at)
    provider: ChatProvider | None  # None when the stored key cannot be decrypted
    provider_id: str
    model: AiModel
    encrypted_key: bytes


async def finish(
    db: AsyncSession,
    subject: Subject,
    decision: Decision,
    conversation_id: UUID,
    number: int,
    result: str,
) -> None:
    """Write a turn's outcome row and clear its mark. Callers hold the conversation row lock and
    have seen the mark is this turn's, or that the conversation is gone."""
    await record(
        db,
        subject,
        decision,
        detail={"conversation_id": str(conversation_id), "turn": number, "outcome": result},
    )
    await db.execute(
        update(Conversation).where(Conversation.id == conversation_id).values(in_flight_at=None)
    )


async def start(
    db: AsyncSession,
    subject: Subject,
    decision: Decision,
    conversation_id: UUID,
    question: str,
    state,
) -> Started:
    """Step 1: claim the conversation, guard its memory, search, and write the "sent" row."""
    settings: Settings = state.settings
    row = await ai_settings.current(db, subject.workspace_id)
    entry = settings.ai_provider(row.provider_id) if row and row.provider_id else None
    model = entry.model(row.model) if entry else None
    if model is None:  # R6, R31
        raise HTTPException(409, AI_OFF)

    # Lock order: conversations before turns, as purge_document takes them (KTD10).
    mark = (
        await db.execute(
            select(
                Conversation.in_flight_turn,
                Conversation.in_flight_at,
                Conversation.in_flight_at > func.now() - turn_timeout(settings),
            )
            .where(Conversation.id == conversation_id)
            .with_for_update()
        )
    ).one_or_none()
    if mark is None:  # deleted since it was loaded
        raise not_found()
    last, since, fresh = mark
    if fresh:
        raise HTTPException(409, IN_PROGRESS)
    if since is not None:  # a stale mark: this turn clears it, so it records the outcome
        await finish(db, subject, decision, conversation_id, last, "abandoned")
    saved = await db.scalar(
        select(func.max(Turn.ordinal)).where(Turn.conversation_id == conversation_id)
    )
    # in_flight_turn stays as the last number claimed, so numbers are never reused and an
    # older turn never mistakes a newer turn's mark for its own.
    number = max(saved or 0, last or 0) + 1

    memory = await guard(db, subject, conversation_id)
    cap = min(ASK_RESULTS, settings.search_limit_cap)
    results = await search(db, subject, question, cap, state.embedder, settings)
    new = await search_passages(db, results)
    sent = build_prompt(memory, new, question)[1]
    approvals = await approvals_of(db, {p.version_id for p in sent})
    try:
        api_key = keys.decrypt(settings, subject.workspace_id, entry.id, row.encrypted_key)
        provider = make_provider(entry, model.id, api_key, state.ai_client)
    except keys.KeyUnavailable:
        provider = None  # every cause gets the one notice (R13)

    # Everything a condensing call or the answer may send, committed before either (R23).
    versions = {p.version_id for p in sent} | {v for _, v in memory.summary_sources}
    await record(
        db,
        subject,
        decision,
        returned_version_ids=sorted(versions),
        detail={
            "conversation_id": str(conversation_id),
            "turn": number,
            "query": question,
            "count": len(results),
            "provider_id": entry.id,
            "model": model.id,
        },
    )
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(in_flight_turn=number, in_flight_at=func.now())
    )
    return Started(
        subject=subject,
        decision=decision,
        conversation_id=conversation_id,
        number=number,
        question=question,
        memory=memory,
        new=new,
        sent=sent,
        approvals=approvals,
        provider=provider,
        provider_id=entry.id,
        model=model,
        encrypted_key=row.encrypted_key,
    )


async def approvals_of(db: AsyncSession, versions: set[UUID]) -> dict[UUID, tuple[str, str]]:
    """Who approved each version and when, as Ask shows beside a passage (R11)."""
    return {
        version: (name, at.isoformat())
        for version, name, at in await db.execute(
            select(VersionDecision.version_id, User.display_name, VersionDecision.created_at)
            .join(User, User.id == VersionDecision.actor_id)
            .where(VersionDecision.version_id.in_(versions), VersionDecision.decision == "approved")
        )
    }


def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


def _passage(n: int, p: Passage, approvals: dict[UUID, tuple[str, str]]) -> dict:
    """A sent passage as Ask shows it, with its document and approval (R11)."""
    approved_by, approved_at = approvals.get(p.version_id, (None, None))
    return {
        "id": f"P{n}",
        "document_id": str(p.document_id),
        "version_id": str(p.version_id),
        "title": p.title,
        "start": p.start,
        "end": p.end,
        "text": p.text,
        "approved_by": approved_by,
        "approved_at": approved_at,
    }


def _passages(t: Started, sent: list[Passage]) -> str:
    return _event(
        "passages",
        {
            "passages": [_passage(n, p, t.approvals) for n, p in enumerate(sent, start=1)],
            # The guard reset these, so this is the asker's one chance to see them (R15, R17).
            "left_out": t.memory.left_out,
            "summary_dropped": t.memory.summary_dropped,
        },
    )


async def _lines(chunks: AsyncIterator[str]) -> AsyncIterator[str]:
    async with aclosing(chunks):
        rest = ""
        async for chunk in chunks:
            lines, rest = complete_lines(rest + chunk)
            for line in lines:
                yield line
        if rest.strip():
            yield rest.strip()


async def _unreadable(db: AsyncSession, subject: Subject, versions: set[UUID]) -> bool:
    """Whether any version is no longer readable to the subject: the guard's read rule (KTD3)."""
    query = readable_versions(subject, Action.RETRIEVE).where(
        DocumentVersion.id.in_(sorted(versions))
    )
    return set(await db.scalars(query)) != versions


async def _lost(sessionmaker: async_sessionmaker, t: Started, versions: set[UUID]) -> str | None:
    """Step 2's check before a sentence is released: the outcome that ends the turn, if any."""
    async with sessionmaker() as db:
        gone = await db.scalar(select(Conversation.id).where(Conversation.id == t.conversation_id))
        if gone is None:
            return "conversation_changed"
        subject = await load_subject(db, t.subject.user_id, t.subject.workspace_id)
        return "discarded" if await _unreadable(db, subject, versions) else None


async def _lock_mark(db: AsyncSession, t: Started) -> str:
    """Lock the conversation row: "gone", "ours", or "taken" when a newer turn cleared the mark
    and so recorded this turn's outcome already."""
    mark = (
        await db.execute(
            select(Conversation.in_flight_turn, Conversation.in_flight_at)
            .where(Conversation.id == t.conversation_id)
            .with_for_update()
        )
    ).one_or_none()
    if mark is None:
        return "gone"
    turn, since = mark
    return "ours" if turn == t.number and since is not None else "taken"


async def _end(
    sessionmaker: async_sessionmaker, t: Started, result: str, rejected: bool = False
) -> None:
    """Record an unsaved turn's outcome in its own transaction (KTD5 steps 4 and 5)."""
    async with sessionmaker.begin() as db:
        if rejected:  # R30; only the key that was used, not one saved since (KTD9)
            await db.execute(
                update(AiSettings)
                .where(
                    AiSettings.workspace_id == t.subject.workspace_id,
                    AiSettings.encrypted_key == t.encrypted_key,
                )
                .values(key_status="rejected")
            )
        mark = await _lock_mark(db, t)
        if mark != "taken":
            result = "conversation_changed" if mark == "gone" else result
            await finish(db, t.subject, t.decision, t.conversation_id, t.number, result)


def _discarded(result: str) -> str:
    notice = CONVERSATION_CHANGED if result == "conversation_changed" else NOT_SAVED
    return _event("discarded", {"notice": notice})


async def _save(
    sessionmaker: async_sessionmaker, t: Started, prompt: Prompt | None, checked: list[dict]
) -> str:
    """Step 3: re-check under FOR SHARE on every cited document, then save the turn, any new
    summary and the outcome together. A purge that starts afterwards waits, then deletes it."""
    sent = prompt.sent if prompt else []
    summary = prompt.memory if prompt and prompt.condensed else None
    sources = set(summary.summary_sources) if summary else set()
    cited = {(p.document_id, p.version_id) for p in sent}
    async with sessionmaker.begin() as db:
        # Lock order: documents, then conversations, then turns (KTD10), documents by id.
        await db.execute(
            select(Document.id)
            .where(Document.id.in_({d for d, _ in cited | sources}))
            .order_by(Document.id)
            .with_for_update(read=True)
        )
        mark = await _lock_mark(db, t)
        if mark == "taken":
            return _discarded("conversation_changed")
        subject = await load_subject(db, t.subject.user_id, t.subject.workspace_id)
        if mark == "gone" or await _unreadable(db, subject, {v for _, v in cited}):
            result = "conversation_changed" if mark == "gone" else "discarded"
            await finish(db, t.subject, t.decision, t.conversation_id, t.number, result)
            return _discarded(result)

        result = outcome(checked)
        turn_id = await db.scalar(
            insert(Turn)
            .values(
                workspace_id=t.subject.workspace_id,
                conversation_id=t.conversation_id,
                ordinal=t.number,
                question=t.question,
                sentences=checked,
                outcome=result,
                provider_id=t.provider_id,
                model=t.model.id,
            )
            .returning(Turn.id)
        )
        if sent:
            await db.execute(
                insert(TurnPassage),
                [
                    {
                        "turn_id": turn_id,
                        "ordinal": n,
                        "workspace_id": t.subject.workspace_id,
                        "document_id": p.document_id,
                        "version_id": p.version_id,
                        "start": p.start,
                        "end": p.end,
                        "sha256": p.sha256,
                    }
                    for n, p in enumerate(sent, start=1)
                ],
            )
        # A summary is kept only if every version it drew on is still readable (KTD12).
        if summary and not await _unreadable(db, subject, {v for _, v in sources}):
            summary_id = await db.scalar(
                insert(ConversationSummary)
                .values(
                    workspace_id=t.subject.workspace_id,
                    conversation_id=t.conversation_id,
                    text=summary.summary,
                    covers_through=summary.covers_through,
                    model=t.model.id,
                )
                .returning(ConversationSummary.id)
            )
            await db.execute(
                insert(SummarySource),
                [
                    {
                        "summary_id": summary_id,
                        "workspace_id": t.subject.workspace_id,
                        "document_id": d,
                        "version_id": v,
                    }
                    for d, v in sorted(sources)
                ],
            )
        await db.execute(
            update(Conversation)
            .where(Conversation.id == t.conversation_id)
            .values(last_turn_at=func.now())
        )
        await finish(db, t.subject, t.decision, t.conversation_id, t.number, result)
    if result == "not_covered":  # R10, R26: replaces any streamed sentences
        return _event("not_covered", {"notice": NOT_COVERED, "turn": t.number})
    return _event("done", {"outcome": result, "turn": t.number})


async def run(sessionmaker: async_sessionmaker, t: Started) -> AsyncIterator[str]:
    """Steps 2 and 3 as server-sent events: passages, checked sentences, then one of done,
    not_covered, unavailable or discarded (KTD7)."""
    ended = False
    try:
        prompt, failure, lost, checked = None, None, None, []
        try:
            if t.provider is None:
                raise ProviderError("failed", "key unavailable")
            prompt = await prepare(t.provider, t.memory, t.new, t.question, t.model)
        except ProviderError as error:
            failure = error
        sent = prompt.sent if prompt else t.sent
        yield _passages(t, sent)

        if prompt and not failure:
            ids = {f"P{n}": p.text for n, p in enumerate(sent, start=1)}
            versions = {p.version_id for p in sent}
            chunks = t.provider.stream(prompt.messages, max_tokens=t.model.max_output_tokens)
            try:
                async with aclosing(_lines(chunks)) as lines:
                    async for line in lines:
                        sentence = check(line, ids)
                        if lost := await _lost(sessionmaker, t, versions):
                            break  # R29: closing the stream cancels the provider call
                        checked.append(sentence)
                        yield _event("sentence", sentence)
            except ProviderError as error:
                failure = error

        # Shielded: once step 3 starts, a disconnect cannot leave it half-recorded.
        with anyio.CancelScope(shield=True):
            if failure:
                await _end(sessionmaker, t, "provider_failed", failure.kind == "key_rejected")
                final = _event("unavailable", {"notice": UNAVAILABLE})
            elif lost:
                await _end(sessionmaker, t, lost)
                final = _discarded(lost)
            else:
                try:
                    final = await _save(sessionmaker, t, prompt, checked)
                except Exception:
                    await _end(sessionmaker, t, "discarded")
                    final = _discarded("discarded")
            ended = True
        yield final
    finally:
        if not ended:  # a disconnect, Stop, or an error before step 3 (KTD5 step 5)
            with anyio.CancelScope(shield=True):
                await _end(sessionmaker, t, "abandoned")
