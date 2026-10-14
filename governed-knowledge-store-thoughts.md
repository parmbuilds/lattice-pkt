# Lattice

**Connected knowledge for AI agents.**

Date: 15 October 2026  
Status: Initial project proposal

## Purpose

Build Lattice, a governed knowledge store for AI agents, to demonstrate relevant engineering skills for Datacom’s **Senior Developer / Tech Lead – AI LAB** role and share it as a GitHub repository with the job application.

- [Job advertisement](https://nz.seek.com/job/94985365)
- Target application timeframe: within one week.
- Intended deliverable: a runnable GitHub repository with a short demo recording.
- Cloud preference: undecided. Sharing the repository does not require hosting a public application.

The aim is to demonstrate implementation quality, technical judgement and an understanding of how organisations can safely make their knowledge available to AI agents.

## Product idea

> A governed knowledge store that lets AI agents use approved organisational knowledge, with permissions, source citations and an auditable record of access.

Users ingest documents, manage their ownership and approval status, and make authorised content available to agents through MCP. Agents can search for evidence and use relationships between documents and entities to complete everyday tasks.

The platform can remain general-purpose while the first demonstration focuses on one concrete workflow.

## Recommended demonstration: engineering incident support

Use synthetic architecture documents, operational runbooks and past incident reports.

Example agent task:

> Prepare an investigation brief for checkout failures. Identify relevant dependencies, previous incidents and approved troubleshooting steps.

The demonstration should show:

1. Documents being ingested and approved.
2. An actual agent connecting through MCP and retrieving relevant evidence.
3. A brief with citations to exact passages and document versions.
4. An audit view showing the tool calls and evidence accessed.
5. A document being withdrawn or its access permissions changed.
6. A fresh retrieval respecting the updated approval or access state.

Use synthetic documents so the repository and recording can be shared without exposing employer or customer information.

## Fit with the job

The advertisement asks for Python/TypeScript, production GenAI experience, orchestration frameworks, RAG, vector databases, evaluation, cloud deployment and cloud-native engineering. Tool-using agents and advanced RAG approaches are additional interests.

| Role requirement | Evidence the project could provide |
| --- | --- |
| Python and TypeScript | Python backend and TypeScript web interface |
| RAG and vector databases | Document ingestion, hybrid retrieval and source-grounded outputs |
| Orchestration frameworks | Practical use of LlamaIndex for ingestion and retrieval |
| Tool-using agents | An agent completing a task through the MCP interface |
| Evaluation | Repeatable retrieval, citation and access-control checks |
| Engineering quality | Docker setup, automated tests, CI and documented trade-offs |
| Advanced RAG | A bounded comparison of standard and graph-assisted retrieval, if time permits |

The repository should complement a concrete example of production GenAI delivery from previous work. A portfolio demo alone does not establish production experience. Cloud and Kubernetes deployment remain evidence gaps unless they are actually implemented and verified.

## First-version scope

| Capability | Proposed behaviour |
| --- | --- |
| Document ingestion | Upload Markdown and text-based PDFs; inspect processing status, chunks and source versions |
| Governance | Track document owner, access group and draft → approved → withdrawn lifecycle |
| Retrieval | Combine keyword and semantic search and return exact supporting passages |
| MCP integration | Expose a small interface such as `search_knowledge` and `get_evidence` |
| Audit trail | Record which authenticated user or agent accessed which document versions |
| Evaluation | Run repeatable checks for retrieval quality, citations, permissions and withdrawn content |

The primary milestone is one complete workflow from ingestion to an agent-produced, cited brief.

## Governance design principles

- Enforce authorisation in the backend on every retrieval path.
- Use the same permission logic for the web interface and MCP tools.
- Derive identity from authentication. An agent-supplied user or group identifier must not grant access.
- Make approval status and document version explicit in the retrieval model.
- Apply permissions to supporting passages, graph relationships and any derived summaries.
- Ensure permission or approval changes affect subsequent retrieval, including any retrieval caches.
- Record enough provenance to explain which evidence was returned and used.

Revocation prevents future retrieval; it cannot erase content already delivered to an external agent. The demonstration and documentation should make that boundary clear.

Microsoft’s [secure multitenant RAG guidance](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/secure-multitenant-rag) is a useful reference for permission-aware retrieval. The [MCP authorisation specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) should inform any authenticated HTTP implementation.

## Proposed technical stack

These are starting recommendations, not final implementation decisions.

| Component | Proposed choice | Reason |
| --- | --- | --- |
| Backend | Python + FastAPI | Ingestion, retrieval, permissions and audit records |
| Web interface | TypeScript + React | Document management and evidence inspection |
| Retrieval framework | LlamaIndex | Document processing and retrieval orchestration; named in the advert |
| Data store | PostgreSQL + pgvector | Keep metadata, permissions and embeddings in one database |
| Agent interface | MCP SDK | A narrow tool interface, verified with one real client |
| Local environment | Docker Compose | Reproducible setup for reviewers |
| CI | GitHub Actions | Automated checks and visible validation |

pgvector supports combining vector retrieval with PostgreSQL full-text search. See the [pgvector documentation](https://github.com/pgvector/pgvector).

Relevant LlamaIndex references:

- [Document loading](https://developers.llamaindex.ai/python/framework/module_guides/loading/)
- [MCP integration](https://developers.llamaindex.ai/python/examples/tools/mcp/)

Model and embedding providers still need to be selected. The README must explain any API credentials or local model requirements. A GitHub deliverable removes the need for a public deployment, but does not remove model-runtime requirements.

## Knowledge graph: a bounded extension

Start with useful relationships that support the demonstration:

- A service depends on another service.
- An incident affects a service.
- A runbook covers a service.

Every relationship should link to supporting passages and respect their permissions. The graph should improve evidence retrieval for questions involving these connections.

Compare ordinary retrieval with graph-assisted retrieval on the same questions. Report whether the graph improves the results and what additional complexity it introduces.

A small evidence graph should be described accurately as graph-assisted retrieval. Implementing the full Microsoft GraphRAG approach would considerably expand the first release. Its [query documentation](https://microsoft.github.io/graphrag/query/overview/) distinguishes entity-focused retrieval from broader corpus-level reasoning.

## Evaluation approach

Prepare a small, fixed set of questions with expected supporting passages and access outcomes before implementing retrieval.

Evaluate:

- Whether the expected evidence is retrieved.
- Whether citations support the claims in the generated brief.
- Whether restricted and unapproved documents are excluded.
- Whether withdrawn documents are excluded from subsequent retrieval.
- Whether the agent identifies insufficient evidence instead of inventing an answer.
- Whether graph-assisted retrieval improves questions that require relationships across documents.

Report actual results, limitations and failed cases. Passing a finite access-control test suite is evidence of tested behaviour, not proof that every possible information leak has been eliminated.

## Suggested one-week plan

| Time | Focus |
| --- | --- |
| Days 1–2 | Synthetic documents, evaluation questions, permission model and ingestion |
| Days 3–4 | Retrieval, citations, MCP connection and one working agent task |
| Days 5–6 | Access-control tests, evaluation results and UI polish; add graph traversal if the core is solid |
| Day 7 | Three-minute demo, README, architecture diagram, trade-offs and application submission |

Protect time for packaging and applying. Graph functionality and cloud deployment should not displace the complete core demonstration.

## Repository deliverables

- A README that explains the problem and demonstration quickly.
- Reproducible setup instructions and clearly documented prerequisites.
- Synthetic sample documents and a repeatable demo scenario.
- MCP client configuration for the tested integration.
- Automated tests and evaluation instructions with measured results.
- An architecture diagram and a short explanation of important trade-offs.
- A short recording showing the successful task and a governance change.
- Clear distinctions between implemented features, verified behaviour and future work.

## Decisions still open

1. Confirm the incident-support scenario or choose another everyday task.
2. Select the generation and embedding providers.
3. Select the first MCP client and authentication approach.
4. Decide whether graph traversal fits after the core workflow is complete.
5. Decide whether any verified cloud deployment is achievable within the application window.

The recommended starting point is a complete, permission-aware retrieval workflow that a real agent can use, with evidence showing how well it works.
