# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Current: Vue 3, Vite, vue-router, Tailwind 4, served as a static SPA by the FastAPI app on the same origin (KTD12).

## Users

An engineering team that keeps its operational knowledge in one workspace and lets AI agents use it during incident support.

- **Owner and admins** (seed: Olivia, Arjun) curate the workspace: review and approve drafts, manage members and groups, read the audit trail.
- **Members** (seed: Mei in Platform, Sam in Payments) write and edit runbooks, architecture notes and incident reports, share them with their groups, search, and create personal access tokens for their agents.
- **AI agents** (for example Claude Code over MCP) retrieve approved evidence as their user. They never see the UI, but the UI is where people understand and control what agents can reach.

The UI must work as a believable daily tool for this team, not a demo stage.

## Product Purpose

Lattice is a governed knowledge store for AI agents. It lets an organisation make approved knowledge available to agents with permissions, version-exact approval and an auditable record of access. Success: a team member can trust that what their agent retrieves is approved, permitted and traceable, and a manager can see and prove it.

## Positioning

- One policy, two forms: each read rule is evaluated in Python for single decisions and compiled to SQL inside search before ranking and LIMIT, so results are never filtered after the fact.
- Retrieval is approved-only for everyone; drafts and withdrawn documents exist only in the UI for their author and managers.
- Approval binds to one exact immutable version.
- Agents act as their user and get exactly that user's live access; nothing caches permissions.
- Denials look like missing: "Not found or not permitted", with the reason only in the audit trail.

## Operating Context

- Demonstration scenario: incident support for checkout failures, workspace "Acme Engineering". Architecture documents, runbooks, incident reports, a runbook edit awaiting approval, a withdrawn runbook, a draft and a rejected draft.
- The same search (`checkout`) returns different documents for Mei and Sam.
- Approval review shows a raw-text diff with invisible format characters shown as escapes.
- Agents connect with a token from the Tokens page (docs/mcp-client.md); MCP offers two retrieval tools only.
- Every decision and denial is written to an append-only audit trail; the app fails closed if audit writes fail.

## Capabilities and Constraints

- Implemented: workspaces, roles (owner, admin, member), invites, ownership transfer, groups, document versions, approve, reject, discard, withdraw, sharing, policy-filtered full-text search, MCP with personal access tokens, audit trail.
- Implemented for ingestion and retrieval: upload of Markdown, text, HTML, text-layer PDF and DOCX files into drafts (one at a time, several at once, a whole folder, or dropped onto the Documents page), each with an extraction receipt whose flags an approver must acknowledge by hash; a derivation ledger (chunks and embeddings per version) so approve waits until a draft is ready for search; purge receipts in the audit trail when a version stops serving and its derivatives are deleted; hybrid search (keywords plus embeddings, policy-filtered before ranking) with evidence spans an agent can check through `get_evidence`; an entity registry curated by managers and a query-time entity graph in the UI; a retrieval and lifecycle benchmark on the synthetic corpus.
- Implemented for written answers: when an owner or admin connects an OpenAI-compatible provider from the operator's list with the workspace's own key, Ask writes answers drawn only from approved passages the asker can read. Every sentence must cite a passage it matches, unsupported sentences are marked, and an answer with no supported sentence reads "Your documents don't cover this". Answers stream one checked sentence at a time; a document that stops being readable mid-answer ends the turn unsaved. Conversations are private to their asker, resumable, re-checked against live access on every follow-up and reopen, purged with the versions they cite, and deleted after the workspace's retention period (1 to 365 days, default 30). Every turn writes a "sent" audit row before anything reaches the provider and an outcome row after, never the answer text. MCP is unchanged: two retrieval tools, no generated text.
- Lifecycle terms: draft, approved, rejected, discarded, withdrawn (final). Edits create new drafts; the approved version keeps serving until a newer one is approved.
- CSP: `default-src 'self'; script-src 'self'; style-src 'self'`. No inline styles and no runtime-injected style tags; styling must ship as bundled files.
- The UI never decides access itself; it renders what the API returns. Role-gated navigation is presentation only.
- Not built, do not show or imply: AI entity suggestions, an AI approval assistant, provider embeddings, usage or spend limits on AI answers, answers over MCP, OCR for scanned PDFs, an approximate vector index, LLM entity extraction or community reports, the graph over MCP, original-file download, zip upload, OAuth for MCP, agent accounts, scoped delegation, folders, nested groups, SSO, password reset, email delivery, rate limiting, cloud deployment.

## Brand Commitments

- Name: Lattice. Line from the proposal: "Connected knowledge for AI agents."
- No logo, palette or typeface is committed.

## Evidence on Hand

- Seed data in `backend/app/seed.py` (synthetic, label it as such where it could be mistaken for real).
- README and `docs/plans/2026-10-15-1207-feat-workspace-authorization-plan.md` (requirements R1-R35, acceptance examples AE1-AE11).
- The only benchmark is the synthetic one in `backend/tests/benchmark/`. No customers, metrics or testimonials exist; do not invent them.

## Product Principles

1. Make governance visible: every document shows its state, which version serves, and who can reach it.
2. Never leak what is hidden: absence and denial must look identical in the UI.
3. The API is the authority: the UI explains decisions, it does not make them.
4. Built for repeated daily use: scanning speed and predictable placement beat flourish.
5. Honest about limits: deferred features stay absent rather than mocked.
