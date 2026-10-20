import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, postStream, setCsrfToken } from './api'

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

  it('sends FormData as-is with no Content-Type but with X-CSRF-Token', async () => {
    setCsrfToken('csrf-abc')
    const fetchMock = mockFetch(201, { id: 'd1' })
    const form = new FormData()
    form.append('file', new File(['# Hi'], 'hi.md'))
    await api('/api/x', 'POST', form)
    const init = fetchMock.mock.calls[0][1]
    expect(new Headers(init.headers).has('Content-Type')).toBe(false)
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('csrf-abc')
    expect(init.body).toBe(form)
  })
})

describe('postStream', () => {
  it('yields events whose frames are split across chunks, posting with the CSRF header', async () => {
    setCsrfToken('csrf-abc')
    const chunks = ['event: passages\ndata: {"passages":[]', ',"left_out":2}\n\nevent: sen', 'tence\ndata: {"text":"Héllo"}\n\n', 'event: done\ndata: {}\n\n']
    const bytes = new TextEncoder().encode(chunks.join(''))
    const cut = bytes.indexOf(0xc3) + 1 // inside the two bytes of é, after a frame split mid-JSON
    const fetchMock = vi.fn(
      async (_path: string, _init: RequestInit) =>
        new Response(
          new ReadableStream({
            start(c) {
              c.enqueue(bytes.slice(0, 30))
              c.enqueue(bytes.slice(30, cut))
              c.enqueue(bytes.slice(cut))
              c.close()
            },
          }),
        ),
    )
    vi.stubGlobal('fetch', fetchMock)
    const events = []
    for await (const e of postStream('/api/x', { question: 'q' })) events.push(e)
    expect(events).toEqual([
      { event: 'passages', data: { passages: [], left_out: 2 } },
      { event: 'sentence', data: { text: 'Héllo' } },
      { event: 'done', data: {} },
    ])
    const init = fetchMock.mock.calls[0][1]
    expect(init.method).toBe('POST')
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('csrf-abc')
    expect(init.credentials).toBe('same-origin')
    expect(init.body).toBe('{"question":"q"}')
  })

  it('throws the refusal detail when the turn is refused before streaming', async () => {
    mockFetch(409, { detail: 'An answer is already in progress' })
    await expect(postStream('/api/x', {}).next()).rejects.toThrow('An answer is already in progress')
  })
})
