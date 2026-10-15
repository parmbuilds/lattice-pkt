import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router'
import { api, errorText, ws } from './api'

export type Role = 'owner' | 'admin' | 'member'
export type Membership = { workspace_id: string; workspace_name: string; role: Role }
export type Me = {
  user: { id: string; email: string; name: string }
  csrf_token: string
  memberships: Membership[]
}

// `me` is undefined while loading and null when signed out.
export const SessionContext = createContext<{ me: Me | null | undefined; refresh: () => Promise<void> }>({
  me: undefined,
  refresh: async () => {},
})

export const useSession = () => useContext(SessionContext)

// The role comes from /api/auth/me, re-fetched on every page load, so a demotion shows at once.
export function useWorkspace() {
  const { workspaceId = '' } = useParams()
  const { me } = useSession()
  const role = me?.memberships.find((m) => m.workspace_id === workspaceId)?.role
  return {
    workspaceId,
    base: ws(workspaceId),
    role,
    manager: role === 'owner' || role === 'admin',
    userId: me?.user.id ?? '',
  }
}

export type Loaded<T> = { data?: T; error?: string; loading: boolean; reload: () => void }

export function useLoad<T>(path: string | null): Loaded<T> {
  const [count, setCount] = useState(0)
  const key = path === null ? null : `${count}:${path}`
  // A reload keeps the path's earlier data visible; a new path (another workspace or query) does not.
  const [state, setState] = useState<{ key?: string; path?: string; data?: T; error?: string }>({})
  useEffect(() => {
    if (path === null || key === null) return
    let live = true
    api<T>(path).then(
      (data) => live && setState({ key, path, data }),
      (e) => live && setState({ key, path, error: errorText(e) }),
    )
    return () => {
      live = false
    }
  }, [path, key])
  const reload = useCallback(() => setCount((c) => c + 1), [])
  const current = state.path === path
  return {
    data: current ? state.data : undefined,
    error: current ? state.error : undefined,
    loading: key !== null && state.key !== key,
    reload,
  }
}

// Runs an action and keeps one message for the page's aria-live region.
export function useAction() {
  const [message, setMessage] = useState('')
  const busy = useRef(false)
  const run = async (
    action: () => Promise<unknown>,
    done = '',
    refused: (e: unknown) => string = errorText,
  ) => {
    if (busy.current) return false // a double click must not submit twice
    busy.current = true
    setMessage('')
    try {
      await action()
      setMessage(done)
      return true
    } catch (e) {
      setMessage(refused(e))
      return false
    } finally {
      busy.current = false
    }
  }
  return { message, setMessage, run }
}

export const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : '—')

export type DocumentSummary = {
  id: string
  title: string | null
  author_id: string
  author_name: string
  withdrawn: boolean
  serving_version_id: string | null
  pending_version_id: string | null
}

export function documentState(d: Pick<DocumentSummary, 'withdrawn' | 'serving_version_id' | 'pending_version_id'>) {
  if (d.withdrawn) return 'Withdrawn'
  const parts = [d.serving_version_id && 'Approved', d.pending_version_id && 'Draft awaiting approval']
  return parts.filter(Boolean).join(' + ') || 'No version'
}
