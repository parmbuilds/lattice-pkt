import { type FormEvent, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { api } from '../api'
import { type DocumentSummary, documentState, useAction, useLoad, useWorkspace } from '../session'
import { LoadState, Message } from '../ui'

export default function Documents() {
  const { base, workspaceId } = useWorkspace()
  const navigate = useNavigate()
  const docs = useLoad<{ documents: DocumentSummary[] }>(`${base}/documents`)
  const { message, run } = useAction()
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')

  const create = async (e: FormEvent) => {
    e.preventDefault()
    let id = ''
    if (await run(async () => (id = (await api<{ id: string }>(`${base}/documents`, 'POST', { title, body })).id)))
      navigate(`/w/${workspaceId}/documents/${id}`)
  }

  return (
    <>
      <h1>Documents</h1>
      <LoadState load={docs} empty={!docs.data?.documents.length} emptyText="No documents you can see yet." />
      {!!docs.data?.documents.length && (
        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>Author</th>
              <th>State</th>
            </tr>
          </thead>
          <tbody>
            {docs.data.documents.map((d) => (
              <tr key={d.id}>
                <td>
                  <Link to={d.id}>{d.title || '(untitled)'}</Link>
                </td>
                <td>{d.author_name}</td>
                <td>{documentState(d)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form onSubmit={create} className="card">
        <h2>Add a document</h2>
        <p>New documents start as an unshared draft that an admin must approve.</p>
        <label>
          Title
          <input required value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label>
          Text
          <textarea required rows={10} value={body} onChange={(e) => setBody(e.target.value)} />
        </label>
        <button type="submit">Add document</button>
      </form>
      <Message text={message} />
    </>
  )
}
