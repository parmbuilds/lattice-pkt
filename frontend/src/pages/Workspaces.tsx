import { type FormEvent, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { api } from '../api'
import { useAction, useSession } from '../session'
import { Message } from '../ui'

export default function Workspaces() {
  const { me, refresh } = useSession()
  const navigate = useNavigate()
  const { message, run } = useAction()
  const [name, setName] = useState('')
  const create = async (e: FormEvent) => {
    e.preventDefault()
    let id = ''
    const ok = await run(async () => {
      id = (await api<{ id: string }>('/api/workspaces', 'POST', { name })).id
    })
    if (ok) {
      await refresh()
      navigate(`/w/${id}/documents`)
    }
  }
  return (
    <>
      <h1>Your workspaces</h1>
      {me?.memberships.length ? (
        <ul>
          {me.memberships.map((m) => (
            <li key={m.workspace_id}>
              <Link to={`/w/${m.workspace_id}/documents`}>{m.workspace_name}</Link> ({m.role})
            </li>
          ))}
        </ul>
      ) : (
        <p>You are not in any workspace yet. Create one, or open an invite link.</p>
      )}
      <form onSubmit={create} className="card">
        <h2>Create a workspace</h2>
        <label>
          Workspace name
          <input required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <button type="submit">Create workspace</button>
      </form>
      <Message text={message} />
    </>
  )
}
