import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { type Role, SessionContext } from '../session'
import Audit from './Audit'
import Groups from './Groups'
import Search from './Search'
import Tokens from './Tokens'

const EVIL = '<img src=x onerror=alert(1)>'

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

function renderPage(path: string, routePath: string, page: ReactNode, role: Role = 'admin') {
  const me = {
    user: { id: 'u1', email: 'ada@example.com', name: 'Ada' },
    csrf_token: 'csrf',
    memberships: [{ workspace_id: 'ws1', workspace_name: 'Acme', role }],
  }
  return render(
    <SessionContext value={{ me, refresh: async () => {} }}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={routePath} element={page} />
        </Routes>
      </MemoryRouter>
    </SessionContext>,
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Tokens page', () => {
  it('shows the secret only from the creation response, never after the list reloads', async () => {
    const listed = {
      id: 't1',
      name: 'laptop',
      prefix: 'lat_ab12',
      created_at: '2026-10-15T00:00:00Z',
      expires_at: '2027-01-13T00:00:00Z',
      revoked: false,
      last_used_at: null,
    }
    mockApi({
      'GET /api/workspaces/ws1/tokens': { tokens: [listed] },
      'POST /api/workspaces/ws1/tokens': { ...listed, token: 'lat_SECRET_VALUE' },
    })
    renderPage('/w/ws1/tokens', '/w/:workspaceId/tokens', <Tokens />, 'member')
    await screen.findByText('laptop')
    expect(document.body.textContent).not.toContain('lat_SECRET_VALUE')

    fireEvent.change(screen.getByLabelText('Token name'), { target: { value: 'laptop' } })
    fireEvent.change(screen.getByLabelText('Your password'), { target: { value: 'pw123456' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create token' }))
    await screen.findByText('lat_SECRET_VALUE')
    expect(document.body.textContent).toContain('will not be shown again')

    cleanup()
    renderPage('/w/ws1/tokens', '/w/:workspaceId/tokens', <Tokens />, 'member')
    await screen.findByText('laptop')
    expect(document.body.textContent).not.toContain('lat_SECRET_VALUE')
  })
})

describe('user-supplied strings render as text', () => {
  it('shows an HTML-looking group name literally on the groups page', async () => {
    mockApi({
      'GET /api/workspaces/ws1/groups': { groups: [{ id: 'g1', name: EVIL, members: [] }] },
      'GET /api/workspaces/ws1/members': { members: [] },
    })
    renderPage('/w/ws1/groups', '/w/:workspaceId/groups', <Groups />)
    expect(await screen.findByText(EVIL)).toBeTruthy()
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
    renderPage('/w/ws1/audit', '/w/:workspaceId/audit', <Audit />)
    await screen.findByText('manage_groups')
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
            approved_by: 'u2',
          },
        ],
      },
    })
    renderPage('/w/ws1/search', '/w/:workspaceId/search', <Search />, 'member')
    fireEvent.change(screen.getByLabelText('Search approved documents'), {
      target: { value: 'checkout' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    await screen.findByText('Checkout guide')
    const marks = document.querySelectorAll('mark')
    expect(marks).toHaveLength(1)
    expect(marks[0].textContent).toBe('checkout')
    expect(document.body.textContent).not.toContain('[[')
    expect(document.body.textContent).not.toContain(']]')
  })
})
