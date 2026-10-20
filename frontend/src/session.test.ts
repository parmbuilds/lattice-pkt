import { expect, it } from 'vitest'
import { homePath, me, rememberWorkspace, revLetter, useAction } from './session'

it('lands sign-in on the last workspace still held, else the first, else the list', () => {
  const m = (id: string) => ({ workspace_id: id, workspace_name: id, role: 'member' as const })
  const signedIn = (...ids: string[]) =>
    (me.value = { user: { id: 'u', email: 'u@x', name: 'U' }, csrf_token: '', memberships: ids.map(m) })
  localStorage.clear()
  signedIn('a', 'b')
  expect(homePath()).toBe('/w/a/overview')
  rememberWorkspace('b')
  expect(homePath()).toBe('/w/b/overview')
  signedIn('a', 'c') // removed from b, or another person on this browser
  expect(homePath()).toBe('/w/a/overview')
  signedIn()
  expect(homePath()).toBe('/')
  me.value = undefined
})

it('ignores a second run while the first is still in flight', async () => {
  const { run } = useAction()
  let calls = 0
  let finish = () => {}
  const action = () => {
    calls += 1
    return new Promise<void>((resolve) => (finish = resolve))
  }
  const first = run(action)
  expect(await run(action)).toBe(false)
  finish()
  expect(await first).toBe(true)
  expect(calls).toBe(1)
})

it('letters revisions like a drawing register', () => {
  expect([0, 1, 25, 26, 27, 701, 702].map(revLetter)).toEqual(['A', 'B', 'Z', 'AA', 'AB', 'ZZ', 'AAA'])
})
