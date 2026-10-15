import { useState } from 'react'
import { Link } from 'react-router'
import { api, ApiError, errorText, NOT_FOUND } from '../api'
import { type DocumentSummary, useAction, useLoad, useWorkspace } from '../session'
import { LoadState, Message } from '../ui'

type Review = {
  document_id: string
  pending_version_id: string
  serving_version_id: string | null
  author_id: string
  title: string
  diff: string[]
}

const staleRefusal = (e: unknown) =>
  e instanceof ApiError && e.status === 409
    ? 'Refused: this draft was replaced or already decided. The current pending drafts have been reloaded.'
    : errorText(e)

function diffClass(line: string) {
  if (line.startsWith('+')) return 'add'
  if (line.startsWith('-')) return 'del'
  return undefined
}

export default function Approvals() {
  const { base, manager } = useWorkspace()
  const docs = useLoad<{ documents: DocumentSummary[] }>(manager ? `${base}/documents` : null)
  const { message, run } = useAction()
  const pending = docs.data?.documents.filter((d) => d.pending_version_id) ?? []

  // Reload after every decision, refused or not, so a replaced draft is fetched again.
  const decide = async (action: () => Promise<unknown>, done: string) => {
    await run(action, done, staleRefusal)
    docs.reload()
  }

  // Reviewing is for owners and admins; a member's own drafts are on the Documents page.
  if (!manager) return <><h1>Approvals</h1><Message text={NOT_FOUND} /></>

  return (
    <>
      <h1>Approvals</h1>
      <Message text={message} />
      <LoadState load={docs} empty={!pending.length} emptyText="No drafts are waiting for approval." />
      {pending.map((d) => (
        // Keyed by the draft id: a replaced draft remounts and loads its own review.
        <ReviewCard key={`${d.id}:${d.pending_version_id}`} doc={d} base={base} decide={decide} />
      ))}
    </>
  )
}

function ReviewCard({
  doc,
  base,
  decide,
}: {
  doc: DocumentSummary
  base: string
  decide: (action: () => Promise<unknown>, done: string) => Promise<void>
}) {
  const path = `${base}/documents/${doc.id}`
  const review = useLoad<Review>(`${path}/review`)
  const [reason, setReason] = useState('')
  const r = review.data
  return (
    <section className="card">
      <h2>
        <Link to={`../documents/${doc.id}`} relative="path">
          {r?.title ?? doc.title}
        </Link>
      </h2>
      <p>
        By {doc.author_name}. {doc.serving_version_id ? 'Replaces the approved version.' : 'First version.'}
      </p>
      <LoadState load={review} empty={!r?.diff.length} emptyText="No text changes." />
      {r && (
        <>
          <pre className="diff" aria-label="Changes against the approved version">
            {r.diff.map((line, i) => (
              <span key={i} className={diffClass(line)}>
                {line}
                {'\n'}
              </span>
            ))}
          </pre>
          <div className="actions">
            <button
              type="button"
              onClick={() =>
                decide(
                  () => api(`${path}/approve`, 'POST', { version_id: r.pending_version_id }),
                  `Approved "${r.title}"; it is now the serving version.`,
                )
              }
            >
              Approve
            </button>
            <label>
              Rejection reason (optional)
              <input value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} />
            </label>
            <button
              type="button"
              onClick={() =>
                decide(
                  () => api(`${path}/reject`, 'POST', { version_id: r.pending_version_id, reason: reason || null }),
                  `Rejected "${r.title}"; the approved version keeps serving.`,
                )
              }
            >
              Reject
            </button>
          </div>
        </>
      )}
    </section>
  )
}
