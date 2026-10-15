import { useLocation, useNavigate } from 'react-router'
import { api, ApiError, errorText } from '../api'
import { useAction, useSession } from '../session'
import { Message } from '../ui'
import { SignIn, SignUp } from './SignIn'

export default function Invite() {
  const { me, refresh } = useSession()
  const navigate = useNavigate()
  const { message, run } = useAction()
  const token = useLocation().hash.slice(1)

  if (!token) return <p role="alert">This invite link is incomplete. Ask for a new one.</p>
  if (!me)
    return (
      <>
        <h1>Join a workspace</h1>
        <p>Sign in or create an account to accept this invite.</p>
        <div className="columns">
          <SignIn onDone={() => {}} />
          <SignUp onDone={() => {}} />
        </div>
      </>
    )

  const accept = async () => {
    let workspaceId = ''
    const ok = await run(
      async () => {
        workspaceId = (await api<{ workspace_id: string }>('/api/invites/accept', 'POST', { token })).workspace_id
      },
      '',
      (e) =>
        e instanceof ApiError && e.status === 404
          ? 'This invite was refused: it is invalid, expired, already used or cancelled. Ask for a new one.'
          : errorText(e),
    )
    if (ok) {
      await refresh()
      navigate(`/w/${workspaceId}/documents`)
    }
  }
  return (
    <>
      <h1>Join a workspace</h1>
      <p>Signed in as {me.user.email}.</p>
      <button type="button" onClick={accept}>
        Accept invite
      </button>
      <Message text={message} />
    </>
  )
}
