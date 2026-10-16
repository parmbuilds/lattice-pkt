"""Query-time entity graph (R22, KTD14): only the registry is stored; mentions are matched live."""

from sqlalchemy import func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentVersion, Entity, EntityAlias
from app.policy import Action, Subject, read_clause

CONTEXT_CHARS = 60


async def mentions(db: AsyncSession, subject: Subject) -> list[dict]:
    """Every match of an entity name or alias in a version the subject may retrieve.

    One statement: the read policy filters versions before any body is matched (KTD14).
    shortcut: scans the readable corpus per request; store mentions per version if it gets slow.
    """
    d, v = Document.__table__, DocumentVersion.__table__
    e, a = Entity.__table__, EntityAlias.__table__
    terms = union_all(
        select(
            e.c.id.label("entity_id"), e.c.name.label("term"), literal(False).label("alias")
        ).where(e.c.workspace_id == subject.workspace_id),
        select(a.c.entity_id, a.c.alias, literal(True)).where(
            a.c.workspace_id == subject.workspace_id
        ),
    ).subquery("terms")
    # The term as a literal: every punctuation character is backslash-escaped, then fenced by
    # word boundaries that also hold where the term itself starts or ends with punctuation.
    escaped = func.regexp_replace(terms.c.term, r"([^[:alnum:][:space:]_])", r"\\\1", "g")
    pattern = r"(?<![[:alnum:]_])" + escaped + r"(?![[:alnum:]_])"
    n = (
        func.generate_series(1, func.regexp_count(v.c.body, pattern, 1, "i"))
        .table_valued("value")
        .lateral("n")
        .render_derived()
    )
    first = func.regexp_instr(v.c.body, pattern, 1, n.c.value, 0, "i") - 1
    after = func.regexp_instr(v.c.body, pattern, 1, n.c.value, 1, "i") - 1
    lead = func.greatest(first - CONTEXT_CHARS, 0)
    rows = await db.execute(
        select(
            terms.c.entity_id,
            terms.c.term,
            terms.c.alias,
            v.c.id,
            v.c.document_id,
            v.c.title,
            first,
            after,
            func.substr(v.c.body, lead + 1, after + CONTEXT_CHARS - lead),
        )
        .select_from(v.join(d, v.c.document_id == d.c.id).join(terms, literal(True)))
        .join(n, literal(True))
        .where(read_clause(subject, Action.RETRIEVE, d, v))
        .order_by(v.c.id, first)
    )
    return [
        {
            "entity_id": eid,
            "term": term,
            "alias": alias,
            "version_id": vid,
            "document_id": did,
            "title": title,
            "start": start,
            "end": end,
            "snippet": snippet,
        }
        for eid, term, alias, vid, did, title, start, end, snippet in rows
    ]
