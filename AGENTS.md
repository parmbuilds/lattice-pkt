# Agent instructions

## Decisions

- Read `docs/decisions.md` before any task that involves design, architecture, stack or library choices, interfaces, authorization, audit, or data handling. Follow its decisions unless the user says otherwise. If a request conflicts with one, say which decision and ask; don't silently override it.
- `docs/decisions.md` is generated from the `Key Decisions` and `Key Technical Decisions` sections of `docs/plans/*.md`. Don't edit the generated block. Change the plan and run `make decisions`.
- When the user settles a decision in conversation that is not in a plan, append it under "Recorded decisions" in `docs/decisions.md`: date, decision, reason, and where it was agreed. Keep the entry to one short paragraph.
- Run `make hooks` once per clone. The pre-commit hook then regenerates `docs/decisions.md` whenever a plan is staged.
