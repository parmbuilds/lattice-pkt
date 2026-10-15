import { type Browser, expect, type Page, test } from '@playwright/test'

const PASSWORD = 'lattice-demo-password'

// Fails the test on any console error or CSP violation except the expected 401 from /me before sign-in.
function watch(page: Page) {
  const problems: string[] = []
  page.on('console', (m) => {
    if ((m.type() === 'error' || /Content Security Policy/i.test(m.text())) && !/401 \(Unauthorized\)/.test(m.text()))
      problems.push(m.text())
  })
  page.on('pageerror', (e) => problems.push(e.message))
  return problems
}

async function signIn(page: Page, email: string, password = PASSWORD) {
  await page.goto('/signin')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page).toHaveURL(/\/$/)
}

async function newPage(browser: Browser) {
  const page = await (await browser.newContext()).newPage()
  return { page, problems: watch(page) }
}

const nav = (page: Page, name: string) => page.getByRole('navigation', { name: 'Workspace' }).getByRole('link', { name })
const status = (page: Page) => page.getByRole('status').filter({ hasText: /\S/ }).first()

test('an alias host redirects to the public origin, where sign-in works', async ({ page, baseURL }) => {
  test.skip(!baseURL?.includes('localhost'), 'alias check needs a localhost base URL')
  await page.goto(baseURL!.replace('localhost', '127.0.0.1') + '/signin')
  await expect(page).toHaveURL(`${baseURL}/signin`)
  await signIn(page, 'olivia@lattice.demo')
  await expect(page.getByRole('link', { name: 'Acme Engineering' })).toBeVisible()
})

for (const [who, manager] of [
  ['olivia', true],
  ['arjun', true],
  ['mei', false],
  ['sam', false],
] as const) {
  test(`every page renders for ${who} without console or CSP errors`, async ({ page }) => {
    const problems = watch(page)
    await signIn(page, `${who}@lattice.demo`)
    await page.getByRole('link', { name: 'Acme Engineering' }).click()
    await expect(page.getByRole('heading', { name: 'Documents' })).toBeVisible()
    await expect(page.getByRole('table')).toBeVisible()

    const pages = ['Documents', 'Search', 'Tokens', ...(manager ? ['Approvals', 'Members', 'Groups', 'Audit trail'] : [])]
    for (const name of pages) {
      await nav(page, name).click()
      await expect(page.getByRole('heading', { level: 1, name })).toBeVisible()
      await expect(page.getByText('Loading…')).toHaveCount(0)
      await expect(page.getByRole('alert')).toHaveCount(0)
    }
    if (!manager) {
      await expect(nav(page, 'Approvals')).toHaveCount(0)
      await page.goto(page.url().replace(/\/[^/]+$/, '/approvals'))
      await expect(page.getByText('Not found or not permitted.')).toBeVisible()
    }

    await nav(page, 'Documents').click()
    await page.getByRole('table').getByRole('link').first().click()
    await expect(page.getByRole('region', { name: 'Document text' }).or(page.getByLabel('Document text'))).toBeVisible()
    await expect(page.getByText('Serves search and agents')).toBeVisible()
    expect(problems).toEqual([])
  })
}

test('the same search returns different documents for Mei and Sam', async ({ browser }) => {
  const results: Record<string, string[]> = {}
  for (const who of ['mei', 'sam']) {
    const { page } = await newPage(browser)
    await signIn(page, `${who}@lattice.demo`)
    await page.getByRole('link', { name: 'Acme Engineering' }).click()
    await nav(page, 'Search').click()
    await page.getByLabel('Search approved documents').fill('checkout')
    await page.getByRole('button', { name: 'Search' }).click()
    await expect(page.getByText(/results? for “checkout”/)).toBeVisible()
    results[who] = await page.getByRole('listitem').getByRole('link').allTextContents()
    await expect(page.locator('mark').first()).toBeVisible()
  }
  expect(results.mei).toContain('Runbook: checkout payment timeouts')
  expect(results.sam).not.toContain('Runbook: checkout payment timeouts')
  expect(results.sam).toContain('Incident 2026-09-14: checkout outage')
  expect(results.mei).not.toContain('Incident 2026-09-14: checkout outage')
})

test('a document goes through draft, approval, edit, rejection, sharing and withdrawal', async ({ browser }) => {
  const run = Date.now().toString(36)
  const word = `zebracorn${run}`
  const title = `E2E runbook ${run}`
  const ownerEmail = `e2e-owner-${run}@lattice.test`
  const memberEmail = `e2e-member-${run}@lattice.test`
  const memberPassword = 'e2e-member-password'

  // Owner: sign up, create a workspace and a group, invite a member.
  const { page: owner, problems: ownerProblems } = await newPage(browser)
  await owner.goto('/signup')
  await owner.getByLabel('Name').fill('E2E Owner')
  await owner.getByLabel('Email').fill(ownerEmail)
  await owner.getByLabel('Password (8 or more characters)').fill('e2e-owner-password')
  await owner.getByRole('button', { name: 'Create account' }).click()
  await expect(owner).toHaveURL(/\/$/)
  await owner.getByLabel('Workspace name').fill(`E2E ${run}`)
  await owner.getByRole('button', { name: 'Create workspace' }).click()
  await expect(owner).toHaveURL(/\/w\/[^/]+\/documents$/)
  const workspace = owner.url().replace(/\/documents$/, '')

  await nav(owner, 'Groups').click()
  await owner.getByLabel('New group name').fill('Ops')
  await owner.getByRole('button', { name: 'Create group' }).click()
  await expect(owner.getByRole('region', { name: 'Ops' })).toBeVisible()

  await nav(owner, 'Members').click()
  await owner.getByRole('button', { name: 'Create invite link' }).click()
  const inviteUrl = await owner.getByLabel('Invite link (single use)').inputValue()
  expect(inviteUrl).toContain('/invite#')

  // Member: accept the invite with a new account.
  const { page: member, problems: memberProblems } = await newPage(browser)
  await member.goto(inviteUrl)
  const signUp = member.locator('form', { hasText: 'Create an account' })
  await signUp.getByLabel('Name').fill(`E2E Member ${run}`)
  await signUp.getByLabel('Email').fill(memberEmail)
  await signUp.getByLabel('Password').fill(memberPassword)
  await signUp.getByRole('button', { name: 'Create account' }).click()
  await member.getByRole('button', { name: 'Accept invite' }).click()
  await expect(member).toHaveURL(`${workspace}/documents`)

  // Owner puts the member in Ops.
  await nav(owner, 'Groups').click()
  const ops = owner.getByRole('region', { name: 'Ops' })
  await ops.getByLabel('Add person').selectOption({ label: `E2E Member ${run}` })
  await ops.getByRole('button', { name: 'Add to group' }).click()
  await expect(ops.getByText(`E2E Member ${run}`)).toBeVisible()

  // Member drafts a document: visible to them, never served.
  await member.getByRole('button', { name: 'New document' }).click()
  await member.getByLabel('Title').fill(title)
  await member.getByLabel('Text').fill(`Step one.\nRestart the ${word} service.`)
  await member.getByRole('button', { name: 'Add document' }).click()
  await expect(member).toHaveURL(/\/documents\/[^/]+$/)
  const docPath = new URL(member.url()).pathname
  await expect(member.getByText('Nothing yet. Drafts are never served.')).toBeVisible()

  const search = async (expectFound: boolean) => {
    await member.goto(`${workspace}/search`)
    await member.getByLabel('Search approved documents').fill(word)
    await member.getByRole('button', { name: 'Search' }).click()
    if (expectFound) await expect(member.getByRole('link', { name: title })).toBeVisible()
    else await expect(member.getByText('No approved documents you can retrieve match.')).toBeVisible()
  }
  await search(false)

  // Owner approves the exact draft from a clouded diff.
  await nav(owner, 'Approvals').click()
  const review = owner.getByRole('region', { name: title })
  await expect(review.locator('.cloud')).toContainText(word)
  await review.getByRole('button', { name: 'Approve' }).click()
  await expect(status(owner)).toContainText('it is now the serving version')
  await search(true)

  // Member edits: the approved REV A keeps serving while REV B waits.
  await member.goto(docPath)
  await member.getByRole('button', { name: 'Edit (creates a new draft)' }).click()
  await member.getByLabel('Text').fill(`Step one.\nRestart the ${word} service.\nStep two: page the on-call.`)
  await member.getByRole('button', { name: 'Save draft' }).click()
  await expect(status(member)).toContainText('New draft saved')
  await expect(member.locator('.stamp', { hasText: 'Edit pending' })).toBeVisible()
  await expect(member.getByText('REV A, the approved version')).toBeVisible()

  // Owner rejects the edit with a reason; the member sees the decision.
  await owner.reload()
  const edit = owner.getByRole('region', { name: title })
  await edit.getByLabel('Rejection reason (optional)').fill('Needs a rollback step')
  await edit.getByRole('button', { name: 'Reject' }).click()
  await expect(status(owner)).toContainText('the approved version keeps serving')
  await member.reload()
  await expect(member.getByText('Needs a rollback step')).toBeVisible()
  await expect(member.locator('.stamp', { hasText: 'Rejected' })).toHaveCount(2) // title block and revisions list

  // Owner shares with Ops.
  await owner.goto(docPath)
  await owner.getByLabel('Share with group').selectOption({ label: 'Ops' })
  await owner.getByRole('button', { name: 'Share', exact: true }).click()
  await expect(status(owner)).toHaveText('Shared.')
  await expect(owner.getByRole('button', { name: 'Unshare from Ops' })).toBeVisible()

  // Member creates a token, sees the secret once, revokes it.
  await member.goto(`${workspace}/tokens`)
  await member.getByLabel('Token name').fill('e2e laptop')
  await member.getByLabel('Your password').fill(memberPassword)
  await member.getByRole('button', { name: 'Create token' }).click()
  await expect(member.getByText('Copy this token now. It will not be shown again.')).toBeVisible()
  await member.getByRole('button', { name: 'Revoke e2e laptop' }).click()
  await expect(member.locator('.stamp', { hasText: 'Revoked' })).toBeVisible()

  // Owner withdraws: search stops serving it at once.
  owner.once('dialog', (d) => d.accept())
  await owner.getByRole('button', { name: 'Withdraw' }).click()
  await expect(owner.getByText('Nothing. Withdrawn documents are never served.')).toBeVisible()
  await search(false)

  // The audit trail recorded the decisions.
  await nav(owner, 'Audit trail').click()
  for (const action of ['approve', 'reject', 'withdraw', 'share']) {
    await expect(owner.getByRole('cell', { name: action, exact: true }).first()).toBeVisible()
  }

  // Signing out returns to sign-in.
  await owner.getByRole('button', { name: 'Sign out' }).last().click()
  await expect(owner).toHaveURL(/\/signin$/)

  expect(ownerProblems).toEqual([])
  expect(memberProblems).toEqual([])
})
