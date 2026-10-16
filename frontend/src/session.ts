import { reactive, computed, ref, shallowReactive, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, ApiError, errorText, setCsrfToken, ws } from './api'

export type Role = 'owner' | 'admin' | 'member'
export type Membership = { workspace_id: string; workspace_name: string; role: Role }
export type Me = {
  user: { id: string; email: string; name: string }
  csrf_token: string
  memberships: Membership[]
}

// `me` is undefined while loading and null when signed out.
export const me = ref<Me | null | undefined>(undefined)

export async function refresh() {
  try {
    const fresh = await api<Me>('/api/auth/me')
    setCsrfToken(fresh.csrf_token)
    me.value = fresh
  } catch (e) {
    // Only a 401 means signed out; a transient failure keeps the page that is showing.
    if (e instanceof ApiError && e.status === 401) {
      setCsrfToken('')
      me.value = null
    } else if (me.value === undefined) me.value = null
  }
}

export async function signOut() {
  await api('/api/auth/signout', 'POST').catch(() => undefined)
  await refresh()
}

// The role comes from /api/auth/me, re-fetched on every navigation, so a demotion shows at once.
export function useWorkspace() {
  const route = useRoute()
  const workspaceId = computed(() => String(route.params.workspaceId ?? ''))
  const membership = computed(() => me.value?.memberships.find((m) => m.workspace_id === workspaceId.value))
  const role = computed(() => membership.value?.role)
  return reactive({
    workspaceId,
    workspaceName: computed(() => membership.value?.workspace_name ?? ''),
    base: computed(() => ws(workspaceId.value)),
    role,
    manager: computed(() => role.value === 'owner' || role.value === 'admin'),
    userId: computed(() => me.value?.user.id ?? ''),
  })
}

export type Loaded<T> = { data?: T; error?: string; loading: boolean; reload: () => void }

export function useLoad<T>(path: () => string | null): Loaded<T> {
  const count = ref(0)
  const state: Loaded<T> = shallowReactive({ data: undefined, error: undefined, loading: false, reload: () => count.value++ })
  let current: string | null = null
  watch(
    [path, count],
    ([p], _old, onCleanup) => {
      // A reload keeps the path's earlier data visible; a new path (another workspace or query) does not.
      if (p !== current) state.data = state.error = undefined
      current = p
      state.loading = p !== null
      if (p === null) return
      let live = true
      onCleanup(() => (live = false))
      api<T>(p).then(
        (data) => live && Object.assign(state, { data, error: undefined, loading: false }),
        (e) => live && Object.assign(state, { data: undefined, error: errorText(e), loading: false }),
      )
    },
    { immediate: true },
  )
  return state
}

// Runs an action and keeps one message for the page's aria-live region.
export function useAction() {
  const message = ref('')
  let busy = false
  const run = async (action: () => Promise<unknown>, done = '', refused: (e: unknown) => string = errorText) => {
    if (busy) return false // a double click must not submit twice
    busy = true
    message.value = ''
    try {
      await action()
      message.value = done
      return true
    } catch (e) {
      message.value = refused(e)
      return false
    } finally {
      busy = false
    }
  }
  return { message, run }
}

export const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—'

export type DocumentSummary = {
  id: string
  title: string | null
  author_id: string
  author_name: string
  withdrawn: boolean
  serving_version_id: string | null
  pending_version_id: string | null
}

export type StampKind =
  | 'approved'
  | 'draft'
  | 'withdrawn'
  | 'superseded'
  | 'rejected'
  | 'allow'
  | 'deny'
  | 'plain'
  | 'processing'
  | 'needs-ack'
export type StampSpec = { kind: StampKind; label: string }

export type Derivation = { status: 'pending' | 'ready' | 'failed'; error: string | null }

// Ready reuses the approved look and failed the rejected one; only "Preparing" is new.
export function derivationStamp(d: Derivation): StampSpec {
  if (d.status === 'ready') return { kind: 'approved', label: 'Ready' }
  if (d.status === 'failed') return { kind: 'rejected', label: 'Derivation failed' }
  return { kind: 'processing', label: 'Preparing' }
}

export function documentStamps(d: Pick<DocumentSummary, 'withdrawn' | 'serving_version_id' | 'pending_version_id'>): StampSpec[] {
  if (d.withdrawn) return [{ kind: 'withdrawn', label: 'Withdrawn' }]
  const stamps: StampSpec[] = []
  if (d.serving_version_id) stamps.push({ kind: 'approved', label: 'Approved' })
  if (d.pending_version_id) stamps.push({ kind: 'draft', label: d.serving_version_id ? 'Edit pending' : 'Draft' })
  return stamps.length ? stamps : [{ kind: 'plain', label: 'Not approved' }]
}

// Drawing-register revision letters: A, B, … Z, AA, AB, … from the version's position in history.
export function revLetter(index: number): string {
  let n = index + 1
  let out = ''
  while (n > 0) {
    out = String.fromCharCode(65 + ((n - 1) % 26)) + out
    n = Math.floor((n - 1) / 26)
  }
  return out
}

export const shortId = (id: string) => id.slice(0, 8)
