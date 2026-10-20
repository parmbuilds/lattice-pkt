import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, vi } from 'vitest'
import type { Component } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { me, type Role } from '../session'

// Shared by the page test files; vitest collects only *.test.ts, so this file runs no tests itself.
export const EVIL = '<img src=x onerror=alert(1)>'
export let mounted: VueWrapper | undefined

export function track(wrapper: VueWrapper) {
  mounted = wrapper
  return wrapper
}

// A route value that needs a status other than 200, or that changes per call.
export class Reply {
  status: number
  body: unknown
  constructor(status: number, body: unknown) {
    this.status = status
    this.body = body
  }
}
type Handler = (init: RequestInit) => Reply | Response

// Responses keyed by "METHOD /path" (query string ignored).
export function mockApi(routes: Record<string, unknown>) {
  const fetchMock = vi.fn(async (path: string, init: RequestInit) => {
    const key = `${init.method ?? 'GET'} ${path.split('?')[0]}`
    if (!(key in routes)) return new Response(null, { status: 404 })
    let value = routes[key]
    if (typeof value === 'function') value = (value as Handler)(init)
    if (value instanceof Response) return value
    const reply = value instanceof Reply ? value : new Reply(200, value)
    return new Response(JSON.stringify(reply.body), { status: reply.status })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

export async function renderPage(
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
  track(mount(RouterView, { global: { plugins: [router] }, attachTo: document.body }))
  await flushPromises()
  return mounted!
}

export const field = (w: VueWrapper, label: string) => w.findAll('label').find((l) => l.text().startsWith(label))!.find('input')

afterEach(() => {
  mounted?.unmount()
  mounted = undefined
  vi.unstubAllGlobals()
})
