import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, test, vi } from 'vitest'
import AdminTemplatesView from '../views/AdminTemplatesView.vue'

const templates = [
  {
    id: 'template-1',
    code: 'CORP-OPERATING-2026',
    name: '企业流动资金贷清单',
    product: '流动资金贷',
    borrower_type: 'corporate',
    version: 1,
    status: 'published',
    demo_only: false,
    content_hash: 'aabbccddeeff00112233445566778899',
    published_at: '2026-08-07T00:00:00Z',
    retired_at: null,
    created_at: '2026-08-07T00:00:00Z',
    items: [
      {
        code: 'license',
        label: '营业执照',
        category: 'basic_info',
        category_label: '基础信息',
        order: 1,
        requires_seal: true,
        requires_signature: false,
        condition: null,
      },
    ],
  },
  {
    id: 'template-2',
    code: 'CORP-OPERATING-2026',
    name: '企业流动资金贷清单',
    product: '流动资金贷',
    borrower_type: 'corporate',
    version: 2,
    status: 'draft',
    demo_only: false,
    content_hash: '11223344556677889900aabbccddeeff',
    published_at: null,
    retired_at: null,
    created_at: '2026-08-07T00:00:00Z',
    items: [
      {
        code: 'license',
        label: '营业执照',
        category: 'basic_info',
        category_label: '基础信息',
        order: 1,
        requires_seal: true,
        requires_signature: false,
        condition: null,
      },
    ],
  },
]

afterEach(() => vi.restoreAllMocks())

async function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/admin/templates', component: AdminTemplatesView }],
  })
  await router.push('/admin/templates')
  await router.isReady()
  const wrapper = mount(AdminTemplatesView, { global: { plugins: [router, ElementPlus] } })
  await flushPromises()
  return wrapper
}

test('模板按编码分组展示版本与状态，草稿可发布、已发布可复制或停用', async () => {
  document.cookie = 'icrm_csrf=test-csrf; path=/'
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json(templates))
  const wrapper = await mountView()

  expect(wrapper.text()).toContain('完备性模板管理')
  expect(wrapper.text()).toContain('CORP-OPERATING-2026')
  expect(wrapper.text()).toContain('已发布')
  expect(wrapper.text()).toContain('草稿')
  expect(wrapper.findAll('[aria-label="复制-CORP-OPERATING-2026-v1"]').length).toBe(1)
  expect(wrapper.findAll('[aria-label="停用-CORP-OPERATING-2026-v1"]').length).toBe(1)
  expect(wrapper.findAll('[aria-label="发布-CORP-OPERATING-2026-v2"]').length).toBe(1)
  expect(wrapper.text()).toContain('需印章')
  expect(wrapper.text()).toContain('基础信息')
})

test('发布草稿版本调用发布接口', async () => {
  document.cookie = 'icrm_csrf=test-csrf; path=/'
  const fetchMock = vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation(async () => Response.json(templates))
  const wrapper = await mountView()

  await wrapper.get('[aria-label="发布-CORP-OPERATING-2026-v2"]').trigger('click')
  await flushPromises()

  const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
  expect(post?.[0]).toBe('/api/v1/admin/completeness-templates/template-2/publish')
  expect((post?.[1]?.headers as Headers)?.get('x-csrf-token')).toBe('test-csrf')
})

test('新建模板表单校验必填项并提交清单项', async () => {
  document.cookie = 'icrm_csrf=test-csrf; path=/'
  const fetchMock = vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation(async () => Response.json(templates))
  const wrapper = await mountView()

  await wrapper.get('[aria-label="新建模板"]').trigger('click')
  await flushPromises()
  await wrapper.get('[aria-label="提交新建模板"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('请填写模板编码、名称与产品')

  await wrapper.get('[aria-label="模板编码"]').setValue('IND-OPERATING-2026')
  await wrapper.get('[aria-label="模板名称"]').setValue('个人经营贷清单')
  await wrapper.get('[aria-label="清单项编号-0"]').setValue('id_card')
  await wrapper.get('[aria-label="清单项名称-0"]').setValue('身份证')
  await wrapper.get('[aria-label="提交新建模板"]').trigger('click')
  await flushPromises()

  const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
  expect(post?.[0]).toBe('/api/v1/admin/completeness-templates')
  const payload = JSON.parse(String(post?.[1]?.body))
  expect(payload.code).toBe('IND-OPERATING-2026')
  expect(payload.items[0]).toMatchObject({ code: 'id_card', label: '身份证', category: 'basic_info' })
})
