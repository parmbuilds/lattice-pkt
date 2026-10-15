import { expect, it } from 'vitest'
import { revLetter, useAction } from './session'

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
