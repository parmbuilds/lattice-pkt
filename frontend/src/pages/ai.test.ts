import { flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import AiSettings from './AiSettings.vue'
import Overview from './Overview.vue'
import { EVIL, field, mockApi, renderPage, Reply } from './testing'

const WS = '/api/workspaces/ws1'
const PROVIDERS = [{ id: 'openai', label: 'OpenAI', models: [{ id: 'gpt-x', label: 'GPT X' }] }]
const settings = (over: Record<string, unknown> = {}) => ({
  providers: PROVIDERS,
  enabled: true,
  provider_id: 'openai',
  model: 'gpt-x',
  retention_days: 30,
  ...over,
})
const CONVERSATION = { id: 'c1', title: 'latency', created_at: '2026-10-20T00:00:00Z', last_turn_at: '2026-10-20T00:00:00Z' }
const PASSAGE = {
  id: 'P1',
  document_id: 'd1',
  version_id: 'v1',
  title: 'Checkout runbook',
  start: 10,
  end: 60,
  text: 'Drain the queue first.',
  approved_by: 'Arjun',
  approved_at: '2026-10-15T00:00:00Z',
}

// A turn's event stream the test feeds by hand; aborting the request errors it, as a browser does.
function openStream(init: RequestInit) {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const response = new Response(new ReadableStream<Uint8Array>({ start: (c) => void (controller = c) }))
  init.signal?.addEventListener('abort', () => controller.error(init.signal!.reason))
  const send = async (event: string, data: unknown) => {
    controller.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`))
    await flushPromises()
  }
  const end = async (event: string, data: unknown) => {
    await send(event, data)
    controller.close()
    await flushPromises()
  }
  return { response, send, end, signal: init.signal! }
}

const text = () => document.body.textContent ?? ''
const button = (label: string) => [...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === label)

async function askWithAi(routes: Record<string, unknown> = {}) {
  const streams: ReturnType<typeof openStream>[] = []
  const fetchMock = mockApi({
    [`GET ${WS}/documents`]: { documents: [] },
    [`GET ${WS}/search/history`]: { searches: [] },
    [`GET ${WS}/ai/settings`]: settings(),
    [`GET ${WS}/conversations`]: { conversations: [] },
    [`POST ${WS}/conversations`]: new Reply(201, CONVERSATION),
    [`POST ${WS}/conversations/c1/turns`]: (init: RequestInit) => streams[streams.push(openStream(init)) - 1].response,
    ...routes,
  })
  const w = await renderPage('/w/ws1/overview', '/w/:workspaceId/overview', Overview, 'member')
  await w.find('#ask-input').setValue('latency')
  await w.find('form').trigger('submit')
  await flushPromises()
  return { w, fetchMock, stream: streams[0] }
}

describe('Overview with AI answers on', () => {
  it('streams checked sentences in order, marks unsupported ones and renders them as text', async () => {
    const { fetchMock, stream } = await askWithAi()
    const bodyOf = (path: string) => JSON.parse(fetchMock.mock.calls.find(([p, i]) => p === path && i.method === 'POST')![1].body as string)
    expect(bodyOf(`${WS}/conversations`)).toEqual({ title: 'latency' })
    expect(bodyOf(`${WS}/conversations/c1/turns`)).toEqual({ question: 'latency' })
    expect(text()).toContain('Written by GPT X from OpenAI')

    await stream.send('passages', { passages: [PASSAGE], left_out: 0, summary_dropped: false })
    await stream.send('sentence', { text: `Drain the queue. ${EVIL}`, markers: ['P1'], supported: true })
    expect(text()).toContain(`Drain the queue. ${EVIL}`)
    expect(button('Answering…')).toBeDefined()
    await stream.send('sentence', { text: 'Then reboot the moon.', markers: [], supported: false })
    await stream.end('done', { outcome: 'partly_supported', turn: 1 })

    const answer = document.querySelector('[aria-live="polite"]')!.textContent!
    expect(answer.indexOf('Drain the queue.')).toBeLessThan(answer.indexOf('Then reboot the moon.'))
    const marked = [...document.querySelectorAll('li')].find((li) => li.textContent?.includes('Then reboot the moon.'))!
    expect(marked.textContent).toContain('Not supported by your documents')
    expect(document.querySelector('[aria-live="polite"] img')).toBeNull()
    expect(text()).toContain('Checkout runbook')
    expect(text()).toContain('Drain the queue first.')
    expect(button('Ask')).toBeDefined()
  })

  it("replaces the streamed sentences with \"Your documents don't cover this\"", async () => {
    const { stream } = await askWithAi()
    await stream.send('passages', { passages: [PASSAGE], left_out: 0, summary_dropped: false })
    await stream.send('sentence', { text: 'A guess.', markers: [], supported: false })
    await stream.end('not_covered', { notice: "Your documents don't cover this", turn: 1 })
    expect(text()).toContain("Your documents don't cover this")
    expect(text()).not.toContain('A guess.')
  })

  it('replaces the streamed sentences with the not-saved notice when the turn is discarded', async () => {
    const { stream } = await askWithAi()
    const notice = 'This answer was not saved because a document it used is no longer available to you'
    await stream.send('passages', { passages: [PASSAGE], left_out: 0, summary_dropped: false })
    await stream.send('sentence', { text: 'Drain the queue.', markers: ['P1'], supported: true })
    await stream.end('discarded', { notice })
    expect(text()).toContain(notice)
    expect(text()).not.toContain('Drain the queue.')
    expect(text()).not.toContain('Checkout runbook') // no notice names a document the asker may no longer read
  })

  it('disables the composer and history while answering, and Stop aborts the request', async () => {
    const { w, stream } = await askWithAi({
      [`GET ${WS}/search/history`]: {
        searches: [{ occurred_at: '2026-10-20T00:00:00Z', source: 'search', conversation_id: null, token_name: null, query: 'q', count: 1 }],
      },
    })
    await stream.send('passages', { passages: [], left_out: 0, summary_dropped: false })
    expect((w.find('#ask-input').element as HTMLTextAreaElement).disabled).toBe(true)
    expect((document.querySelector('[aria-label="Ask again: q"]') as HTMLButtonElement).disabled).toBe(true)
    button('Stop')!.click()
    await flushPromises()
    expect(stream.signal.aborted).toBe(true)
    expect(text()).toContain('Stopped. This answer was not saved.')
    expect((w.find('#ask-input').element as HTMLTextAreaElement).disabled).toBe(false)
  })

  it('reopens a conversation from an Ask history row and says only how many turns were left out', async () => {
    mockApi({
      [`GET ${WS}/documents`]: { documents: [] },
      [`GET ${WS}/search/history`]: {
        searches: [{ occurred_at: '2026-10-20T00:00:00Z', source: 'ask', conversation_id: 'c1', token_name: null, query: 'latency', count: 1 }],
      },
      [`GET ${WS}/ai/settings`]: settings(),
      [`GET ${WS}/conversations`]: { conversations: [CONVERSATION] },
      [`GET ${WS}/conversations/c1`]: {
        ...CONVERSATION,
        turns: [
          {
            ordinal: 3,
            question: 'and after that?',
            sentences: [{ text: 'Reboot the gateway.', markers: ['P1'], supported: true }],
            outcome: 'answered',
            passages: [{ document_id: 'd1', version_id: 'v1', title: 'Checkout runbook', start: 0, end: 9, text: 'Reboot it.' }],
          },
          { ordinal: 4, question: 'and the moon?', sentences: [{ text: 'A guess.', markers: [], supported: false }], outcome: 'not_covered', passages: [] },
        ],
        left_out: 2,
        summary_dropped: true,
      },
    })
    await renderPage('/w/ws1/overview', '/w/:workspaceId/overview', Overview, 'member')
    ;(document.querySelector('[aria-label="Reopen conversation: latency"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(text()).toContain('Reboot the gateway.')
    expect(text()).toContain("Your documents don't cover this")
    expect(text()).not.toContain('A guess.')
    expect(text()).toContain('2 earlier turns are left out because a document they used is no longer available to you.')
    expect(text()).toContain('Earlier context was dropped because a document it drew on is no longer available to you')
  })

  it('shows a conversation in a disconnected workspace read-only, without the composer', async () => {
    mockApi({
      [`GET ${WS}/documents`]: { documents: [] },
      [`GET ${WS}/search/history`]: { searches: [] },
      [`GET ${WS}/ai/settings`]: settings({ enabled: false, provider_id: null, model: null }),
      [`GET ${WS}/conversations`]: { conversations: [CONVERSATION] },
      [`GET ${WS}/conversations/c1`]: { ...CONVERSATION, turns: [], left_out: 0, summary_dropped: false },
    })
    const w = await renderPage('/w/ws1/overview', '/w/:workspaceId/overview', Overview, 'member')
    await w.find('[aria-label="Open conversation: latency"]').trigger('click')
    await flushPromises()
    expect(text()).toContain('AI answers are off for this workspace, so this conversation is read-only')
    expect(w.find('#ask-input').exists()).toBe(false)
  })

  it('deletes a saved conversation after confirmation', async () => {
    let listed = [CONVERSATION]
    const fetchMock = mockApi({
      [`GET ${WS}/documents`]: { documents: [] },
      [`GET ${WS}/search/history`]: { searches: [] },
      [`GET ${WS}/ai/settings`]: settings(),
      [`GET ${WS}/conversations`]: () => new Reply(200, { conversations: listed }),
      [`DELETE ${WS}/conversations/c1`]: () => ((listed = []), new Response(null, { status: 204 })),
    })
    vi.stubGlobal('confirm', () => true)
    await renderPage('/w/ws1/overview', '/w/:workspaceId/overview', Overview, 'member')
    ;(document.querySelector('[aria-label="Delete conversation: latency"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(fetchMock.mock.calls.some(([p, i]) => p === `${WS}/conversations/c1` && i.method === 'DELETE')).toBe(true)
    expect(document.querySelector('[aria-label="Open conversation: latency"]')).toBeNull()
  })
})

describe('Overview with AI answers off', () => {
  it('asks through search and keeps the quote-only footer', async () => {
    const fetchMock = mockApi({
      [`GET ${WS}/documents`]: { documents: [] },
      [`GET ${WS}/search/history`]: { searches: [] },
      [`GET ${WS}/ai/settings`]: settings({ enabled: false, provider_id: null, model: null }),
      [`GET ${WS}/conversations`]: { conversations: [] },
      [`GET ${WS}/search`]: { results: [] },
    })
    const w = await renderPage('/w/ws1/overview', '/w/:workspaceId/overview', Overview, 'member')
    expect(text()).toContain('Answers quote approved documents. Nothing is generated.')
    await w.find('#ask-input').setValue('latency')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(fetchMock.mock.calls.some(([p]) => String(p).startsWith(`${WS}/search?`))).toBe(true)
    expect(fetchMock.mock.calls.some(([, i]) => i.method === 'POST')).toBe(false)
  })
})

describe('AI settings page', () => {
  const page = (role: 'admin' | 'member') => renderPage('/w/ws1/ai', '/w/:workspaceId/ai', AiSettings, role)

  it('shows a member the provider and model but no form and no key status', async () => {
    mockApi({ [`GET ${WS}/ai/settings`]: settings() })
    await page('member')
    expect(text()).toContain('OpenAI')
    expect(text()).toContain('GPT X')
    expect(document.querySelector('form')).toBeNull()
    expect(document.querySelector('input[type="password"]')).toBeNull()
  })

  it('shows an admin the form and the rejected-key warning, and empties the key field after saving', async () => {
    const fetchMock = mockApi({
      [`GET ${WS}/ai/settings`]: settings({ key_status: 'rejected' }),
      [`PUT ${WS}/ai/settings`]: settings({ key_status: 'ok' }),
    })
    const w = await page('admin')
    expect(text()).toContain('The provider rejected the saved key')
    await field(w, 'API key').setValue('sk-new')
    await w.find('form').trigger('submit')
    await flushPromises()
    const put = fetchMock.mock.calls.find(([, i]) => i.method === 'PUT')![1]
    expect(JSON.parse(put.body as string)).toEqual({ provider_id: 'openai', model: 'gpt-x', retention_days: 30, api_key: 'sk-new' })
    expect((field(w, 'API key').element as HTMLInputElement).value).toBe('')
    expect(text()).toContain('AI settings saved.')
  })

  it('shows how many conversations a shorter retention deletes and saves only after confirmation', async () => {
    const bodies: Record<string, unknown>[] = []
    mockApi({
      [`GET ${WS}/ai/settings`]: settings({ key_status: 'ok' }),
      [`PUT ${WS}/ai/settings`]: (init: RequestInit) => {
        bodies.push(JSON.parse(init.body as string))
        return bodies.length === 1
          ? new Reply(409, { detail: 'This deletes 3 saved conversations. Confirm to continue.', conversations_to_delete: 3 })
          : new Reply(200, settings({ key_status: 'ok', retention_days: 7 }))
      },
    })
    const confirm = vi.fn(() => true)
    vi.stubGlobal('confirm', confirm)
    const w = await page('admin')
    await field(w, 'Keep conversations for').setValue('7')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(confirm).toHaveBeenCalledWith('This deletes 3 saved conversations. Confirm to continue.')
    expect(bodies[1]).toMatchObject({ retention_days: 7, confirm_deletions: 3 })
    expect(text()).toContain('AI settings saved.')
  })
})
