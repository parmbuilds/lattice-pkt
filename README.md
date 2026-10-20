# Lattice

A governed knowledge store for AI agents. Teams add documents, an admin approves them, and agents retrieve only approved knowledge the person behind them is allowed to read, through MCP. Every decision is recorded in an audit trail.

This repository holds the authorization foundation (workspaces, roles, groups, a document lifecycle with approval, policy-filtered search, a retrieval-only MCP endpoint and the audit trail) and the document ingestion built on it: file upload with extraction receipts, hybrid keyword and vector search with checkable evidence spans, and a query-time entity graph in the UI.

## Quick start

Requirements: Docker.

```bash
make start   # docker compose up -d --build, then waits until the app is healthy
make seed    # docker compose exec app python -m app.seed
```

For development, `make dev` keeps the database in Docker and runs the API (uvicorn `--reload`) and the UI (Vite) on your machine at <http://localhost:5173>, both reloading on save; Ctrl-C stops both and `make start` switches back to the Docker app. The first run downloads the embedding model into the git-ignored `.cache/fastembed`.

`make` lists the other commands: `stop`, `restart`, `down`, `ps`, `logs` (one service with `s=app`), `migrate`, `psql`, `backup` and `restore file=…` (dumps go to the git-ignored `backups/`), `test`, `lint` and `e2e`.

Open <http://localhost:8000> and sign in as one of the seeded people (password `lattice-demo-password`):

| Email | Role in "Acme Engineering" |
|---|---|
| `olivia@lattice.demo` | owner |
| `arjun@lattice.demo` | admin |
| `mei@lattice.demo` | member, group Platform |
| `sam@lattice.demo` | member, group Payments |

The seed creates an incident-support scenario: an architecture document, runbooks, an incident report, a runbook edit waiting for approval, a withdrawn runbook, a draft and a rejected draft. Sign in as Mei and as Sam and search for `checkout`: each sees a different set of documents.

If port 5432 is taken, run `LATTICE_DB_PORT=55432 docker compose up --build`.

To connect an agent, create a token on the **Tokens** page and follow [docs/mcp-client.md](docs/mcp-client.md).

## How access works

Everything belongs to a workspace, and nothing crosses a workspace boundary. One module, [`backend/app/policy.py`](backend/app/policy.py), holds every rule; nothing else compares roles or lifecycle states.

| Action | Owner | Admin | Member |
|---|---|---|---|
| Read any document in the UI, any state | Yes | Yes | Own documents, plus approved documents shared with them |
| Retrieve through search or MCP | Approved only | Approved only | Approved and shared with them, or their own |
| Add documents, edit own | Yes | Yes | Yes |
| Approve or reject drafts | Yes | Yes | No (authors discard their own drafts) |
| Withdraw | Any | Any | Own |
| Share with groups | Any group | Any group | Groups they belong to |
| Manage groups, view members and audit | Yes | Yes | No |
| Invite | Members or admins | Members | No |
| Promote, demote, remove admins; transfer ownership | Yes | No | No |
| Connect an AI provider, change the model, key or conversation retention | Yes | Yes | No (sees the provider and model) |
| Ask with written answers; open and delete their own conversations | Own only | Own only | Own only |

- **Lifecycle.** Every document version is immutable. A new document or an edit becomes a draft; an admin or the owner approves one exact version, which then serves until a newer version is approved. Withdrawal stops every version being served and is final.
- **Retrieval is approved-only for everyone.** Drafts and withdrawn documents appear in the UI for their author and managers, never in search or MCP results.
- **Agents act as their user.** An MCP client authenticates with a personal access token and gets exactly its user's live access. MCP offers two retrieval tools and nothing else.
- **Changes apply on the next request.** Roles, groups, shares and lifecycle are read from the database on every request; nothing caches permissions.
- **Denials look like missing.** A request for something you may not see gets the same "Not found or not permitted" as a request for something that does not exist. The reason goes to the audit trail.

### One policy, two forms

Search cannot fetch the ten best matches and then drop the ones you may not read: a restricted-heavy corpus would return two results instead of ten, and the gaps would hint at what is hidden. So each read rule is written once as named terms. Each term evaluates in Python for a single decision and compiles to SQL, and the SQL form sits in the search query's `WHERE` clause before ranking and `LIMIT`.

## How it is verified

Run `cd backend && uv run pytest` (Docker must be running: the tests start PostgreSQL 18 with Testcontainers).

- **Equivalence property test** ([`test_policy_properties.py`](backend/tests/test_policy_properties.py)). A Hypothesis state machine drives the real API through document, sharing, group, role and membership changes across two workspaces. After every step it checks, for every person, that an independent reference model, the SQL read predicates, the single-item decision, UI search and MCP search all agree. A second test breaks one rule and confirms the property catches it.
- **Endpoint matrix** ([`test_endpoint_matrix.py`](backend/tests/test_endpoint_matrix.py), [`test_endpoint_global.py`](backend/tests/test_endpoint_global.py)). Every REST endpoint is called by the owner, an admin, an author, a reader, another member, an outsider and an anonymous caller in a fresh, known workspace. Each call is grounded three ways: the status against the role table, the response body against the database, and the exact rows it changed (a refusal changes nothing), plus the audit row it wrote. The MCP tools are checked the same way for every member. A guard test fails when a new route has no grounded test.
- **Acceptance examples.** Every example in the [plan](docs/plans/2026-10-15-1207-feat-workspace-authorization-plan.md) (AE1–AE11) has a test that cites it.
- **MCP over real HTTP.** Token checks, the closed tool list, ignored identity arguments, evidence lookups and cross-workspace isolation are tested against a running server, not an in-memory client.
- **Database guarantees.** Schema tests run as the runtime role and confirm it cannot update, delete or truncate audit rows, cannot change a document version, and owns nothing.

CI runs the backend tests, the property suite and the frontend build and tests on every pull request.

## Security notes and limits

- **The MCP endpoint is a demo adapter.** It accepts Lattice personal access tokens as bearer headers. It is not interoperable with the MCP 2025-11-25 OAuth profile, which needs an authorization server; clients that only use OAuth discovery will not connect.
- **The audit trail is append-only to the application.** The runtime database role can only insert and read audit rows, and triggers reject changes. A database superuser can still alter it; it is not tamper-proof.
- **Revocation stops future access only.** Text an agent already retrieved cannot be recalled; the audit trail records what each agent received.
- **Approval is a human gate, and humans miss things.** Reviewers see a raw-text diff with invisible format characters shown as escapes, but a determined author can still hide intent in plain text.
- **Not built for a public deployment yet:** no rate limiting on sign-in or token use, no password reset (there is no email delivery), no audit retention policy, and the database passwords in `db/init/01-roles.sql` and `compose.yaml` are local development defaults.

## Implemented, verified, deferred

| | Status |
|---|---|
| Workspaces, roles, invites, ownership transfer, groups | Implemented and tested |
| Document versions, approval, rejection, discard, withdrawal, sharing | Implemented and tested |
| Policy-filtered full-text search (PostgreSQL) | Implemented and tested, including hidden-top-match ordering |
| MCP endpoint with personal access tokens | Implemented and tested over HTTP; Claude Code connection checked by hand |
| Audit trail, fail-closed | Implemented and tested |
| Web UI for every flow | Implemented; component tests plus a manual end-to-end check |
| Upload of Markdown, text, HTML, text-layer PDF and DOCX into drafts (single files, several files, folders or drag and drop), with extraction receipts that flagged uploads need acknowledged | Implemented and tested |
| Derivation ledger (chunks and embeddings per version); approve waits until a draft is ready | Implemented and tested |
| Purge of derivatives when a version stops serving, with a receipt in the audit trail | Implemented and tested |
| Hybrid search (keywords plus embeddings, reciprocal rank fusion) with evidence spans; MCP `get_evidence` returns a span by `start` and `end` | Implemented and tested, including the property suite |
| Entity registry and a query-time, policy-filtered entity graph in the UI | Implemented and tested |
| Retrieval and lifecycle benchmark on the synthetic corpus | Implemented; `pytest -m benchmark` |
| Written answers in Ask from an OpenAI-compatible provider: checked citations, streamed sentences, private resumable conversations, retention, two audit rows per turn | Implemented and tested with a fake provider; no real provider checked in CI |
| AI entity suggestions, an AI approval assistant, provider embeddings, usage limits on AI answers | Deferred |
| OCR for scanned PDFs, an approximate vector index, LLM entity extraction or community reports, the graph over MCP, original-file download, zip upload | Deferred |
| MCP OAuth authorization server | Deferred |
| Agent accounts, scoped delegation, folders, nested groups, SSO | Deferred |
| Cloud deployment | Not attempted |

## Development

```bash
# Backend (Python 3.13, uv)
cd backend
uv sync
uv run pytest -m "not property"   # fast suite
uv run pytest -m property         # equivalence property tests
uv run pytest -m benchmark -s     # retrieval and lifecycle benchmark gates
FASTEMBED_CACHE_PATH=... uv run pytest -m benchmark_model -s   # real-model gate, needs a warmed cache
uv run ruff check .

# Frontend (Node 24)
cd frontend
npm ci
npm run dev    # proxies /api and /mcp to http://127.0.0.1:8000
npm test
npm run build
npm run e2e    # browser tests against the running stack (compose up + seed)
```

Run the API locally against the Compose database with `LATTICE_INSECURE_DEV_COOKIES=true uv run uvicorn --factory app.main:create_app --reload` after `uv run alembic upgrade head`. Without a private `LATTICE_SECRET_PEPPER`, the app starts only in this local development mode; settings are read from `LATTICE_*` environment variables (see [`backend/app/config.py`](backend/app/config.py)).

The database image is `pgvector/pgvector:pg18`. A `pgdata` volume created before pgvector has no `vector` extension and the migration stops with a message: run `docker compose down -v` to start fresh, or run `CREATE EXTENSION vector;` once in the `lattice` database as the `postgres` superuser.

Embeddings come from fastembed (`BAAI/bge-small-en-v1.5`). The image build downloads the model into `FASTEMBED_CACHE_PATH` (`/app/models`), checks it against a pinned hash, and runs with `HF_HUB_OFFLINE=1`, so the app never reaches the network for it. Outside the image, warm a cache once with `FASTEMBED_CACHE_PATH=<dir> uv run python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"` and point `FASTEMBED_CACHE_PATH` at it; the default tests use a deterministic fake embedder and skip `benchmark_model` unless it is selected.

## Layout

```text
backend/app/policy.py      every authorization rule
backend/app/audit.py       decisions and the audit trail
backend/app/routers/       REST API
backend/app/services/      document lifecycle, search, membership
  intake.py                uploads into drafts and receipt acknowledgement
  derive.py                chunking and embedding of every live version
  graph.py                 query-time entity graph
backend/app/ingest/        file sniffing, per-format extractors, extraction receipts
backend/app/derived.py     derived tables and purge receipts
backend/app/embedding.py   the embedder seam (fastembed, or a fake for tests)
backend/app/routers/entities.py  entity registry API
backend/app/mcp_server.py  MCP endpoint and token verification
backend/app/seed.py        demo data
frontend/src/              Vue UI (design system: DESIGN.md)
db/init/01-roles.sql       database roles (owner for migrations, app for runtime)
docs/plans/                requirements and implementation plan
```
