// The file types the upload endpoint accepts. The server still sniffs content; this only
// spares the round trip for files that would certainly be refused.
export const ACCEPT = '.md,.txt,.html,.pdf,.docx'
const EXTENSIONS = ACCEPT.split(',')

export type Picked = { file: File; path: string }

export const hidden = (path: string) => path.split('/').some((part) => part.startsWith('.'))
export const supported = (path: string) => EXTENSIONS.some((ext) => path.toLowerCase().endsWith(ext))

// The files a drop carries, walking dropped folders recursively.
export async function droppedFiles(data: DataTransfer): Promise<Picked[]> {
  // Entries must be taken before the first await: the drop's data is unreadable once the event returns.
  const entries = [...data.items].map((item) => item.webkitGetAsEntry?.()).filter((e): e is FileSystemEntry => !!e)
  if (!entries.length) return [...data.files].map((file) => ({ file, path: file.name }))
  const out: Picked[] = []
  for (const entry of entries) await walk(entry, out)
  return out
}

async function walk(entry: FileSystemEntry, out: Picked[]): Promise<void> {
  if (entry.isFile) {
    const file = await new Promise<File>((ok, fail) => (entry as FileSystemFileEntry).file(ok, fail))
    out.push({ file, path: entry.fullPath.replace(/^\//, '') })
    return
  }
  const reader = (entry as FileSystemDirectoryEntry).createReader()
  // readEntries hands back a folder in batches; an empty batch means it is done.
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((ok, fail) => reader.readEntries(ok, fail))
    if (!batch.length) return
    for (const child of batch) await walk(child, out)
  }
}
