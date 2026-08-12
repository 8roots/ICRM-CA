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
const individualApplication = {
  ...application,
  id: 'application-2',
  primary_borrower: { type: 'individual', name: '示例个人' },
  product: '个人经营贷',
}

afterEach(() => vi.restoreAllMocks())

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
