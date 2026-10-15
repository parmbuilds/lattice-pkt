import { useCallback, useEffect, useState } from 'react'
import { BrowserRouter, Link, Navigate, NavLink, Outlet, Route, Routes, useLocation, useNavigate } from 'react-router'
import { api, NOT_FOUND, setCsrfToken } from './api'
import Approvals from './pages/Approvals'
import Audit from './pages/Audit'
import DocumentDetail from './pages/DocumentDetail'
import Documents from './pages/Documents'
import Groups from './pages/Groups'
import Invite from './pages/Invite'
import Members from './pages/Members'
import Search from './pages/Search'
import { SignIn, SignUp } from './pages/SignIn'
import Tokens from './pages/Tokens'
import Workspaces from './pages/Workspaces'
import { type Me, SessionContext, useSession, useWorkspace } from './session'

export default function App() {
  return (
    <BrowserRouter>
      <Shell />
    </BrowserRouter>
  )
}

function Shell() {
  const [me, setMe] = useState<Me | null | undefined>(undefined)
  const location = useLocation()
  const refresh = useCallback(
    () =>
      api<Me>('/api/auth/me').then(
        (fresh) => {
          setCsrfToken(fresh.csrf_token)
          setMe(fresh)
        },
        () => {
          setCsrfToken('')
          setMe(null)
        },
      ),
    [],
  )
  // Re-fetch on every page so role changes (e.g. a demotion) show at once.
  useEffect(() => {
    void refresh()
  }, [location.pathname, refresh])

  return (
    <SessionContext value={{ me, refresh }}>
      <Header />
      <main>
        {me === undefined ? (
          <p>Loading…</p>
        ) : (
          <Routes>
            <Route path="/signin" element={<SignInPage />} />
            <Route path="/signup" element={<SignUpPage />} />
            <Route path="/invite" element={<Invite />} />
            <Route element={me ? <Outlet /> : <Navigate to="/signin" replace />}>
              <Route path="/" element={<Workspaces />} />
              <Route path="/w/:workspaceId" element={<WorkspaceLayout />}>
                <Route index element={<Navigate to="documents" replace />} />
                <Route path="documents" element={<Documents />} />
                <Route path="documents/:documentId" element={<DocumentDetail />} />
                <Route path="search" element={<Search />} />
                <Route path="tokens" element={<Tokens />} />
                <Route path="approvals" element={<Approvals />} />
                <Route path="members" element={<Members />} />
                <Route path="groups" element={<Groups />} />
                <Route path="audit" element={<Audit />} />
              </Route>
            </Route>
            <Route path="*" element={<p>{NOT_FOUND}</p>} />
          </Routes>
        )}
      </main>
    </SessionContext>
  )
}

function Header() {
  const { me, refresh } = useSession()
  const navigate = useNavigate()
  const location = useLocation()
  const current = location.pathname.match(/^\/w\/([^/]+)/)?.[1] ?? ''
  const signOut = async () => {
    await api('/api/auth/signout', 'POST').catch(() => undefined)
    await refresh()
    navigate('/signin')
  }
  return (
    <header className="site-header">
      <Link to="/" className="brand">
        Lattice
      </Link>
      {me && (
        <>
          <label>
            Workspace{' '}
            <select value={current} onChange={(e) => navigate(e.target.value ? `/w/${e.target.value}/documents` : '/')}>
              <option value="">All workspaces</option>
              {me.memberships.map((m) => (
                <option key={m.workspace_id} value={m.workspace_id}>
                  {m.workspace_name} ({m.role})
                </option>
              ))}
            </select>
          </label>
          <span className="who">{me.user.name}</span>
          <button type="button" onClick={signOut}>
            Sign out
          </button>
        </>
      )}
    </header>
  )
}

function WorkspaceLayout() {
  const { role, manager } = useWorkspace()
  if (!role) return <p role="alert">{NOT_FOUND}</p>
  const links = [
    ['documents', 'Documents'],
    ['search', 'Search'],
    ['tokens', 'Tokens'],
    ...(manager
      ? [
          ['approvals', 'Approvals'],
          ['members', 'Members'],
          ['groups', 'Groups'],
          ['audit', 'Audit'],
        ]
      : []),
  ]
  return (
    <>
      <nav aria-label="Workspace">
        <span className="role">Your role: {role}</span>
        <ul>
          {links.map(([to, label]) => (
            <li key={to}>
              <NavLink to={to}>{label}</NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <Outlet />
    </>
  )
}

function SignInPage() {
  const navigate = useNavigate()
  return (
    <>
      <SignIn onDone={() => navigate('/')} />
      <Link to="/signup">Create an account</Link>
    </>
  )
}

function SignUpPage() {
  const navigate = useNavigate()
  return (
    <>
      <SignUp onDone={() => navigate('/')} />
      <Link to="/signin">I already have an account</Link>
    </>
  )
}
