# Decisions

Decisions that govern this project, with the reasons and alternatives given in the plans. Read this before designing, choosing a library or stack, changing an interface, or touching authorization, audit, or data handling.

- The block between the markers is copied from `docs/plans/*.md`. Do not edit it. Change the plan, then run `make decisions`.
- Decisions made in conversation that are not yet in a plan go in the log at the end.

<!-- BEGIN GENERATED: plan decisions -->

## Workspace Authorization and Policy Kernel

Source: [2026-10-15-1207-feat-workspace-authorization-plan.md](plans/2026-10-15-1207-feat-workspace-authorization-plan.md)

### Key Decisions (plan line 49)

- **Workspaces are tenants from day one.** User accounts are global and everything else belongs to exactly one workspace. Governs R1, R2, R3. (session-settled: user-approved — chosen over one workspace per deployment: avoids a migration that touches every table and query when Lattice goes multi-team.)

- **Document access comes from groups.** Grants are stored as subject → document, where the subject is a group or a single user; only group sharing has a UI in v1. Governs R11, R12, R13. (session-settled: user-approved — chosen over per-user sharing only, folders/spaces, or both groups and users in the UI: groups stay manageable as the workspace grows, and the subject shape keeps individual sharing open.)

- **Owners and admins can read all document content.** Governs R9. (session-settled: user-directed — chosen over separating "manage" from "read": simpler administration, accepting that admin and owner agents can retrieve every approved document.)

- **An agent acts as the user who connected it.** Governs R22, R23. (session-settled: user-directed — chosen over separate agent accounts and over capped delegation: simplest model, matching how most MCP integrations work today.)

- **Admins approve; members submit drafts.** Governs R15, R16. (session-settled: user-directed — chosen over authors publishing their own documents, after seeing that self-publishing plus admin read-all plus agent inheritance would let any member place unreviewed content in every admin agent's context.)

- **MCP is retrieval-only.** Governs R33. (session-settled: user-approved — chosen over letting agents also submit drafts, and over MCP mirroring every UI action: a prompt-injected admin agent cannot approve content it planted, so the approval gate stays human.)

- **Retrieval returns approved versions only, for every role.** Governs R19. (session-settled: user-approved — chosen over letting authors or admins retrieve drafts: one rule no agent can get around, and withdrawn content never reaches an agent.)

- **Authors share only with groups they belong to; admins share with any group.** Governs R12. (session-settled: user-directed — chosen over authors sharing with any group: stops members pushing content into teams they are not part of.)

- **One application-owned policy, applied as a decision and as a search filter.** Governs R25, R26, R27. (session-settled: user-approved — chosen over database row-level security as the primary enforcer and over an external relationship-based authorization service: explainable decisions with no extra infrastructure; grant storage stays compatible with either later.)

- **Approval does not require a second person.** Admins and the owner can approve their own drafts. Governs R15.

- **New documents start unshared.** A forgotten sharing step fails closed. Governs R11.

### Key Technical Decisions (plan line 282)

- KTD1. **Stack: FastAPI + async SQLAlchemy 2 (psycopg 3) + Alembic on PostgreSQL 18, a React 19 + TypeScript + Vite SPA, and the MCP Python SDK v2.** The backend uses `uv`; dependencies are constrained to the major versions in Sources and locked by `uv.lock` and `package-lock.json`. Python is pinned to 3.13 through `.python-version`, `requires-python` and the Compose and CI images, because wheels for psycopg, pydantic-core and argon2 on the host's 3.15 are unverified. Async was chosen over sync SQLAlchemy because the MCP tools and FastAPI handlers are async and share one service layer. LlamaIndex and pgvector are not needed until vector retrieval lands, so they are not added now.

- KTD2. **One policy module, `backend/app/policy.py`, owns every authorization rule.** It exposes a pure decision function for a subject, action and resource state that returns outcome, rule id and inputs (R27), and a SQL clause builder for the two read predicates, *retrieve* (R19) and *view in UI* (R20). Every service calls it; no route or query encodes a rule itself. Instantiates the Key Decision "One application-owned policy" and governs R25, R26, R27. (session-settled: user-approved — chosen over database row-level security as the primary enforcer and over an external relationship-based authorization service: explainable decisions with no extra infrastructure.)

- KTD3. **R26 is proven per read predicate against an independent oracle.** A Hypothesis `RuleBasedStateMachine` drives real services against real PostgreSQL and, after every step, compares the SQL result ids for *retrieve* and *view* with a plain-Python reference model that shares no code with `policy.py`. Unit tests cover the write-action matrix table-driven from the role capabilities table. Example-only tests were rejected because they cover the combinations someone thought of, and R26 is the claim reviewers will probe. Each Hypothesis example creates its own workspaces and uniquely named users and the invariants consider only those workspaces, so nothing is truncated and the fixture stays on the runtime role; the property suite is marked `property`, runs with `deadline=None`, a small local profile and a larger CI profile.

- KTD4. **Document content is immutable per version.** `document_versions` holds title and body; a document points at its serving version and at most one pending draft and carries a withdrawn timestamp. Approval is a conditional update that succeeds only if the named version is still the pending draft (R16, R17). Every agent-visible field lives on the version, so nothing reaches retrieval unreviewed. Each lifecycle transition is one `UPDATE documents` guarded by the expected pointers and `withdrawn_at IS NULL`, with its row count checked, so the document row is the single serialization point. A mutable row plus a history table was rejected because approval must name content that can never change afterwards.

- KTD5. **Browser and agent credentials never cross.** The browser uses a server-side opaque session cookie (`HttpOnly`, `SameSite=Lax`, `Secure` outside local development) plus a synchronizer CSRF token and an Origin check on unsafe methods. Personal access tokens are accepted only as `Authorization: Bearer` on `/mcp`; REST routes reject bearer credentials and `/mcp` ignores cookies. This closes the path where an admin's token could call management routes and bypass R33. One credential type with scopes was rejected because a scope bug would reopen that path. Sessions also have an absolute lifetime of 7 days, and creating a personal access token requires re-entering the password, so a hijacked session cannot quietly mint a long-lived credential.

- KTD6. **MCP uses the SDK's token-verifier hook with Lattice personal access tokens, labelled a demo adapter.** The verifier looks up the token on every request and returns only trusted claims (user, workspace, token id) after checking it is unrevoked, unexpired and its user is still a member (R23, R24). This is bearer-header compatible but not MCP 2025-11-25 OAuth-profile interoperable; the README says so, and an OAuth authorization server is deferred. The tool set is closed: `search_knowledge` and `get_evidence` (R33).

- KTD7. **Secrets are high-entropy and stored only as keyed verifiers.** Session ids, personal access tokens and invite links are 256-bit CSPRNG values stored as HMAC-SHA-256 under a server pepper with a non-secret lookup prefix, shown once and never logged. Passwords use Argon2id. A fast keyed hash is safe here because these values are random, unlike passwords. Invite tokens travel in the URL fragment and are redeemed by POST, and the accept page sends `Referrer-Policy: no-referrer`. Defaults: tokens expire after 90 days, member invites after 7 days, admin invites after 24 hours, sessions after 12 hours idle; all are configuration values.

- KTD8. **Audit is transactional and append-only to the runtime database role.** Each request runs in one transaction: decide, act or query, insert the audit row, commit, and only then respond (R28, R34). Migrations run as an owner role; the app connects as a role with `INSERT, SELECT` on `audit_events` only, and a trigger rejects `UPDATE`, `DELETE` and `TRUNCATE`. Roles are created by a bootstrap script outside Alembic; the runtime role is not a member of the owner role, holds no `TRIGGER` privilege, and default privileges grant it nothing on future tables. A denial commits its audit row and then returns the generic response (R29). Fail-closed audit means an audit outage is a retrieval outage, which R34 accepts. The README claims "append-only to the application", not tamper-proof against a database superuser.

- KTD9. **Search is one SQL statement.** PostgreSQL full-text search runs over a stored generated `tsvector` on version title and body with a GIN index. Workspace, retrieve-predicate and text match all sit in the `WHERE` clause before `ORDER BY rank LIMIT`, and counts and snippets come only from returned rows (R21). `limit` is capped at 20. Filtering after ranking was rejected because it violates R21 (AE10), and a vector index is deferred with vector retrieval. Snippets use plain-text markers, never HTML.

- KTD10. **People sign in with email and password; there is no password reset in v1** because email delivery is deferred. Sign-up is open; any signed-in person can create a workspace.

- KTD11. **Every user-supplied string renders as text, never as HTML or Markdown.** That covers document bodies, titles, group names, token names, rejection reasons, search queries and audit details; raw-HTML rendering is banned by lint. The approval screen shows a line diff of raw text against the serving version (R35) and makes invisible format characters (Unicode category Cf, such as zero-width spaces) visible as escapes. Sanitized Markdown was rejected because a sanitizer bug would hand member content an admin session.

- KTD12. **Same origin everywhere.** In development the Vite dev server proxies `/api` and `/mcp` to FastAPI; in Compose the backend serves the built SPA. No credentialed CORS is configured.

- KTD13. **Browser and MCP responses carry hardening headers.** A strict Content-Security-Policy without inline scripts, `frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, and `Secure` cookies on by default with an explicit development opt-out. Unsafe requests with a missing or foreign Origin are refused. `/mcp` validates Host and Origin through the SDK's transport security settings.

- KTD14. **Authorization and the action share one transaction; an in-flight request completes under the authorization it loaded.** The request transaction loads the actor's membership and decides before acting, so a change committed by another request applies from the next request (R24, R25), and the in-flight action is audited. Row locking to serialize role changes against in-flight actions was considered and not built: no requirement asks for it, and the races that corrupt data (double approval, two owners, no owner) are already closed by KTD4's guarded updates and the one-owner index. Role changes and transfers update a membership only while it still has the role that was authorized, so a role change racing a transfer returns 409 instead of demoting the new owner (found in code review).

## Document Ingestion, Governed RAG and Graph

Source: [2026-10-15-1737-feat-document-ingestion-rag-plan.md](plans/2026-10-15-1737-feat-document-ingestion-rag-plan.md)

### Key Decisions (plan line 83)

- **Derive the draft locally; Approve waits for readiness.** (session-settled: user-approved — chosen over approving first with a visible lexical-only lag, and over deriving only after approval: approve then means fully searchable in one commit, and no draft text leaves the machine.) Governs R10.

- **v1 file types are Markdown, text, HTML, text-layer PDF and Word, with no OCR.** (session-settled: user-approved — chosen over Docling or OCR toolchains: far lighter, and a page with no text layer becomes a blocking receipt flag instead of silently blank text.) Governs R1, R7.

- **Body cap is 1,000,000 characters.** (session-settled: user-approved — chosen over keeping 200,000: the old cap rejects PDFs over about 60 pages.) Governs R6.
  - **Conflict call-out:** Postgres caps a `tsvector` at about 1 MB. A body with very high word diversity can exceed it below 1,000,000 characters. Such a body is refused with 422 `body_too_diverse` instead of failing; ordinary prose of that length fits.

- **A graph connection is visible if any of its supporting evidence is readable, and descriptions are never stored.** (session-settled: user-approved — chosen over requiring all evidence to be readable: that over-hides co-mentions, and not storing merged prose removes the leak.) Governs R22.

- **Uploading, receipt acknowledgement and the graph stay off MCP in this plan.** An agent acting as its user could otherwise plant drafts in the review queue, and graph traversal needs its own oracle before agents get it. Governs R3, R23.

### Key Technical Decisions (plan line 121)

- KTD1. **Embeddings come from fastembed 0.9.0 with `BAAI/bge-small-en-v1.5` (384 dimensions) behind an `Embedder` seam on `Settings`.** Passages use `passage_embed` and queries use `query_embed`. The model is downloaded into a fixed cache directory at image build, then loaded with `local_files_only`, so runtime never reaches the network. Tests inject a deterministic fake embedder. PyPI confirms 0.9.0 is not yanked and onnxruntime 1.31.0 ships cp313 manylinux wheels, but an offline smoke test on the built image is a release gate.
  - The build verifies the downloaded model file against a pinned SHA-256.
  - Startup asserts the embedder dimension is 384.
  - The embedder lock is held per batch (`embed_batch_size`, default 32), never per document, so a query embedding waits at most one batch.
  - `embedder_threads` bounds ONNX CPU use. (session-settled: user-approved — chosen over hosted embedding APIs: no API key, no PyTorch, nothing leaves the machine.)

- KTD2. **Postgres runs from `pgvector/pgvector:pg18` (extension 0.8.7) in `compose.yaml` and the test container.** `CREATE EXTENSION vector` goes in `db/init/01-roles.sql` as superuser, because the extension is not marked trusted and migrations run as `lattice_owner`. Migration `0002` only checks that the extension exists and fails with a clear message otherwise. (session-settled: user-approved — chosen over stock `postgres:18`.)

- KTD3. **Derivation has one core, `derive(version_id)`, run in three steps.**
  1. Read the body in a short transaction.
  2. Chunk and embed with no database locks held.
  3. In a second transaction, lock the document row `FOR SHARE`, re-check that the document is not withdrawn, the version is still its current pending or serving version, the config hash is current, and the ledger row still exists. Then insert chunks and mark ready. Otherwise discard the result and delete the ledger row if it remains.
  - **Worker:** an in-process loop in the app lifespan claims a due row with `FOR UPDATE SKIP LOCKED`, sets a lease (`next_attempt_at = now() + lease`), and commits before deriving. A crashed claim is retried when its lease expires.
  - **Ordering:** each tick enqueues current pending or serving versions of non-withdrawn documents that lack a row at the current hash, taking each document row `FOR SHARE` so the insert serializes with lifecycle purges. Drafts go before backfill.
  - **Inline mode:** `derive_inline` runs the same core inside create, edit and upload requests. Only tests and the seed turn it on, because `httpx.ASGITransport` never runs the lifespan.
  - **Lock order everywhere:** `documents` → `derivations` → `chunks` → `intake_payloads`.
  - Rejected: a separate worker service or external queue (more infrastructure, no gain at this size), and holding the document lock while embedding (blocks withdraw for seconds).

- KTD4. **The readiness check is an `EXISTS` inside the guarded `UPDATE documents` in `decide_draft`.** It looks for a ready derivation for that version at the current config hash. A refusal is a new 409 (`DERIVATIONS_NOT_READY`), distinct from `STALE_DRAFT` and raised after authorization. Rejected: SELECT-then-UPDATE, which races a config change or purge. Implements the Key Decision governing R10.

- KTD5. **Chunks are spans, not copies.** A chunk row holds the version, config hash, ordinal, `start`, `end`, `sha256` and the embedding, with no text column. Chunks are paragraph-aligned and capped at about 1,200 characters so the embedded input (title prefix plus chunk) stays under the model's 512-token limit. `derive` asserts each span's hash against `body[start:end]` and that the spans cover the body. The contextual prefix is the document title, recomputed at embed time and never stored or returned.

- KTD6. **Vector ranking uses exact cosine distance over chunks of versions passing `read_clause(RETRIEVE)`, in the same SQL statement, before `LIMIT`.** The vector candidate subquery keeps its nearest `vector_candidates` (default 50) before fusion, and a plain index on `chunks(version_id, config_hash)` supports the join. No vector index. Rejected for now: HNSW with iterative scans, which is still approximate and would need its own recall oracle. The revisit trigger is under Scope Boundaries.

- KTD7. **Hybrid fusion is reciprocal-rank fusion per version.** It merges the lexical rank (`ts_rank`) and the best-chunk vector rank, keeping vector candidates under a configurable cosine-distance floor. Results are aggregated per version before `LIMIT`, and each result carries its best spans (up to 3).

- KTD8. **The purge runs inside the lifecycle transaction, by exclusion.** After each guarded lifecycle `UPDATE`, delete every derived row of that document whose version is no longer its current pending or serving version (all of them on withdraw). The counts come from those deletes. A new `audit.record_system(db, workspace_id, action, detail)` writes the `channel='system'` purge receipt in the same transaction, failing closed. Bodies stay, because `document_versions` is immutable, and the receipt says so. Rejected: purging version ids read before the update (a concurrent edit can make them stale), and a queued purge (it can write a receipt before the rows are gone).

- KTD9. **One `DERIVED_TABLES` registry maps each derived table to its purge rule.** It lists `derivations` (chunks cascade from it) and `intake_payloads`. The purge iterates it. A schema test lists every table with a foreign key to `document_versions`, outside the core lifecycle tables and the permanent `intakes` record, and fails when one is missing from the registry.

- KTD10. **`config_hash` is the SHA-256 of the model name, fastembed version, dimension and chunker version.** The worker derives any pending or serving version with no ready row at the current hash, then deletes that version's rows at older hashes. Approve and vector search read only the current hash. A config change deploys as a single-process stop and start, so two hashes never compete.

- KTD11. **Upload is `POST /api/workspaces/{workspace_id}/documents/upload` (multipart: `file`, optional `title`, optional `document_id`, optional `new_document`).**
  - **Title:** the `title` field when given. Otherwise a new document takes the filename without its extension, and a new version takes its document's current title. The result has invisible characters stripped and is truncated to the title limit. Title matching (R5) compares this resolved title with the uploader's live document titles, case-insensitively after trimming.
  1. A path-scoped ASGI wrapper runs before multipart parsing and before any database work.
     - It refuses requests without a valid session cookie.
     - It answers 429 when `upload_max_inflight` uploads (default 4, below the database pool size) are already in progress.
     - It counts bytes as they stream, aborting with 413 past `upload_max_bytes` (default 20 MB) whether or not `Content-Length` is present.
  2. Authorization comes next: `CREATE_DOCUMENT`, or `EDIT` when a `document_id` is given.
  3. Content sniffing: `%PDF-` means PDF. A zip containing `word/document.xml` means DOCX. Anything else must be strict UTF-8, and the extension then chooses md, txt or html.
  4. Extraction runs in a child process killed at `extract_timeout_seconds`. An address-space limit is applied where the platform supports it (the Linux image and CI); on macOS development machines it is skipped with a logged warning, and the timeout, page cap and zip bounds still apply. A global semaphore (`extract_concurrency`) bounds in-flight extractions.
  5. The write order is document and draft (`new_draft`), then the intake and its payload. A transaction-scoped advisory lock on (workspace, author, sha256) serializes the dedupe check.
  6. Rejections after authorization (unsupported type, malformed or encrypted file, zip, page or decompressed-size limits, timeout) are persisted as intakes with a reason and no receipt or payload, through `CommitThenRaise`, and answered 415 or 422. Filename and reason are truncated to 256 characters. The pre-authorization 401, 413 and 429 responses write nothing.

- KTD12. **Extractors are stdlib plus pypdf 6.20.1 and python-docx 1.2.0.**
  - **PDF:** `PdfReader(strict=True)` with a declared-stream cap and a page cap. Encrypted files are rejected, and a page whose `extract_text()` is empty is flagged.
  - **DOCX:** the body comes from top-level paragraphs via python-docx. A namespace-aware, entity-free scan of the raw OOXML parts flags comments, `w:ins`/`w:del`/`w:moveFrom`/`w:moveTo`, `w:vanish` and white text. A character-accounting check (all `w:t` text vs. extracted plus flagged) flags anything unaccounted for, such as text boxes, footnotes and headers.
  - **Zip:** member count, per-member size, total size and compression ratio are bounded, with no extraction to disk.
  - **HTML:** a `html.parser` stack extractor drops comments, `script`, `style`, `template`, `noscript`, `[hidden]`, `aria-hidden="true"` and inline `display:none`/`visibility:hidden` subtrees. `visibility_ambiguous` is raised only for an external stylesheet link, or a `<style>` block or `style` attribute containing a hiding declaration (`display:none`, `visibility:hidden`, `opacity:0`, `font-size:0`, or off-screen positioning). Class attributes alone do not raise it, so routine wiki and CMS exports carry no flag.
  - **Markdown:** HTML comments are removed and flagged.
  - **All types:** format characters (Unicode category Cf), bidirectional controls and the tag block U+E0000–E007F are stripped from the body and flagged `invisible_chars`. Text decodes as `utf-8-sig`, so a leading byte-order mark is dropped silently, and soft hyphens (U+00AD) are stripped without a flag.
  - **Notes:** the receipt also carries non-blocking `notes`, included in its hash but never counted as flags. Every PDF receipt carries `pdf_invisible_text_not_checked`.
  - **Size:** a body whose `tsvector` would exceed the Postgres limit is refused with 422 `body_too_diverse`.

- KTD13. **The receipt is split into a permanent part and a purgeable part.**
  - The permanent intake row holds the receipt flags, the section counts and a SHA-256 over them plus the digest of each hidden excerpt.
  - The hidden excerpt text and the original bytes live in a purgeable `intake_payloads` row, so the acknowledgement hash still verifies after a purge.
  - The approve body gains an optional `acknowledged_receipt`, and `version_decisions` gains a nullable `acknowledged_receipt_sha256`.
  - Rejected: keeping excerpts on the immutable intake row (hidden content would outlive withdrawal), and a separate acknowledgement table (heavier for no benefit given `g_decision`'s exact-table assertion).

- KTD14. **The graph is computed at query time in SQL, and only the registry is stored.**
  - Aliases are matched as escaped literals, case-insensitively on word boundaries, against bodies of versions passing `read_clause(RETRIEVE)`, inside the same statement.
  - Mention spans are the match positions. Co-mentions are the other registry entities found in those same versions.
  - Members see an entity only when it has a readable mention, and see only the aliases that have one. Managers see the whole registry.
  - Entities per workspace (default 200), aliases per entity (default 20) and alias length (default 100) are capped on write.
  - Shortcut: each graph query scans the readable corpus. The revisit trigger is under Scope Boundaries.

- KTD15. **The MCP surface stays two tools.** `search_knowledge` results gain `spans` and `retrieval` (`hybrid` or `lexical`), and `get_evidence` gains optional `start` and `end`. A test asserts that `DECLARED` equals each tool's schema properties. New actions (upload, entity management, graph read) are denied on the MCP channel by the existing allowlist, with guard tests.

- KTD16. **Audit `detail` for search and evidence gains `returned_spans` as `[version_id, start, end]` triples.** `returned_version_ids` keeps listing distinct versions in result order, so existing audit grounds still hold.

- KTD17. **The benchmark lives in `backend/tests/benchmark/` behind the `benchmark` marker.** It uses deterministic hostile-file builders shared with the extractor tests. The permission, citation and deletion gates run in CI with the fake embedder. Real-model recall runs behind a separate `benchmark_model` marker that needs the cached model.

## Written Answers in Ask

Source: [2026-10-20-1606-feat-ai-answers-in-ask-plan.md](plans/2026-10-20-1606-feat-ai-answers-in-ask-plan.md)

### Key Decisions (plan line 48)

- **Written answers in Ask come first among the AI features.** (session-settled: user-directed — chosen over entity suggestions, the approval assistant and provider embeddings: it sends the least sensitive text and builds the provider layer the others reuse.)

- **Each workspace brings its own provider key, managed by its owners and admins.** Governs R2, R4, R5. (session-settled: user-directed — chosen over an operator-configured provider with owner opt-in, an operator provider on for everyone, and an operator default with owner override: per-workspace control, accepting that Lattice must store and protect a secret per workspace.)

- **Providers come only from an operator-curated list.** Governs R1, R3. (session-settled: user-directed — chosen over public HTTPS endpoints plus an operator allowlist, public endpoints only, and any URL: owners never enter a URL, so the server cannot be pointed at internal addresses.)

- **Full conversation memory, guarded by a per-turn currency check.** Governs R14, R15. (session-settled: user-directed — chosen over re-searching with earlier questions only and over standalone questions, then over amending authorization R18/AE9: natural follow-ups while withdrawn or hidden text still never comes back.)

- **Only currently serving text stays in a conversation.** Governs R15, R28. (session-settled: user-directed — chosen over keeping turns while any approved revision of the document is readable: an approved edit replaces the old text on purpose, and the UI and MCP keep one meaning of "readable".)

- **Long conversations are condensed into a summary that is discarded whole when any source goes.** Governs R16, R17. (session-settled: user-directed — chosen over leaving out the oldest turns and over ending a full conversation; discarding the whole summary keeps the currency guard exact, since one source cannot be removed from a blended summary.)

- **Conversations are stored on the server, per person, and resumable.** Governs R18. (session-settled: user-directed — chosen over keeping them in the open tab only and over shareable conversations.)

- **Owners and admins set retention, default 30 days, counted from the last turn.** Governs R20. (session-settled: user-directed — chosen over a fixed 30 days and over keeping conversations until deleted; counted from the last turn rather than the first so a conversation in use never expires mid-use.)

- **Conversations are private; the audit trail records each turn but not the answer.** Governs R19, R23. (session-settled: user-directed — chosen over manager-readable conversations and over storing answers in the audit trail: the append-only trail must not hold document-derived text that cannot be removed on withdrawal.)

- **Answers use checked citations.** Governs R8, R9, R10. (session-settled: user-directed — chosen over free answers with unchecked citations and over quotes with a one-line lead-in: every sentence shown is tied to approved text or marked as unsupported.)

- **Answers stream sentence by sentence.** Governs R26, R29. (session-settled: user-directed — chosen over a whole answer shown at once: answers appear sooner, and each sentence is still checked before it is shown.)

- **AI answers are off until a workspace connects a provider; failures fall back to passages; agents never get answers.** Governs R6, R13, R24. (session-settled: user-approved — proposed in the scoping synthesis with its consequences; accepted.)

- **Disconnect, settings visibility, the rejected-key warning, retroactive retention and memory-only follow-ups follow the planning synthesis.** Governs R20, R27, R30, R31. (session-settled: user-approved — proposed in the planning-time scoping synthesis; accepted.)

### Key Technical Decisions (plan line 217)

- KTD1. **A provider seam modeled on the embedder.** A `ChatProvider` protocol with an `httpx` OpenAI-compatible streaming implementation and a deterministic fake, selected per operator provider entry, as `Embedder`/`FakeEmbedder`/`make_embedder` are (`backend/app/embedding.py`). No OpenAI SDK: `httpx` 0.28.1 is already locked, and several servers are only "compatible" ([vLLM](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/), [Ollama](https://docs.ollama.com/openai)). Each operator entry is a typed record (id, label, base URL, auth header kind, offered models with context and output limits); the provider list and the fake kind are JSON in `LATTICE_AI_PROVIDERS`.

- KTD2. **The master encryption key lives in `.env`.** Workspace keys are encrypted with AES-GCM from `cryptography`, a fresh nonce per encryption, and the workspace and provider ids bound as associated data, so a ciphertext copied to another workspace fails. The key list is `LATTICE_AI_KEY_ENCRYPTION_KEYS` in `.env` or the environment, newest first; older entries only decrypt, which allows rotation. Startup refuses a configured provider list without a valid key, as it refuses the dev pepper (`backend/app/main.py`). Because the provider id is bound in, changing the provider requires entering a new key in the same save. Governs R4. (session-settled: user-directed — chosen over a cloud key-management service: the deployment's `.env`, like the existing secret pepper.)

- KTD3. **Policy actions, not service checks.** New `Action`s: `ask` (any member), `manage_own_conversations` (any member, scoped to their own rows in the query, like `searches.own`), `view_ai_settings` (any member) and `manage_ai_settings` (owner or admin). `decide` already denies all of them on the MCP channel. The currency guard is one query over the cited version ids with `read_clause(subject, Action.RETRIEVE, …)`, so it has exactly the read rule MCP's `get_evidence` applies (`backend/app/policy.py`, `backend/app/mcp_server.py`). No read rule changes, so the property suite stays as it is.

- KTD4. **Conversations are derivatives of the versions they cite, purged whole.** Each turn records the spans it sent (document, version, start, end, sha256), and each summary its source versions, in tables with a composite key to `document_versions` like `version_decisions`. The bill of derivatives gets a turn-level rule: `purge_document` deletes every turn and summary with any stale passage or source row, the rows cascading, registered before the passage tables as the file already orders `chunks` and `derivations`; their counts join the receipt (R28), and each affected conversation's left-out count is increased, so the next reopen or follow-up shows R15's and R17's notices for text the purge removed, then the count resets. Passage text is never copied: version bodies are immutable, so memory re-reads the spans. Governs R14, R16, R28.

- KTD5. **A turn runs in three steps, with no transaction held across the provider call.** Governs R23, R29.
  1. In the request transaction: authorize, mark the conversation's turn in flight (KTD10), run the guard, search, and write the "sent" audit row; the request session commits as the handler returns its streaming response, before the stream starts. Never commit the request session inside the handler; if the installed FastAPI tears the dependency down only after the stream ends, step 1 uses its own session instead, and U9's ordering test decides which.
  2. With no open transaction: stream from the provider; before releasing each sentence, reload the asker's live subject and re-check the cited versions in a short session, and on any loss cancel the stream and send the discarded event.
  3. In a fresh session: reload the subject, take `FOR SHARE` on every cited document row in ascending id order (the discipline `derive.sweep` and `derive.derive` already use), re-check under that lock, then write the turn, any summary from step 2, and the outcome row. A purge that starts afterwards waits on the lock and then deletes the committed turn.
  4. On any failure in step 3, roll back and write the outcome row ("discarded" or "conversation changed") in a new transaction, so every "sent" row has exactly one outcome row.
  5. A client disconnect or Stop cancels the stream; a shielded cleanup writes the "abandoned" outcome and clears the in-flight mark, and the retention sweep (KTD11) does the same for any mark older than the turn timeout.
  6. A member removed mid-turn is handled by the conversation foreign key: the turn insert blocks the parent delete, and the cascade removes the committed turn.

- KTD6. **Citations are inline markers, checked one sentence at a time.** The model writes one sentence per line ending in markers such as `[P2]`; each passage sent this turn, including those carried in memory, gets a short id. A sentence passes when every marker names a sent passage, its content words overlap that passage's by at least a threshold tuned on U5's labeled set, and it carries the same number of negation words (not, no, never, without and the like) as the cited passage, so an inverted step fails. JSON structured output is not used, because it cannot be checked until it is complete. Governs R8, R9, R26.

- KTD7. **Server-sent events over a POST stream.** The turn route returns `text/event-stream`; the browser reads it through `fetch` (which keeps the CSRF header and Origin check) rather than `EventSource`, which cannot POST. `GuardMiddleware` is plain ASGI, so it does not buffer the stream, and the CSP's `connect-src 'self'` allows it. Events carry passages, checked sentences, a replace-with-not-covered instruction, notices and the final outcome.

- KTD8. **The prompt treats everything but its own rules as data, and gives the model no tools.** One system message states the rules; passages, earlier answers and summaries all go in delimited records marked as data, never as instructions; requests carry no `tools`. Answer text renders as plain text through the existing text-only components, with no automatic links or images. The wording check (KTD6) does not detect injected wording copied from an approved passage; no tools, plain text and the asker's own read access bound what that can do. Governs R12. Source: [OpenAI prompt-injection guidance](https://openai.com/safety/prompt-injections/).

- KTD9. **One attempt per turn, and failures map to stable outcomes.** The client never follows redirects, and the auth header kind is a closed set (bearer or `api-key`), so the key cannot be sent anywhere but the listed endpoint. Connect, read and pool timeouts are explicit; a 401 marks the workspace key as rejected (shown to managers, R30); every other failure, including malformed output and responses after the timeout, is "provider failed". Askers see one notice for every cause (R13). Saving a new key, provider or model sends one request with a fixed prompt and no workspace text, so a bad key or model is caught then, not by the first asker; a retention-only save calls no provider.

- KTD10. **One turn in flight per conversation.** Step 1 locks the conversation row, refuses the turn with "An answer is already in progress" when a fresh in-flight mark exists, and otherwise sets the mark with its turn number; step 3 or the cleanup clears the mark only when that number still matches, and a mark older than the turn timeout (the provider read timeout plus 30 seconds) counts as clear. Turn numbers stay unique per conversation as a backstop. This prevents two tabs streaming into one conversation; it does not cap spend, which stays uncapped until the deferred usage limits ship. Lock order everywhere: documents, then conversations, then turns, added to the lock-order comments in `backend/app/services/derive.py` and `backend/app/derived.py`.

- KTD11. **Retention runs in a sweep next to the derivation worker.** A periodic task started in the app lifespan deletes conversations past their workspace's retention; reads also treat an expired conversation as absent, so nothing reappears between sweeps.

- KTD12. **Memory is budgeted by an estimate before each call.** Lattice estimates prompt size from character counts against the chosen model's configured context, keeping room for the answer; when over, step 2 condenses the oldest turns in one non-streaming call with no open transaction, and step 3 saves the summary and its sources under KTD5's lock, discarding it if any source stopped serving during the call (KTD4).

- KTD13. **Ask turns stay in "Your last 10 queries".** Ask questions already appear there today (`via=ask`); the history route includes `ask` audit rows as source "Ask" only while their conversation still exists, so a deleted or expired conversation also leaves the asker's own history, and choosing one reopens that conversation.

- KTD14. **Routes, checks and errors follow the existing API edge.** Every new route lives under `/api/workspaces/{workspace_id}/`, so `GuardMiddleware`'s Origin, CSRF and bearer refusal apply, and takes the `Member` subject. The key is validated in the service, never in the request schema, so FastAPI's default 422 body, which echoes submitted input, can never contain it.

- KTD15. **The development master key follows the pepper's rule.** Startup refuses the published development key whenever development cookies are off, exactly as it refuses `DEV_PEPPER`; `compose.yaml` labels both as development-only. A stricter rule would break `make start`, whose stack already runs with development cookies.

<!-- END GENERATED: plan decisions -->

## Recorded decisions

Decisions made outside a plan, newest last. Each entry gives the date, the decision, the reason, and where it was agreed.
