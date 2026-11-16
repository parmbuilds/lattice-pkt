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
        }
        for eid, term, alias, vid, did, title, start, end in rows
    ]


async def passages(db: AsyncSession, subject: Subject, found: list[dict]) -> list[dict]:
    """Readable context for each entity's matches in a version: one passage per run of nearby
    matches, word-bounded, with the match offsets inside the snippet for the UI to mark.

    Returned as text and offsets, never HTML (KTD11). The bodies are read under the same policy
    as the matches, so a version the subject cannot read never reaches this function's output.
    """
    d, v = Document.__table__, DocumentVersion.__table__
    version_ids = {m["version_id"] for m in found}
    rows = await db.execute(
        select(v.c.id, v.c.body)
        .select_from(v.join(d, v.c.document_id == d.c.id))
        .where(v.c.id.in_(list(version_ids)), read_clause(subject, Action.RETRIEVE, d, v))
    )
    bodies = dict(rows.all())
    groups: dict[tuple, list[dict]] = {}
    for m in found:
        if m["version_id"] in bodies:
            groups.setdefault((m["version_id"], m["entity_id"]), []).append(m)
    out = []
    for hits in groups.values():
        body = bodies[hits[0]["version_id"]]
        hits.sort(key=lambda m: m["start"])
        runs: list[tuple[int, int, list[dict]]] = []  # (window start, window end, matches)
        for hit in hits:
            lo, hi = _window(body, hit["start"], hit["end"])
            if runs and lo <= runs[-1][1]:
                prev_lo, prev_hi, members = runs[-1]
                runs[-1] = (prev_lo, max(prev_hi, hi), [*members, hit])
            else:
                runs.append((lo, hi, [hit]))
        for lo, hi, members in runs:
            prefix = "…" if lo > 0 else ""
            snippet = prefix + body[lo:hi] + ("…" if hi < len(body) else "")
            marks = _merge_marks(
                [(m["start"] - lo + len(prefix), m["end"] - lo + len(prefix)) for m in members]
            )
            first = members[0]
            out.append(
                {
                    "entity_id": first["entity_id"],
                    "version_id": first["version_id"],
                    "document_id": first["document_id"],
                    "title": first["title"],
                    "term": first["term"],
                    "start": first["start"],
                    "end": members[-1]["end"],
                    "snippet": snippet,
                    "marks": marks,
                }
            )
    return sorted(out, key=lambda p: (str(p["version_id"]), p["start"]))


def _window(body: str, start: int, end: int) -> tuple[int, int]:
    """CONTEXT_CHARS either side of a match, moved out to whole words but never past the match."""
    lo = max(start - CONTEXT_CHARS, 0)
    hi = min(end + CONTEXT_CHARS, len(body))
    if lo > 0:
        while lo < start and not body[lo - 1].isspace():
            lo += 1
    if hi < len(body):
        while hi > end and not body[hi].isspace():
            hi -= 1
    return lo, hi


def _merge_marks(spans: list[tuple[int, int]]) -> list[list[int]]:
    """Overlapping matches (a name and an alias at the same place) become one mark."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged
