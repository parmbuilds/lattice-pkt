import { type FormEvent, useState } from 'react'
import { api, setCsrfToken } from '../api'
import { useAction, useSession } from '../session'
import { Message } from '../ui'

async function signIn(email: string, password: string) {
  const { csrf_token } = await api<{ csrf_token: string }>('/api/auth/signin', 'POST', { email, password })
  setCsrfToken(csrf_token)
}

export function SignIn({ onDone }: { onDone: () => void }) {
  const { refresh } = useSession()
  const { message, run } = useAction()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (await run(() => signIn(email, password))) {
      await refresh()
      onDone()
    }
  }
  return (
    <form onSubmit={submit} className="card">
      <h2>Sign in</h2>
      <label>
        Email
        <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
      </label>
      <label>
        Password
        <input
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      <button type="submit">Sign in</button>
      <Message text={message} />
    </form>
  )
}

export function SignUp({ onDone }: { onDone: () => void }) {
  const { refresh } = useSession()
  const { message, run } = useAction()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const ok = await run(async () => {
      await api('/api/auth/signup', 'POST', { email, password, name })
      await signIn(email, password)
    })
    if (ok) {
      await refresh()
      onDone()
    }
  }
  return (
    <form onSubmit={submit} className="card">
      <h2>Create an account</h2>
      <label>
        Name
        <input required autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label>
        Email
        <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
      </label>
      <label>
        Password (8 or more characters)
        <input
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          maxLength={256}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      <button type="submit">Create account</button>
      <Message text={message} />
    </form>
  )
}
