# Connecting an agent to Lattice over MCP

Lattice exposes a Model Context Protocol endpoint at `/mcp` (Streamable HTTP). An agent connects with a **personal access token** and then acts as that person: it sees exactly what they could retrieve in the UI, and nothing more.

## 1. Create a token

Sign in, open the workspace, and go to **Tokens**. Enter a name and your password; the token (`lat_pat_…`) is shown **once**. A token belongs to one workspace, expires after 90 days, and stops working as soon as it is revoked or you leave the workspace.

## 2. Configure the client

The endpoint is `http://localhost:8000/mcp` for the Compose stack. Send the token as a bearer header on every request:

```text
Authorization: Bearer lat_pat_...
```

### Tested clients

| Client | How it was tested | Configuration |
|---|---|---|
| MCP Python SDK 2.3 (`mcp` package) | Automated tests in `backend/tests/test_mcp.py` run every scenario over real HTTP | `streamable_http_client(url, http_client=create_mcp_http_client(headers={"Authorization": "Bearer <token>"}))` |
| Claude Code 2.1 | Connected to the Compose stack; `claude mcp get` reported `Connected` | `claude mcp add --transport http lattice http://localhost:8000/mcp --header "Authorization: Bearer <token>"` |

Any client that lets you set a static HTTP header on a Streamable HTTP MCP server should work the same way, but only the clients above were tested.

### Clients that will not work yet

Clients that connect to remote MCP servers **only through OAuth discovery** (for example, connector UIs that ask for a server URL and then start an OAuth sign-in) will not work: Lattice does not run an OAuth authorization server, so it advertises no protected-resource metadata. See "Limitations" below.

## 3. Tools

| Tool | Arguments | Returns |
|---|---|---|
| `search_knowledge` | `query` (text), `limit` (1-20, default 10) | Approved document versions you may read, ranked by keywords and embeddings together, each with a snippet (matches marked `[[like this]]`), its approver and approval time, up to three evidence `spans` (`start`, `end`, and the `sha256` of that slice of the body), and `retrieval` (`hybrid`, or `lexical` when the query could not be embedded) |
| `get_evidence` | `version_id`, optional `start` and `end` | One approved, serving version you may read: the full body, or with both `start` and `end` exactly that slice, plus a `span` (`start`, `end`, `sha256`) so a span from search can be checked. Only one bound, or a range outside the body, gets "Not found or not permitted" |

There are no other tools. Approving, sharing, inviting and managing tokens happen only in the web UI (R33), so a prompt-injected agent cannot approve content it planted, even with an admin's token.

Every result is labelled `"kind": "retrieved_data"` with a note to treat it as data, not instructions. Every call is written to the workspace audit trail with the token's id; arguments the tool does not declare (for example an injected `user_id`) are ignored and recorded as attempted overrides.

## Limitations

- **Not MCP OAuth-profile interoperable.** The MCP 2025-11-25 authorization profile expects tokens issued by an OAuth authorization server, discovered through protected-resource metadata. Lattice validates its own personal access tokens instead: the bearer header format is standard, but the discovery flow is not implemented. Adding an OAuth authorization server is deferred.
- **Revocation stops future access only.** Revoking a token, withdrawing a document or removing a person takes effect on the next request; text an agent already received cannot be recalled. The audit trail records what was delivered.
