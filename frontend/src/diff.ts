export type Segment = { kind: 'file' | 'hunk' | 'add' | 'del' | 'context'; text: string }

// Groups unified-diff lines into runs so each added run can be drawn inside one revision cloud.
// Only the first two lines can be file headers: a deleted "-- note" line also starts with "---".
export function diffSegments(diff: string[]): Segment[] {
  const out: Segment[] = []
  diff.forEach((line, i) => {
    const kind: Segment['kind'] =
      i < 2 && /^(---|\+\+\+) /.test(line)
        ? 'file'
        : line.startsWith('@@')
          ? 'hunk'
          : line.startsWith('+')
            ? 'add'
            : line.startsWith('-')
              ? 'del'
              : 'context'
    const last = out.at(-1)
    if (last && last.kind === kind && kind !== 'hunk') last.text += `\n${line}`
    else out.push({ kind, text: line })
  })
  return out
}
