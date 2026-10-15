import { type FormEvent, useState } from 'react'
import { api } from '../api'
import { useAction, useLoad, useWorkspace } from '../session'
import { LoadState, Message } from '../ui'

type Group = { id: string; name: string; members?: { user_id: string; name: string }[] }

export default function Groups() {
  const { base, manager } = useWorkspace()
  const groups = useLoad<{ groups: Group[] }>(`${base}/groups`)
  const people = useLoad<{ members: { user_id: string; name: string }[] }>(manager ? `${base}/members` : null)
  const { message, run } = useAction()
  const [name, setName] = useState('')

  const act = async (action: () => Promise<unknown>, done: string) => {
    const ok = await run(action, done)
    groups.reload()
    return ok
  }
  const create = async (e: FormEvent) => {
    e.preventDefault()
    if (await act(() => api(`${base}/groups`, 'POST', { name }), 'Group created.')) setName('')
  }

  return (
    <>
      <h1>Groups</h1>
      <Message text={message} />
      {manager && (
        <form onSubmit={create} className="card">
          <label>
            New group name
            <input required value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <button type="submit">Create group</button>
        </form>
      )}
      <LoadState load={groups} empty={!groups.data?.groups.length} emptyText="No groups yet." />
      {groups.data?.groups.map((g) => (
        <section key={g.id} className="card">
          <h2>{g.name}</h2>
          {manager && <GroupManager group={g} people={people.data?.members ?? []} act={act} base={base} />}
        </section>
      ))}
    </>
  )
}

function GroupManager({
  group,
  people,
  act,
  base,
}: {
  group: Group
  people: { user_id: string; name: string }[]
  act: (action: () => Promise<unknown>, done: string) => Promise<boolean>
  base: string
}) {
  const [rename, setRename] = useState(group.name)
  const [adding, setAdding] = useState('')
  const path = `${base}/groups/${group.id}`
  const inGroup = new Set(group.members?.map((m) => m.user_id))
  return (
    <>
      <ul>
        {group.members?.map((m) => (
          <li key={m.user_id}>
            {m.name}{' '}
            <button type="button" onClick={() => act(() => api(`${path}/members/${m.user_id}`, 'DELETE'), `${m.name} removed from the group.`)}>
              Remove from group
            </button>
          </li>
        ))}
      </ul>
      {!group.members?.length && <p>No members.</p>}
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (adding) void act(() => api(`${path}/members/${adding}`, 'PUT'), 'Member added to the group.')
        }}
      >
        <label>
          Add person
          <select value={adding} onChange={(e) => setAdding(e.target.value)}>
            <option value="">Choose…</option>
            {people
              .filter((p) => !inGroup.has(p.user_id))
              .map((p) => (
                <option key={p.user_id} value={p.user_id}>
                  {p.name}
                </option>
              ))}
          </select>
        </label>
        <button type="submit">Add to group</button>
      </form>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void act(() => api(path, 'PATCH', { name: rename }), 'Group renamed.')
        }}
      >
        <label>
          Rename group
          <input required value={rename} onChange={(e) => setRename(e.target.value)} />
        </label>
        <button type="submit">Rename</button>
      </form>
      <button
        type="button"
        onClick={() =>
          window.confirm(`Delete group "${group.name}"? Every document shared with it loses that share.`) &&
          act(() => api(path, 'DELETE'), 'Group deleted.')
        }
      >
        Delete group
      </button>
    </>
  )
}
