// The one client for the same-origin API (KTD12). The server stays the authority.
let csrfToken = ''

export function setCsrfToken(token: string) {
  csrfToken = token
}

export class ApiError extends Error {
  status: number
  body: unknown
  constructor(status: number, message: string, body?: unknown) {
    super(message)
    this.status = status
    this.body = body
  }
}

export const NOT_FOUND = 'Not found or not permitted.'

export async function api<T = unknown>(
  path: string,
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' = 'GET',
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = {}
  // FormData goes as-is so the browser sets the multipart boundary.
  const form = body instanceof FormData
  if (body !== undefined && !form) headers['Content-Type'] = 'application/json'
  if (method !== 'GET') headers['X-CSRF-Token'] = csrfToken
  const res = await fetch(path, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined || form ? (body as FormData | undefined) : JSON.stringify(body),
  })
  if (res.ok) return (res.status === 204 ? undefined : await res.json()) as T
  const refusal = res.status === 404 || res.status >= 500 ? undefined : await res.json().catch(() => undefined)
  throw new ApiError(res.status, errorMessage(res.status, refusal?.detail), refusal)
}

function errorMessage(status: number, detail: unknown): string {
  if (status === 404) return NOT_FOUND
  // The request and its audit row commit together, so a server error changed nothing (R34).
  if (status >= 500) return 'The server could not complete and record this action, so nothing changed. Try again.'
  if (typeof detail === 'string') return detail
  return status === 401 ? 'Please sign in.' : `The request was refused (${status}).`
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export const ws = (workspaceId: string) => `/api/workspaces/${workspaceId}`
