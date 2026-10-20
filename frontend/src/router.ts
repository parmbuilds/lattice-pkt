import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import Approvals from './pages/Approvals.vue'
import Audit from './pages/Audit.vue'
import DocumentDetail from './pages/DocumentDetail.vue'
import Documents from './pages/Documents.vue'
import Entities from './pages/Entities.vue'
import Groups from './pages/Groups.vue'
import Invite from './pages/Invite.vue'
import Members from './pages/Members.vue'
import NotFound from './pages/NotFound.vue'
import Overview from './pages/Overview.vue'
import Search from './pages/Search.vue'
import SignIn from './pages/SignIn.vue'
import Tokens from './pages/Tokens.vue'
import WorkspaceLayout from './pages/WorkspaceLayout.vue'
import Workspaces from './pages/Workspaces.vue'
import { me, refresh } from './session'

export const routes: RouteRecordRaw[] = [
  { path: '/signin', component: SignIn, props: { mode: 'signin' } },
  { path: '/signup', component: SignIn, props: { mode: 'signup' } },
  { path: '/invite', component: Invite },
  { path: '/', component: Workspaces, meta: { auth: true } },
  {
    path: '/w/:workspaceId',
    component: WorkspaceLayout,
    meta: { auth: true },
    children: [
      { path: '', redirect: (to) => `/w/${String(to.params.workspaceId)}/overview` },
      { path: 'overview', component: Overview },
      { path: 'documents', component: Documents },
      { path: 'documents/:documentId', component: DocumentDetail },
      { path: 'search', component: Search },
      { path: 'entities', component: Entities },
      { path: 'entities/:entityId', component: Entities },
      { path: 'tokens', component: Tokens },
      { path: 'approvals', component: Approvals },
      { path: 'members', component: Members },
      { path: 'groups', component: Groups },
      { path: 'audit', component: Audit },
    ],
  },
  { path: '/:rest(.*)*', component: NotFound },
]

export const router = createRouter({ history: createWebHistory(), routes })

// Re-fetch the session on every page so role changes (e.g. a demotion) show at once.
router.beforeEach(async (to, from) => {
  if (me.value === undefined || to.path !== from.path) await refresh()
  if (to.matched.some((r) => r.meta.auth) && !me.value) return { path: '/signin', replace: true }
})
