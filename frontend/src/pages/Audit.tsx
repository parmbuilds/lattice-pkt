import { type FormEvent, useState } from 'react'
import { useLoad, useWorkspace, when } from '../session'
import { LoadState } from '../ui'

type AuditEvent = {
  id: number
  occurred_at: string
  actor_user_id: string | null
  channel: string
  token_id: string | null
  action: string
  outcome: 'allow' | 'deny'
  rule_id: string
  inputs: unknown
  document_id: string | null
  version_id: string | null
  returned_version_ids: string[]
  detail: unknown
}

export default function Audit() {
  const { base } = useWorkspace()
  const people = useLoad<{ members: { user_id: string; name: string }[] }>(`${base}/members`)
  const [filters, setFilters] = useState({ user_id: '', action: '', outcome: '', limit: '100' })
  const [query, setQuery] = useState('')
  const events = useLoad<{ events: AuditEvent[] }>(`${base}/audit?${query}`)
  const names = new Map(people.data?.members.map((m) => [m.user_id, m.name]))

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const next = new URLSearchParams(Object.entries(filters).filter(([, v]) => v)).toString()
    if (next === query) events.reload()
    setQuery(next)
  }
  const set = (key: keyof typeof filters) => (e: { target: { value: string } }) =>
    setFilters({ ...filters, [key]: e.target.value })

  return (
    <>
      <h1>Audit trail</h1>
      <form onSubmit={submit} className="filters">
        <label>
          Person
          <select value={filters.user_id} onChange={set('user_id')}>
            <option value="">Anyone</option>
            {people.data?.members.map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Action
          <input value={filters.action} onChange={set('action')} />
        </label>
        <label>
          Outcome
          <select value={filters.outcome} onChange={set('outcome')}>
            <option value="">Any</option>
            <option value="allow">allow</option>
            <option value="deny">deny</option>
          </select>
        </label>
        <label>
          Limit
          <input type="number" min={1} max={500} value={filters.limit} onChange={set('limit')} />
        </label>
        <button type="submit">Filter</button>
      </form>
      <div aria-live="polite">
        <LoadState load={events} empty={!events.data?.events.length} emptyText="No audit events match." />
      </div>
      {!!events.data?.events.length && (
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Who</th>
              <th>Channel</th>
              <th>Action</th>
              <th>Outcome</th>
              <th>Rule</th>
              <th>Target</th>
              <th>Inputs</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {events.data.events.map((e) => (
              <tr key={e.id}>
                <td>{when(e.occurred_at)}</td>
                <td>{e.actor_user_id ? (names.get(e.actor_user_id) ?? e.actor_user_id) : '—'}</td>
                <td>
                  {e.channel}
                  {e.token_id && ` (token ${e.token_id})`}
                </td>
                <td>{e.action}</td>
                <td>{e.outcome}</td>
                <td>{e.rule_id}</td>
                <td>
                  {e.document_id && <div>Document {e.document_id}</div>}
                  {e.version_id && <div>Version {e.version_id}</div>}
                  {e.returned_version_ids.length > 0 && <div>Returned {e.returned_version_ids.join(', ')}</div>}
                </td>
                <td>
                  <pre>{JSON.stringify(e.inputs, null, 2)}</pre>
                </td>
                <td>
                  <pre>{JSON.stringify(e.detail, null, 2)}</pre>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}
