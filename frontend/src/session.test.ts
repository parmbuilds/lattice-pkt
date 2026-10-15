import { act, renderHook } from '@testing-library/react'
import { expect, it } from 'vitest'
import { useAction } from './session'

it('ignores a second run while the first is still in flight', async () => {
  const { result } = renderHook(() => useAction())
  let calls = 0
  let finish = () => {}
  const action = () => {
    calls += 1
    return new Promise<void>((resolve) => (finish = resolve))
  }
  let first: Promise<boolean> = Promise.resolve(false)
  let second = true
  await act(async () => {
    first = result.current.run(action)
    second = await result.current.run(action)
  })
  expect(second).toBe(false)
  await act(async () => {
    finish()
    expect(await first).toBe(true)
  })
  expect(calls).toBe(1)
})
