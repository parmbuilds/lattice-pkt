import type { Loaded } from './session'

// Always rendered so screen readers announce changes to it.
export function Message({ text }: { text: string }) {
  return (
    <p className="message" role="status" aria-live="polite">
      {text}
    </p>
  )
}

export function LoadState<T>({ load, empty, emptyText }: { load: Loaded<T>; empty: boolean; emptyText: string }) {
  if (load.loading && !load.data) return <p>Loading…</p>
  if (load.error) return <p role="alert" className="error">{load.error}</p>
  if (load.data && empty) return <p>{emptyText}</p>
  return null
}
