import { type FormEvent, useState } from 'react'
import { api } from '../api'
import { useAction, useLoad, useWorkspace, when } from '../session'
import { LoadState, Message } from '../ui'

type Token = {
  id: string
  name: string
  prefix: string
  created_at: string
  expires_at: string
  revoked: boolean
  last_used_at: string | null
}

export default function Tokens() {
  const { base } = useWorkspace()
  const tokens = useLoad<{ tokens: Token[] }>(`${base}/tokens`)
  const { message, setMessage, run } = useAction()
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  // The secret lives only in this state, from the create response; the list never carries it.
  const [secret, setSecret] = useState('')
  const endpoint = `${window.location.origin}/mcp`

  const create = async (e: FormEvent) => {
    e.preventDefault()
    setSecret('')
    const ok = await run(async () => setSecret((await api<{ token: string }>(`${base}/tokens`, 'POST', { name, password })).token))
    setPassword('')
    if (ok) {
      setName('')
      tokens.reload()
    }
  }
  const copy = () =>
    navigator.clipboard?.writeText(secret).then(
      () => setMessage('Token copied.'),
      () => setMessage('Copy failed; select the token and copy it by hand.'),
    )

  return (
    <>
      <h1>Personal access tokens</h1>
      <p>Tokens let your MCP agent search this workspace with your access. Agents can only search and read.</p>
      <p>
        MCP endpoint: <code>{endpoint}</code>
      </p>
      <p>Example client configuration:</p>
      <pre>{JSON.stringify({ mcpServers: { lattice: { type: 'http', url: endpoint, headers: { Authorization: 'Bearer <token>' } } } })}</pre>

      <form onSubmit={create} className="card">
        <h2>Create a token</h2>
        <label>
          Token name
          <input required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          Your password
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <button type="submit">Create token</button>
      </form>
      <Message text={message} />
      {secret && (
        <div className="card secret" role="alert">
          <p>
            <strong>Copy this token now. It will not be shown again.</strong>
          </p>
          <code>{secret}</code>{' '}
          <button type="button" onClick={copy}>
            Copy token
          </button>
        </div>
      )}

      <h2>Your tokens</h2>
      <LoadState load={tokens} empty={!tokens.data?.tokens.length} emptyText="No tokens yet." />
      {!!tokens.data?.tokens.length && (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Prefix</th>
              <th>Created</th>
              <th>Expires</th>
              <th>Last used</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {tokens.data.tokens.map((t) => (
              <tr key={t.id}>
                <td>{t.name}</td>
                <td>
                  <code>{t.prefix}…</code>
                </td>
                <td>{when(t.created_at)}</td>
                <td>{when(t.expires_at)}</td>
                <td>{when(t.last_used_at)}</td>
                <td>
                  {t.revoked ? (
                    'Revoked'
                  ) : (
                    <button
                      type="button"
                      onClick={() => run(() => api(`${base}/tokens/${t.id}`, 'DELETE'), 'Token revoked.').then(tokens.reload)}
                    >
                      Revoke {t.name}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}
