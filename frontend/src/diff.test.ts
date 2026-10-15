import { expect, it } from 'vitest'
import { diffSegments } from './diff'

it('groups runs and treats only the first two lines as file headers', () => {
  const kinds = diffSegments(['--- serving', '+++ draft', '@@ -1,3 +1,3 @@', ' keep', '--- old rule', '-gone', '+new', '+newer', ' tail']).map(
    (s) => [s.kind, s.text],
  )
  expect(kinds).toEqual([
    ['file', '--- serving\n+++ draft'],
    ['hunk', '@@ -1,3 +1,3 @@'],
    ['context', ' keep'],
    ['del', '--- old rule\n-gone'],
    ['add', '+new\n+newer'],
    ['context', ' tail'],
  ])
})
