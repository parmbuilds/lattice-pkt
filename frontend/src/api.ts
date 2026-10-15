// The one client for the same-origin API (KTD12). The server stays the authority.
let csrfToken = ''

export function setCsrfToken(token: string) {
  csrfToken = token
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export const NOT_FOUND = 'Not found or not permitted.'

export async function api<T = unknown>(
  path: string,
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' = 'GET',
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET') headers['X-CSRF-Token'] = csrfToken
  const res = await fetch(path, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (res.ok) return (res.status === 204 ? undefined : await res.json()) as T
  throw new ApiError(res.status, await errorMessage(res))
}

async function errorMessage(res: Response): Promise<string> {
  if (res.status === 404) return NOT_FOUND
  // The request and its audit row commit together, so a server error changed nothing (R34).
  if (res.status >= 500) return 'The server could not complete and record this action, so nothing changed. Try again.'
  const detail = await res.json().then((b) => b?.detail, () => undefined)
  if (typeof detail === 'string') return detail
  return res.status === 401 ? 'Please sign in.' : `The request was refused (${res.status}).`
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export const ws = (workspaceId: string) => `/api/workspaces/${workspaceId}`
