import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Component } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import ReviewCard from '../components/ReviewCard.vue'
import { me, type Role } from '../session'
import Approvals from './Approvals.vue'
import Audit from './Audit.vue'
import DocumentDetail from './DocumentDetail.vue'
import Documents from './Documents.vue'
import Entities from './Entities.vue'
import Groups from './Groups.vue'
import Search from './Search.vue'
import Tokens from './Tokens.vue'

const EVIL = '<img src=x onerror=alert(1)>'
let mounted: VueWrapper | undefined

// A route value that needs a status other than 200, or that changes per call.
class Reply {
  status: number
  body: unknown
  constructor(status: number, body: unknown) {
    this.status = status
    this.body = body
  }
}
type Handler = (init: RequestInit) => Reply

// Responses keyed by "METHOD /path" (query string ignored).
function mockApi(routes: Record<string, unknown>) {
  const fetchMock = vi.fn(async (path: string, init: RequestInit) => {
    const key = `${init.method ?? 'GET'} ${path.split('?')[0]}`
    if (!(key in routes)) return new Response(null, { status: 404 })
    let value = routes[key]
    if (typeof value === 'function') value = (value as Handler)(init)
    const reply = value instanceof Reply ? value : new Reply(200, value)
    return new Response(JSON.stringify(reply.body), { status: reply.status })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const DOC_DETAIL = '/w/:workspaceId/documents/:documentId'

async function renderPage(
  path: string,
  routePath: string,
  page: Component,
  role: Role = 'admin',
  extra: { path: string; component: Component }[] = [],
) {
  me.value = {
    user: { id: 'u1', email: 'ada@example.com', name: 'Ada' },
    csrf_token: 'csrf',
    memberships: [{ workspace_id: 'ws1', workspace_name: 'Acme', role }],
  }
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: routePath, component: page }, ...extra] })
  await router.push(path)
  mounted = mount(RouterView, { global: { plugins: [router] }, attachTo: document.body })
  await flushPromises()
  return mounted
}

const field = (w: VueWrapper, label: string) => w.findAll('label').find((l) => l.text().startsWith(label))!.find('input')

afterEach(() => {
  mounted?.unmount()
  mounted = undefined
  vi.unstubAllGlobals()
})

describe('Tokens page', () => {
  it('shows the secret only from the creation response, never after the list reloads', async () => {
    const listed = {
      id: 't1',
      name: 'laptop',
      prefix: 'lat_ab12',
      created_at: '2026-10-15T00:00:00Z',
      expires_at: '2099-01-13T00:00:00Z',
      revoked: false,
      last_used_at: null,
    }
    mockApi({
      'GET /api/workspaces/ws1/tokens': { tokens: [listed] },
      'POST /api/workspaces/ws1/tokens': { ...listed, token: 'lat_SECRET_VALUE' },
    })
    let w = await renderPage('/w/ws1/tokens', '/w/:workspaceId/tokens', Tokens, 'member')
    expect(document.body.textContent).toContain('laptop')
    expect(document.body.textContent).not.toContain('lat_SECRET_VALUE')

    await field(w, 'Token name').setValue('laptop')
    await field(w, 'Your password').setValue('pw123456')
    await w.findAll('form').find((f) => f.text().includes('Create token'))!.trigger('submit')
    await flushPromises()
    expect(document.body.textContent).toContain('lat_SECRET_VALUE')
    expect(document.body.textContent).toContain('will not be shown again')

    w.unmount()
    w = await renderPage('/w/ws1/tokens', '/w/:workspaceId/tokens', Tokens, 'member')
    expect(document.body.textContent).toContain('laptop')
    expect(document.body.textContent).not.toContain('lat_SECRET_VALUE')
  })
})

describe('user-supplied strings render as text', () => {
  it('shows an HTML-looking group name literally on the groups page', async () => {
    mockApi({
      'GET /api/workspaces/ws1/groups': { groups: [{ id: 'g1', name: EVIL, members: [] }] },
      'GET /api/workspaces/ws1/members': { members: [] },
    })
    await renderPage('/w/ws1/groups', '/w/:workspaceId/groups', Groups)
    expect(document.body.textContent).toContain(EVIL)
    expect(document.querySelector('img')).toBeNull()
  })

  it('shows an HTML-looking group name literally on the audit page', async () => {
    mockApi({
      'GET /api/workspaces/ws1/audit': {
        events: [
          {
            id: 1,
            occurred_at: '2026-10-15T00:00:00Z',
            actor_user_id: 'u1',
            channel: 'web',
            token_id: null,
            action: 'manage_groups',
            outcome: 'allow',
            rule_id: 'manager',
            inputs: { role: 'admin' },
            document_id: null,
            version_id: null,
            returned_version_ids: [],
            detail: { op: 'create', name: EVIL },
          },
        ],
      },
      'GET /api/workspaces/ws1/members': { members: [] },
    })
    await renderPage('/w/ws1/audit', '/w/:workspaceId/audit', Audit)
    expect(document.body.textContent).toContain('manage_groups')
    expect(document.body.textContent).toContain(EVIL)
    expect(document.querySelector('img')).toBeNull()
  })
})

describe('Search page', () => {
  it('wraps [[match]] markers in <mark> and drops the brackets', async () => {
    mockApi({
      'GET /api/workspaces/ws1/search': {
        results: [
          {
            version_id: 'v1',
            document_id: 'd1',
            title: 'Checkout guide',
            rank: 0.5,
            snippet: 'a [[checkout]] b',
            approved_at: '2026-10-15T00:00:00Z',
            approved_by: 'Arjun',
            spans: [],
            retrieval: 'lexical',
          },
        ],
      },
    })
    const w = await renderPage('/w/ws1/search', '/w/:workspaceId/search', Search, 'member')
    await field(w, 'Search approved documents').setValue('checkout')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(document.body.textContent).toContain('Checkout guide')
    const marks = document.querySelectorAll('mark')
    expect(marks).toHaveLength(1)
    expect(marks[0].textContent).toBe('checkout')
    expect(document.body.textContent).not.toContain('[[')
    expect(document.body.textContent).not.toContain(']]')
  })
})

const BASE = '/api/workspaces/ws1/documents'
const draft = {
  id: 'd1',
  title: 'Runbook',
  author_id: 'u2',
  author_name: 'Bo',
  withdrawn: false,
  serving_version_id: null,
  pending_version_id: 'v1',
}
const review = (over: Record<string, unknown> = {}) => ({
  document_id: 'd1',
  pending_version_id: 'v1',
  serving_version_id: null,
  author_id: 'u2',
  title: 'Runbook',
  diff: ['+hello'],
  receipt: null,
  intake: null,
  derivation: { status: 'ready', error: null },
  ...over,
})
const approveButton = () => [...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Approve')!
const fileInput = (w: VueWrapper, file: File) => {
  const input = w.find('input[type="file"]')
  Object.defineProperty(input.element, 'files', { value: [file] })
  return input.trigger('change')
}

async function mountReview(decide = vi.fn(async (action: () => Promise<unknown>) => void (await action()))) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: ReviewCard }] })
  await router.push('/')
  mounted = mount(ReviewCard, {
    props: { doc: draft, base: '/api/workspaces/ws1', workspaceId: 'ws1', decide },
    global: { plugins: [router] },
    attachTo: document.body,
  })
  await flushPromises()
  return mounted
}

describe('Documents upload', () => {
  const md = new File(['# Runbook'], 'runbook.md')

  it('posts the file and routes to the new draft', async () => {
    const fetchMock = mockApi({
      [`GET ${BASE}`]: { documents: [] },
      [`POST ${BASE}/upload`]: new Reply(201, { ...draft, created: true }),
    })
    const w = await renderPage('/w/ws1/documents', '/w/:workspaceId/documents', Documents, 'member', [
      { path: DOC_DETAIL, component: Documents },
    ])
    await w.findAll('button').find((b) => b.text() === 'Upload file')!.trigger('click')
    await fileInput(w, md)
    await w.find('form').trigger('submit')
    await flushPromises()
    const body = fetchMock.mock.calls.find(([p]) => p === `${BASE}/upload`)![1].body as FormData
    expect(body.get('file')).toBeInstanceOf(File)
    expect(w.vm.$router.currentRoute.value.fullPath).toBe('/w/ws1/documents/d1')
  })

  it('offers both choices on a title match and "Keep separate" re-posts with new_document=true', async () => {
    const posted: FormData[] = []
    mockApi({
      [`GET ${BASE}`]: { documents: [] },
      [`POST ${BASE}/upload`]: (init: RequestInit) => {
        posted.push(init.body as FormData)
        return posted.length === 1
          ? new Reply(409, { detail: 'You have a document with this title.', suggest_document_id: 'd9' })
          : new Reply(201, { ...draft, id: 'd2', created: true })
      },
    })
    const w = await renderPage('/w/ws1/documents', '/w/:workspaceId/documents', Documents, 'member', [
      { path: DOC_DETAIL, component: Documents },
    ])
    await w.findAll('button').find((b) => b.text() === 'Upload file')!.trigger('click')
    await fileInput(w, md)
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(document.body.textContent).toContain('Upload as new version')
    await w.findAll('button').find((b) => b.text() === 'Keep separate')!.trigger('click')
    await flushPromises()
    expect(posted[1].get('new_document')).toBe('true')
    expect(w.vm.$router.currentRoute.value.fullPath).toBe('/w/ws1/documents/d2')
  })

  it('routes a duplicate to the existing document with the already-uploaded message', async () => {
    mockApi({
      [`GET ${BASE}`]: { documents: [] },
      [`POST ${BASE}/upload`]: { ...draft, id: 'd7', created: false },
      [`GET ${BASE}/d7`]: { ...draft, id: 'd7', body: 'x', shown_version_id: 'v1' },
    })
    const w = await renderPage('/w/ws1/documents', '/w/:workspaceId/documents', Documents, 'member', [
      { path: DOC_DETAIL, component: DocumentDetail },
    ])
    await w.findAll('button').find((b) => b.text() === 'Upload file')!.trigger('click')
    await fileInput(w, md)
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.vm.$router.currentRoute.value.path).toBe('/w/ws1/documents/d7')
    expect(document.body.textContent).toContain('This file was already uploaded')
  })

  it('shows the uploaded file of the shown version as text', async () => {
    mockApi({
      [`GET ${BASE}/d7`]: {
        ...draft,
        id: 'd7',
        body: 'x',
        shown_version_id: 'v1',
        intakes: { v1: { filename: `${EVIL}.pdf`, detected_type: 'pdf', parser: 'pypdf', version: '6.20.1' } },
      },
    })
    await renderPage('/w/ws1/documents/d7', DOC_DETAIL, DocumentDetail, 'member')
    expect(document.body.textContent).toContain('Uploaded file')
    expect(document.body.textContent).toContain(`${EVIL}.pdf`)
    expect(document.body.textContent).toContain('read by pypdf 6.20.1')
    expect(document.querySelector('img')).toBeNull()
  })

  it("shows the server's reason for a 415", async () => {
    mockApi({
      [`GET ${BASE}`]: { documents: [] },
      [`POST ${BASE}/upload`]: new Reply(415, { detail: 'This file type is not supported.' }),
    })
    const w = await renderPage('/w/ws1/documents', '/w/:workspaceId/documents', Documents, 'member')
    await w.findAll('button').find((b) => b.text() === 'Upload file')!.trigger('click')
    await fileInput(w, new File(['x'], 'a.exe'))
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(document.body.textContent).toContain('This file type is not supported.')
  })
})

describe('ReviewCard', () => {
  const receipt = {
    flags: [{ kind: 'hidden_text', location: 'page 1', count: 1 }],
    notes: ['pdf_invisible_text_not_checked'],
    sha256: 'abc123',
    hidden_excerpts: [{ kind: 'hidden_text', location: 'page 1', text: EVIL }],
  }

  it('keeps Approve disabled until the flags are acknowledged, then sends the receipt hash', async () => {
    const fetchMock = mockApi({
      [`GET ${BASE}/d1/review`]: review({ receipt }),
      [`POST ${BASE}/d1/approve`]: { serving_version_id: 'v1' },
    })
    const w = await mountReview()
    expect(document.body.textContent).toContain('Needs acknowledgement')
    expect(document.body.textContent).toContain('pdf_invisible_text_not_checked')
    expect(approveButton().disabled).toBe(true)
    await w.find('input[type="checkbox"]').setValue(true)
    expect(approveButton().disabled).toBe(false)
    approveButton().click()
    await flushPromises()
    const init = fetchMock.mock.calls.find(([p]) => p === `${BASE}/d1/approve`)![1]
    expect(JSON.parse(init.body as string)).toEqual({ version_id: 'v1', acknowledged_receipt: 'abc123' })
  })

  it('keeps Approve disabled while the derivation is pending, then enables it once ready', async () => {
    let calls = 0
    mockApi({
      [`GET ${BASE}/d1/review`]: () =>
        review({ derivation: { status: calls++ === 0 ? 'pending' : 'ready', error: null } }),
    })
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mountReview()
      expect(document.body.textContent).toContain('Preparing')
      expect(approveButton().disabled).toBe(true)
      expect(document.body.textContent).toContain('Approve unlocks once the draft is ready for search.')
      await vi.advanceTimersByTimeAsync(2000) // no page reload: the card checks again by itself
      await flushPromises()
      expect(approveButton().disabled).toBe(false)
      expect(calls).toBe(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps checking for readiness after one failed request', async () => {
    let calls = 0
    mockApi({
      [`GET ${BASE}/d1/review`]: () => {
        calls++
        if (calls === 2) return new Reply(503, { detail: 'Try again.' })
        return review({ derivation: { status: calls === 1 ? 'pending' : 'ready', error: null } })
      },
    })
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mountReview()
      await vi.advanceTimersByTimeAsync(2000)
      await flushPromises()
      expect(document.body.textContent).toContain('Try again.')
      await vi.advanceTimersByTimeAsync(2000)
      await flushPromises()
      expect(approveButton().disabled).toBe(false)
      expect(calls).toBe(3)
    } finally {
      vi.useRealTimers()
    }
  })

  it('renders hidden content and an HTML-looking filename as text', async () => {
    mockApi({
      [`GET ${BASE}/d1/review`]: review({
        receipt,
        intake: { filename: `${EVIL}.html`, detected_type: 'html', parser: 'html' },
      }),
    })
    await mountReview()
    expect(document.body.textContent).toContain(`${EVIL}.html`)
    expect(document.body.textContent).toContain(EVIL)
    expect(document.querySelector('img')).toBeNull()
  })
})

describe('Approvals page', () => {
  it('shows the readiness message on 409 DERIVATIONS_NOT_READY', async () => {
    mockApi({
      [`GET ${BASE}`]: { documents: [draft] },
      [`GET ${BASE}/d1/review`]: review(),
      [`POST ${BASE}/d1/approve`]: new Reply(409, {
        detail: 'This draft is still being prepared for search. Try again shortly.',
      }),
    })
    await renderPage('/w/ws1/approvals', '/w/:workspaceId/approvals', Approvals)
    approveButton().click()
    await flushPromises()
    expect(document.body.textContent).toContain('Approve it once it shows Ready.')
  })
})

describe('Search page modes', () => {
  it('renders the span snippet and mode label for a vector-only hit', async () => {
    mockApi({
      'GET /api/workspaces/ws1/search': {
        results: [
          {
            version_id: 'v1',
            document_id: 'd1',
            title: 'Rollback runbook',
            rank: 0.01,
            snippet: 'Revert the deploy, then drain the queue.',
            approved_at: '2026-10-15T00:00:00Z',
            approved_by: null,
            spans: [{ start: 120, end: 160, sha256: 'h' }],
            retrieval: 'hybrid',
          },
        ],
      },
    })
    const w = await renderPage('/w/ws1/search', '/w/:workspaceId/search', Search, 'member')
    await field(w, 'Search approved documents').setValue('undo a release')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(document.body.textContent).toContain('Revert the deploy, then drain the queue.')
    expect(document.body.textContent).toContain('Keyword and meaning search')
    expect(document.body.textContent).toContain('from character 120')
  })
})

describe('Entities page', () => {
  const entity = { id: 'e1', name: 'Payments API', aliases: ['payapi'] }

  it('shows the add form to managers only', async () => {
    mockApi({ 'GET /api/workspaces/ws1/entities': { entities: [entity] } })
    await renderPage('/w/ws1/entities', '/w/:workspaceId/entities', Entities, 'admin')
    expect(document.body.textContent).toContain('Add an entity')
    mounted!.unmount()
    await renderPage('/w/ws1/entities', '/w/:workspaceId/entities', Entities, 'member')
    expect(document.body.textContent).toContain('Payments API')
    expect(document.body.textContent).not.toContain('Add an entity')
  })

  it('shows members with nothing visible an empty state', async () => {
    mockApi({ 'GET /api/workspaces/ws1/entities': { entities: [] } })
    await renderPage('/w/ws1/entities', '/w/:workspaceId/entities', Entities, 'member')
    expect(document.body.textContent).toContain('No entities are mentioned in documents you can read.')
  })

  it('lists mentions grouped by document with snippets and linked co-mentions', async () => {
    mockApi({
      'GET /api/workspaces/ws1/entities/e1': {
        ...entity,
        mentions: [
          {
            version_id: 'v1',
            document_id: 'd1',
            title: 'Checkout guide',
            spans: [
              { start: 0, end: 12, term: 'Payments API', snippet: 'Payments API retries twice.' },
              { start: 40, end: 46, term: 'payapi', snippet: 'payapi times out after 5s.' },
            ],
          },
          { version_id: 'v2', document_id: 'd2', title: 'Incident 42', spans: [{ start: 3, end: 9, term: 'payapi', snippet: EVIL }] },
        ],
        co_mentions: [{ id: 'e2', name: 'Ledger' }],
      },
    })
    await renderPage('/w/ws1/entities/e1', '/w/:workspaceId/entities/:entityId', Entities, 'member')
    const rows = document.querySelectorAll('section[aria-labelledby="mentions"] li')
    expect(rows).toHaveLength(2)
    expect(rows[0].querySelector('a')!.getAttribute('href')).toBe('/w/ws1/documents/d1')
    expect(rows[0].textContent).toContain('Payments API retries twice.')
    expect(rows[0].textContent).toContain('payapi times out after 5s.')
    expect(rows[1].textContent).toContain(EVIL)
    expect(document.querySelector('img')).toBeNull()
    const co = [...document.querySelectorAll('a')].find((a) => a.textContent === 'Ledger')!
    expect(co.getAttribute('href')).toBe('/w/ws1/entities/e2')
  })
})
