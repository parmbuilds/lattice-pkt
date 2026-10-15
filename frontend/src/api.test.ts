import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, setCsrfToken } from './api'

function mockFetch(status: number, body?: unknown) {
  const fetchMock = vi.fn(
    async (_path: string, _init: RequestInit) =>
      new Response(body === undefined ? null : JSON.stringify(body), { status }),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => vi.unstubAllGlobals())

describe('api', () => {
  it('sends X-CSRF-Token on POST, PUT, PATCH and DELETE but not on GET', async () => {
    setCsrfToken('csrf-abc')
    for (const method of ['POST', 'PUT', 'PATCH', 'DELETE'] as const) {
      const fetchMock = mockFetch(204)
      await api('/api/x', method, {})
      const init = fetchMock.mock.calls[0][1]
      expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('csrf-abc')
      expect(init.credentials).toBe('same-origin')
    }
    const fetchMock = mockFetch(200, { ok: true })
    await api('/api/x')
    const init = fetchMock.mock.calls[0][1]
    expect(new Headers(init.headers).has('X-CSRF-Token')).toBe(false)
    expect(init.credentials).toBe('same-origin')
  })

  it('turns 404 into "Not found or not permitted." and 409 into its detail', async () => {
    mockFetch(404, { detail: 'something else' })
    await expect(api('/api/x')).rejects.toThrow('Not found or not permitted.')
    mockFetch(409, { detail: 'The draft was replaced or already decided.' })
    await expect(api('/api/x', 'POST', {})).rejects.toThrow(
      'The draft was replaced or already decided.',
    )
  })
})
