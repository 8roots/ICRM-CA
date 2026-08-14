import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, test, vi } from 'vitest'
import App from '../src/App.vue'
import ApplicationDetailView from '../src/views/ApplicationDetailView.vue'
import ApplicationsView from '../src/views/ApplicationsView.vue'
import LoginView from '../src/views/LoginView.vue'

const user = { id: 'user-1', username: 'officer', role: 'approval_officer' }
const application = {
  id: 'application-1',
  primary_borrower: { type: 'corporate', name: '示例企业' },
  product: '经营贷',
  application_date: '2026-08-07',
  proposed_signing_date: null,
  owner_id: 'user-1',
  lifecycle_state: 'draft',
  version: 1,
}

const lifecycle = {
  state: 'draft',
  version: 1,
  editable: true,
  can_complete: false,
  can_archive: true,
  can_reopen: false,
  completion_blockers: [],
}

const individualApplication = {
  ...application,
  id: 'application-2',
  primary_borrower: { type: 'individual', name: '示例个人' },
  product: '个人经营贷',
}

afterEach(() => vi.restoreAllMocks())

async function mountApplicationDetail() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/applications', component: ApplicationsView },
      { path: '/applications/:id', component: ApplicationDetailView },
    ],
  })
  await router.push(`/applications/${application.id}`)
  await router.isReady()
  const wrapper = mount(ApplicationDetailView, { global: { plugins: [router, ElementPlus] } })
  await flushPromises()
  return wrapper
}

async function selectFiles(wrapper: ReturnType<typeof mount>, files: File[]) {
  const input = wrapper.get('input[type="file"]')
  Object.defineProperty(input.element, 'files', { value: files })
  await input.trigger('change')
  await flushPromises()
}

test('申请负责人批量上传材料并看到处理状态与带理由重试', async () => {
  document.cookie = 'icrm_csrf=test-csrf; path=/'
  const waiting = {
    id: 'document-1', application_id: application.id, filename: '流水.pdf', declared_mime: 'application/pdf',
    size_bytes: 12, sha256: 'a'.repeat(64), processing_status: 'waiting', review_status: 'not_ready',
    jobs: [{ id: 'job-1', document_id: 'document-1', status: 'waiting', attempts: 0, error_code: null, retry_reason: null, steps: [] }],
  }
  const failed = {
    ...waiting, id: 'document-2', filename: '损坏.pdf', processing_status: 'failed',
    jobs: [{ ...waiting.jobs[0], id: 'job-2', document_id: 'document-2', status: 'failed', error_code: 'signature_mismatch' }],
  }
  const fetchMock = vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(Response.json(application))
    .mockResolvedValueOnce(Response.json(lifecycle))
    .mockResolvedValueOnce(Response.json([]))
    .mockResolvedValueOnce(Response.json({ document: waiting, job: waiting.jobs[0] }, { status: 202 }))
    .mockResolvedValueOnce(Response.json({ document: failed, job: failed.jobs[0] }, { status: 202 }))
    .mockResolvedValueOnce(Response.json(lifecycle))
    .mockResolvedValueOnce(Response.json({ ...failed.jobs[0], status: 'waiting', retry_reason: '重新上传前人工确认' }))

  const wrapper = await mountApplicationDetail()
  await selectFiles(wrapper, [
    new File(['%PDF-1.7'], '流水.pdf', { type: 'application/pdf' }),
    new File(['bad'], '损坏.pdf', { type: 'application/pdf' }),
  ])
  expect(wrapper.text()).toContain('等待处理')
  expect(wrapper.text()).toContain('处理失败')

  await wrapper.get('[aria-label="重试原因-损坏.pdf"]').setValue('重新上传前人工确认')
  await wrapper.get('[aria-label="重试-损坏.pdf"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('重新上传前人工确认')
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/documents')).length).toBe(3)
})

test('重试请求失败时展示结果', async () => {
  const failed = {
    id: 'document-retry', application_id: application.id, filename: '重试.pdf', declared_mime: 'application/pdf',
    size_bytes: 12, sha256: 'e'.repeat(64), processing_status: 'failed', review_status: 'not_ready',
    jobs: [{ id: 'job-retry', document_id: 'document-retry', status: 'failed', attempts: 1, error_code: 'signature_mismatch', retry_reason: null, steps: [] }],
  }
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(Response.json(application))
    .mockResolvedValueOnce(Response.json(lifecycle))
    .mockResolvedValueOnce(Response.json([failed]))
    .mockResolvedValueOnce(Response.json({ detail: 'Only failed steps can be retried' }, { status: 409 }))
  const wrapper = await mountApplicationDetail()
  await wrapper.get('[aria-label="重试原因-重试.pdf"]').setValue('重试失败示例')
  await wrapper.get('[aria-label="重试-重试.pdf"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('Only failed steps can be retried')
})

test('批量上传中单份请求失败不阻塞其他材料且错误可见', async () => {
  const waiting = {
    id: 'document-ok', application_id: application.id, filename: '正常.pdf', declared_mime: 'application/pdf',
    size_bytes: 12, sha256: 'c'.repeat(64), processing_status: 'waiting', review_status: 'not_ready',
    jobs: [{ id: 'job-ok', document_id: 'document-ok', status: 'waiting', attempts: 0, error_code: null, retry_reason: null, steps: [] }],
  }
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(Response.json(application))
    .mockResolvedValueOnce(Response.json(lifecycle))
    .mockResolvedValueOnce(Response.json([]))
    .mockResolvedValueOnce(Response.json({ document: waiting, job: waiting.jobs[0] }, { status: 202 }))
    .mockResolvedValueOnce(Response.json({ detail: 'Material size limit exceeded' }, { status: 413 }))
  const wrapper = await mountApplicationDetail()
  await selectFiles(wrapper, [new File(['ok'], '正常.pdf'), new File(['large'], '超限.pdf')])
  expect(wrapper.text()).toContain('正常.pdf')
  expect(wrapper.text()).toContain('超限.pdf')
  expect(wrapper.text()).toContain('Material size limit exceeded')
})

test('轮询展示 worker 从等待到运行再到成功的状态', async () => {
  vi.useFakeTimers()
  const base = {
    id: 'document-progress', application_id: application.id, filename: '进度.pdf', declared_mime: 'application/pdf',
    size_bytes: 12, sha256: 'd'.repeat(64), review_status: 'not_ready', jobs: [],
  }
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(Response.json(application))
    .mockResolvedValueOnce(Response.json(lifecycle))
    .mockResolvedValueOnce(Response.json([{ ...base, processing_status: 'waiting' }]))
    .mockResolvedValueOnce(Response.json([{ ...base, processing_status: 'running' }]))
    .mockResolvedValueOnce(Response.json([{ ...base, processing_status: 'success' }]))
  const wrapper = await mountApplicationDetail()
  expect(wrapper.text()).toContain('等待处理')
  await vi.advanceTimersByTimeAsync(3000)
  await flushPromises()
  expect(wrapper.text()).toContain('处理中')
  await vi.advanceTimersByTimeAsync(3000)
  await flushPromises()
  expect(wrapper.text()).toContain('处理成功')
  wrapper.unmount()
  vi.useRealTimers()
})

test('审批人员登录、创建申请并在列表中看到该申请', async () => {
  document.cookie = 'icrm_csrf=test-csrf; path=/'
  const fetchMock = vi.spyOn(globalThis, 'fetch')
  fetchMock
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
    .mockResolvedValueOnce(Response.json(user))
    .mockResolvedValueOnce(Response.json([]))
    .mockResolvedValueOnce(Response.json(application, { status: 201 }))
    .mockResolvedValueOnce(Response.json(individualApplication, { status: 201 }))

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', component: LoginView },
      { path: '/applications', component: ApplicationsView },
      { path: '/applications/:id', component: ApplicationDetailView },
    ],
  })
  await router.push('/login')
  await router.isReady()

  const wrapper = mount(App, {
    global: { plugins: [createPinia(), router, ElementPlus] },
  })
  await wrapper.get('[aria-label="用户名"]').setValue('officer')
  await wrapper.get('[aria-label="密码"]').setValue('approval officer password')
  await wrapper.get('form').trigger('submit')
  await flushPromises()

  expect(router.currentRoute.value.path).toBe('/applications')
  await wrapper.get('[aria-label="主借款人名称"]').setValue('示例企业')
  await wrapper.get('[aria-label="产品"]').setValue('经营贷')
  await wrapper.get('[aria-label="申请日期"]').setValue('2026-08-07')
  await wrapper.get('form').trigger('submit')
  await flushPromises()

  expect(wrapper.text()).toContain('示例企业')
  expect(wrapper.text()).toContain('经营贷')

  await wrapper.get('[aria-label="主借款人类型"]').setValue('individual')
  await wrapper.get('[aria-label="主借款人名称"]').setValue('示例个人')
  await wrapper.get('[aria-label="产品"]').setValue('个人经营贷')
  await wrapper.get('form').trigger('submit')
  await flushPromises()

  expect(wrapper.text()).toContain('示例个人')
  expect(wrapper.text()).toContain('个人经营贷')
  expect(fetchMock.mock.calls[3]?.[1]).toMatchObject({ method: 'POST' })
  expect(fetchMock.mock.calls[4]?.[1]).toMatchObject({ method: 'POST' })
})
