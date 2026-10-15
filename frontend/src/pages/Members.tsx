import { type FormEvent, useState } from 'react'
import { api, ApiError, errorText } from '../api'
import { type Role, useAction, useLoad, useSession, useWorkspace, when } from '../session'
import { LoadState, Message } from '../ui'

type Member = { user_id: string; name: string; email: string; role: Role }
type Invite = { id: string; role: Role; prefix: string; inviter_id: string; expires_at: string }

export default function Members() {
  const { base, role, userId } = useWorkspace()
  const { refresh } = useSession()
  const members = useLoad<{ members: Member[] }>(`${base}/members`)
  const invites = useLoad<{ invites: Invite[] }>(`${base}/invites`)
  const { message, run } = useAction()
  const [inviteRole, setInviteRole] = useState<Role>('member')
  const [inviteUrl, setInviteUrl] = useState('')
  const owner = role === 'owner'

  const act = async (action: () => Promise<unknown>, done: string) => {
    if (await run(action, done)) {
      members.reload()
      await refresh() // our own role may have changed (transfer)
    }
  }
  const remove = (m: Member) =>
    window.confirm(`Remove ${m.name} from this workspace? They lose access on their next request.`) &&
    act(() => api(`${base}/members/${m.user_id}`, 'DELETE'), `${m.name} was removed.`)
  const transfer = (m: Member) =>
    window.confirm(`Make ${m.name} the owner? You become an admin and cannot undo this yourself.`) &&
    act(() => api(`${base}/transfer`, 'POST', { user_id: m.user_id }), `${m.name} is now the owner.`)
  const changeRole = (m: Member, next: Role) =>
    act(() => api(`${base}/members/${m.user_id}`, 'PATCH', { role: next }), `${m.name} is now ${next}.`)

  const createInvite = async (e: FormEvent) => {
    e.preventDefault()
    setInviteUrl('')
    await run(
      async () => {
        setInviteUrl((await api<{ invite_url: string }>(`${base}/invites`, 'POST', { role: inviteRole })).invite_url)
        invites.reload()
      },
      'Invite created. Copy the link and send it to the person.',
      (err) =>
        err instanceof ApiError && err.status === 404
          ? 'Invite refused: your role cannot invite with that role.'
          : errorText(err),
    )
  }

  return (
    <>
      <h1>Members</h1>
      <Message text={message} />
      <LoadState load={members} empty={!members.data?.members.length} emptyText="No members." />
      {!!members.data?.members.length && (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Role</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {members.data.members.map((m) => (
              <tr key={m.user_id}>
                <td>{m.name}</td>
                <td>{m.email}</td>
                <td>
                  {owner && m.role !== 'owner' ? (
                    <select
                      aria-label={`Role for ${m.name}`}
                      value={m.role}
                      onChange={(e) => changeRole(m, e.target.value as Role)}
                    >
                      <option value="member">member</option>
                      <option value="admin">admin</option>
                    </select>
                  ) : (
                    m.role
                  )}
                </td>
                <td>
                  {m.user_id !== userId && m.role !== 'owner' && (owner || m.role === 'member') && (
                    <button type="button" onClick={() => remove(m)}>
                      Remove
                    </button>
                  )}
                  {owner && m.role === 'admin' && (
                    <button type="button" onClick={() => transfer(m)}>
                      Transfer ownership
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Invite someone</h2>
      <form onSubmit={createInvite} className="card">
        <label>
          Role for the invitee
          <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value as Role)}>
            <option value="member">member</option>
            {owner && <option value="admin">admin</option>}
          </select>
        </label>
        <button type="submit">Create invite link</button>
      </form>
      {inviteUrl && (
        <p>
          <label>
            Invite link (single use)
            <input readOnly value={inviteUrl} onFocus={(e) => e.target.select()} />
          </label>{' '}
          <button type="button" onClick={() => navigator.clipboard?.writeText(inviteUrl)}>
            Copy link
          </button>
        </p>
      )}

      <h2>Pending invites</h2>
      <LoadState load={invites} empty={!invites.data?.invites.length} emptyText="No pending invites." />
      {!!invites.data?.invites.length && (
        <ul>
          {invites.data.invites.map((i) => (
            <li key={i.id}>
              {i.prefix}… as {i.role}, expires {when(i.expires_at)}{' '}
              <button
                type="button"
                onClick={() =>
                  run(() => api(`${base}/invites/${i.id}`, 'DELETE'), 'Invite cancelled.').then(invites.reload)
                }
              >
                Cancel invite
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}
