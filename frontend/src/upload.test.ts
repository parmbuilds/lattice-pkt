import { describe, expect, it } from 'vitest'
import { droppedFiles, hidden, supported } from './upload'

// Minimal stand-ins for the browser's FileSystemEntry tree.
const fileEntry = (fullPath: string) => ({
  isFile: true,
  fullPath,
  file: (ok: (f: File) => void) => ok(new File(['x'], fullPath.split('/').pop()!)),
})
const dirEntry = (fullPath: string, children: unknown[]) => ({
  isFile: false,
  fullPath,
  createReader: () => {
    // Browsers return a folder in batches; serve two batches, then an empty one.
    const batches = [children.slice(0, 1), children.slice(1), []]
    return { readEntries: (ok: (b: unknown[]) => void) => ok(batches.shift()!) }
  },
})
const drop = (entries: unknown[]) =>
  ({ items: entries.map((e) => ({ webkitGetAsEntry: () => e })), files: [] }) as unknown as DataTransfer

describe('upload helpers', () => {
  it('walks dropped folders recursively, across reader batches', async () => {
    const tree = dirEntry('/docs', [
      fileEntry('/docs/a.md'),
      dirEntry('/docs/sub', [fileEntry('/docs/sub/b.pdf'), fileEntry('/docs/sub/c.png')]),
    ])
    const picked = await droppedFiles(drop([tree, fileEntry('/top.txt')]))
    expect(picked.map((p) => p.path)).toEqual(['docs/a.md', 'docs/sub/b.pdf', 'docs/sub/c.png', 'top.txt'])
    expect(picked[1].file.name).toBe('b.pdf')
  })

  it('falls back to plain files when the drop has no entries', async () => {
    const f = new File(['x'], 'a.md')
    const picked = await droppedFiles({ items: [], files: [f] } as unknown as DataTransfer)
    expect(picked).toEqual([{ file: f, path: 'a.md' }])
  })

  it('accepts only the supported types and spots hidden paths', () => {
    expect(['a.MD', 'b.txt', 'c.html', 'd.pdf', 'e.docx'].every(supported)).toBe(true)
    expect(['f.png', 'g.doc', 'h.htm', 'notes'].some(supported)).toBe(false)
    expect(hidden('.DS_Store')).toBe(true)
    expect(hidden('repo/.git/README.md')).toBe(true)
    expect(hidden('docs/a.md')).toBe(false)
  })
})
