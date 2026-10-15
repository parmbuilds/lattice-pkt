import { type FormEvent, useState } from 'react'
import { Link, useParams } from 'react-router'
import { api, ApiError, errorText } from '../api'
import { type DocumentSummary, documentState, useAction, useLoad, useWorkspace, when } from '../session'
import { LoadState, Message } from '../ui'

type Detail = Omit<DocumentSummary, 'author_name'> & {
  title: string
  body: string
  shown_version_id: string
  versions?: { id: string; title: string; created_at: string }[]
  shared_with?: { id: string; name: string }[]
  last_decision?: { decision: string; reason: string | null; version_id: string } | null
}

export default function DocumentDetail() {
  const { documentId = '' } = useParams()
  const { base, manager, userId } = useWorkspace()
  const path = `${base}/documents/${documentId}`
  const load = useLoad<Detail>(path)
  const { message, run } = useAction()
  const [editing, setEditing] = useState(false)
  const [viewing, setViewing] = useState<{ id: string; title: string; body: string } | null>(null)
  const doc = load.data

  const act = async (action: () => Promise<unknown>, done: string, refused?: (e: unknown) => string) => {
    const ok = await run(action, done, refused)
    load.reload()
    return ok
  }

  if (!doc) return <LoadState load={load} empty={false} emptyText="" />
  const author = doc.author_id === userId
  const showsDraft = doc.shown_version_id === doc.pending_version_id
  return (
    <>
      <p>
        <Link to=".." relative="path">
          Back to documents
        </Link>
      </p>
      <h1>{doc.title}</h1>
      <p>
        State: {documentState(doc)}. {showsDraft ? 'Showing the draft awaiting approval.' : 'Showing the approved version.'}
      </p>
      {doc.last_decision && (
        <p>
          Last decision: {doc.last_decision.decision}
          {doc.last_decision.reason && <>. Reason: {doc.last_decision.reason}</>}
        </p>
      )}
      <Message text={message} />
      <pre className="doc-body">{doc.body}</pre>

      <div className="actions">
        {author && !doc.withdrawn && (
          <button type="button" onClick={() => setEditing(!editing)}>
            {editing ? 'Cancel edit' : 'Edit (creates a new draft)'}
          </button>
        )}
        {author && doc.pending_version_id && (
          <button
            type="button"
            onClick={() =>
              act(() => api(`${path}/discard`, 'POST', { version_id: doc.pending_version_id }), 'Draft discarded.')
            }
          >
            Discard draft
          </button>
        )}
        {manager && doc.pending_version_id && <Link to="../../approvals" relative="path">Review in Approvals</Link>}
        {(author || manager) && !doc.withdrawn && (
          <button
            type="button"
            onClick={() =>
              window.confirm('Withdraw this document? Withdrawal is permanent in this version.') &&
              act(() => api(`${path}/withdraw`, 'POST'), 'Document withdrawn.')
            }
          >
            Withdraw
          </button>
        )}
      </div>

      {editing && (
        <EditForm
          doc={doc}
          onSave={async (title, body) => {
            if (await act(() => api(path, 'PUT', { title, body }), 'New draft saved; it awaits approval.')) setEditing(false)
          }}
        />
      )}

      {doc.shared_with && <Sharing doc={doc} path={path} base={base} act={act} />}
      {manager && <Reassign path={path} base={base} act={act} authorId={doc.author_id} />}

      {doc.versions && (
        <section>
          <h2>Version history</h2>
          <ul>
            {doc.versions.map((v) => (
              <li key={v.id}>
                {v.title} ({when(v.created_at)}){v.id === doc.serving_version_id && ', approved'}
                {v.id === doc.pending_version_id && ', draft'}{' '}
                <button
                  type="button"
                  onClick={() =>
                    run(async () => setViewing(await api<{ id: string; title: string; body: string }>(`${path}/versions/${v.id}`)))
                  }
                >
                  View
                </button>
              </li>
            ))}
          </ul>
          {viewing && (
            <>
              <h3>Version: {viewing.title}</h3>
              <pre className="doc-body">{viewing.body}</pre>
            </>
          )}
        </section>
      )}
    </>
  )
}

type Act = (action: () => Promise<unknown>, done: string, refused?: (e: unknown) => string) => Promise<boolean>

function EditForm({ doc, onSave }: { doc: Detail; onSave: (title: string, body: string) => void }) {
  const [title, setTitle] = useState(doc.title)
  const [body, setBody] = useState(doc.body)
  const submit = (e: FormEvent) => {
    e.preventDefault()
    onSave(title, body)
  }
  return (
    <form onSubmit={submit} className="card">
      <label>
        Title
        <input required value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label>
        Text
        <textarea required rows={14} value={body} onChange={(e) => setBody(e.target.value)} />
      </label>
      <button type="submit">Save draft</button>
    </form>
  )
}

const shareRefused = (e: unknown) =>
  e instanceof ApiError && e.status === 404
    ? 'Sharing refused: the group does not exist, or you can only share with groups you belong to.'
    : errorText(e)

function Sharing({ doc, path, base, act }: { doc: Detail; path: string; base: string; act: Act }) {
  const groups = useLoad<{ groups: { id: string; name: string }[] }>(`${base}/groups`)
  const [groupId, setGroupId] = useState('')
  const shared = new Set(doc.shared_with?.map((g) => g.id))
  return (
    <section>
      <h2>Shared with</h2>
      {doc.shared_with?.length ? (
        <ul>
          {doc.shared_with.map((g) => (
            <li key={g.id}>
              {g.name}{' '}
              <button type="button" onClick={() => act(() => api(`${path}/shares/${g.id}`, 'DELETE'), 'Share removed.', shareRefused)}>
                Unshare
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p>Not shared with any group.</p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (groupId) void act(() => api(`${path}/shares/${groupId}`, 'PUT'), 'Shared.', shareRefused)
        }}
      >
        <label>
          Share with group
          <select value={groupId} onChange={(e) => setGroupId(e.target.value)}>
            <option value="">Choose…</option>
            {groups.data?.groups
              .filter((g) => !shared.has(g.id))
              .map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
          </select>
        </label>
        <button type="submit">Share</button>
      </form>
    </section>
  )
}

function Reassign({ path, base, act, authorId }: { path: string; base: string; act: Act; authorId: string }) {
  const people = useLoad<{ members: { user_id: string; name: string }[] }>(`${base}/members`)
  const [userId, setUserId] = useState('')
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (userId) void act(() => api(`${path}/author`, 'POST', { user_id: userId }), 'Author changed.')
      }}
    >
      <label>
        Reassign author to
        <select value={userId} onChange={(e) => setUserId(e.target.value)}>
          <option value="">Choose…</option>
          {people.data?.members
            .filter((p) => p.user_id !== authorId)
            .map((p) => (
              <option key={p.user_id} value={p.user_id}>
                {p.name}
              </option>
            ))}
        </select>
      </label>
      <button type="submit">Reassign author</button>
    </form>
  )
}
