---
version: 1
slug: "frontend-src"
primary_target: "frontend/src"
related_targets: []
---

## Scope

Whole web app (frontend/src), Operate mode. Daily tool for an engineering team: members search and write runbooks, managers approve exact versions and read the audit trail.

## Direction contract

THESIS: Every document is a drawing in a register: it carries a title block (ID, REV, author, date, what serves search and agents, sharing, last decision), and changes are marked like redlines. Refuses the generic status-pill admin table.
OWN-WORLD: Ink #121417 rail and title-block rules on drafting-film #eef1ef, sheets #fcfdfc, blueline #2b5fae for links and focus, redline #d43d2b only for changes and refusals. IBM Plex Sans, Plex Sans Condensed caps for labels and stamps, Plex Mono for ids, diffs and config. Stamps carry state by wording and border style, colour second.
STORY: A person sees what each document is, which revision serves search and agents, and who it reaches; a manager approves one exact revision from a clouded diff; anyone can see their agent gets exactly their search results.
FIRST VIEWPORT: Ink side rail (workspace switcher, role, Knowledge / Agents / Governance nav) beside a documents register with state filters and stamps; primary action "New document" top right. Document detail: text sheet left, ruled title block and revisions register right.
FORM: Drawing Register, user's pick over the roll (seed e508b0a4), position 1 on the grounded list.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Signature

Revision clouds (CSS border-image of a bundled SVG) round every added hunk on Approvals; REV letters derived from version order.

## Open decisions

Dark theme deferred. Approvals count badge in the rail deferred (needs a list fetch on every page).
