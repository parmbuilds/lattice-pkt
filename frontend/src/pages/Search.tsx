import { type FormEvent, useState } from 'react'
import { Link } from 'react-router'
import { useLoad, useWorkspace, when } from '../session'
import { LoadState } from '../ui'

type Result = {
  version_id: string
  document_id: string
  title: string
  rank: number
  snippet: string
  approved_at: string
  approved_by: string
}

// Snippets mark matches with [[ ]]; split them into text nodes and <mark>s, never HTML (KTD11).
function Snippet({ text }: { text: string }) {
  return <>{text.split(/\[\[(.*?)\]\]/s).map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : part))}</>
}

export default function Search() {
  const { base } = useWorkspace()
  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState<string | null>(null)
  const results = useLoad<{ results: Result[] }>(
    submitted === null ? null : `${base}/search?${new URLSearchParams({ q: submitted })}`,
  )
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (query === submitted) results.reload()
    setSubmitted(query)
  }
  return (
    <>
      <h1>Search</h1>
      <form onSubmit={submit} role="search">
        <label>
          Search approved documents
          <input type="search" required value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <button type="submit">Search</button>
      </form>
      <div aria-live="polite">
        {submitted !== null && (
          <LoadState load={results} empty={!results.data?.results.length} emptyText="No approved documents match." />
        )}
        {!!results.data?.results.length && (
          <ol>
            {results.data.results.map((r) => (
              <li key={r.version_id}>
                <Link to={`../documents/${r.document_id}`} relative="path">
                  {r.title}
                </Link>
                <p>
                  <Snippet text={r.snippet} />
                </p>
                <small>Approved {when(r.approved_at)}</small>
              </li>
            ))}
          </ol>
        )}
      </div>
    </>
  )
}
