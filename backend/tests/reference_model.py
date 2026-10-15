"""Independent oracle for read access (KTD3). Shares no code with app/policy.py.

Re-implements, in plain Python over plain data, the Product Contract's read rules:
- R3: nothing crosses a workspace boundary; non-members see nothing.
- R9: owners and admins read every document in any lifecycle state (UI view).
- R11/R13/R20: authors see their own documents in any state; others with a group or
  direct grant see only the approved serving version of a non-withdrawn document.
- R19: retrieval returns only the serving version of a non-withdrawn document.
"""

from dataclasses import dataclass, field
from uuid import UUID


@dataclass
class World:
    # (workspace_id, user_id) -> role
    roles: dict[tuple[UUID, UUID], str] = field(default_factory=dict)
    # group_id -> (workspace_id, set of member user ids)
    groups: dict[UUID, tuple[UUID, set[UUID]]] = field(default_factory=dict)
    # document_id -> {workspace, author, serving, withdrawn, versions, group_grants, user_grants}
    documents: dict[UUID, dict] = field(default_factory=dict)


def _can_see_document(world: World, workspace: UUID, user: UUID, doc: dict) -> str | None:
    """Return 'all' when every version is visible, 'serving' when only the serving one is."""
    role = world.roles.get((workspace, user))
    if role is None or doc["workspace"] != workspace:
        return None
    if role in ("owner", "admin") or doc["author"] == user:
        return "all"
    in_group = any(user in world.groups[g][1] for g in doc["group_grants"] if g in world.groups)
    if in_group or user in doc["user_grants"]:
        return "serving"
    return None


def viewable_versions(world: World, workspace: UUID, user: UUID) -> set[UUID]:
    result: set[UUID] = set()
    for doc in world.documents.values():
        scope = _can_see_document(world, workspace, user, doc)
        if scope == "all":
            result.update(doc["versions"])
        elif scope == "serving" and doc["serving"] and not doc["withdrawn"]:
            result.add(doc["serving"])
    return result


def retrievable_versions(world: World, workspace: UUID, user: UUID) -> set[UUID]:
    result: set[UUID] = set()
    for doc in world.documents.values():
        if doc["withdrawn"] or not doc["serving"]:
            continue
        if _can_see_document(world, workspace, user, doc):
            result.add(doc["serving"])
    return result
