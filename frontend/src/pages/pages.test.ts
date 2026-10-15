import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Component } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { me, type Role } from '../session'
import Audit from './Audit.vue'
import Groups from './Groups.vue'
import Search from './Search.vue'
import Tokens from './Tokens.vue'

const EVIL = '<img src=x onerror=alert(1)>'
let mounted: VueWrapper | undefined

// Responses keyed by "METHOD /path" (query string ignored).
function mockApi(routes: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit) => {
      const key = `${init.method ?? 'GET'} ${path.split('?')[0]}`
      if (!(key in routes)) return new Response(null, { status: 404 })
      return new Response(JSON.stringify(routes[key]), { status: 200 })
    }),
  )
}

async function renderPage(path: string, routePath: string, page: Component, role: Role = 'admin') {
  me.value = {
    user: { id: 'u1', email: 'ada@example.com', name: 'Ada' },
    csrf_token: 'csrf',
    memberships: [{ workspace_id: 'ws1', workspace_name: 'Acme', role }],
  }
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: routePath, component: page }] })
  await router.push(path)
  mounted = mount(page, { global: { plugins: [router] }, attachTo: document.body })
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
