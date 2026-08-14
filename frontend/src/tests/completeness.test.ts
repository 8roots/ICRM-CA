import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, test, vi } from 'vitest'
import CompletenessView from '../views/CompletenessView.vue'

const items = [
  {
    id: 'item-license',
    code: 'license',
    label: '营业执照',
    category: 'basic_info',
    category_label: '基础信息',
    order: 1,
    requires_seal: true,
    requires_signature: false,
    condition: null,
    condition_label: null,
    state: 'pending_confirmation',
    state_label: '待确认',
    evidence_document_ids: ['doc-1'],
    reason: '待人工确认',
  },
  {
    id: 'item-purpose',
    code: 'purpose_contract',
    label: '购销合同或用途证明材料',
    category: 'purpose',
    category_label: '用途',
    order: 2,
    requires_seal: false,
    requires_signature: false,
    condition: null,
    condition_label: null,
    state: 'missing',
    state_label: '缺失',
    evidence_document_ids: [],
    reason: '缺失',
  },
  {
    id: 'item-collateral',
    code: 'collateral_certificate',
    label: '抵押物权证',
    category: 'collateral',
    category_label: '抵押担保',
    order: 3,
    requires_seal: false,
    requires_signature: false,
    condition: { requires: 'collateral' },
    condition_label: '存在抵押物时适用',
    state: 'not_applicable',
    state_label: '不适用',
    evidence_document_ids: [],
    reason: '不适用',
  },
  {
    id: 'item-waived',
    code: 'credit_report',
    label: '企业信用报告',
    category: 'credit',
    category_label: '征信',
    order: 4,
    requires_seal: false,
    requires_signature: false,
    condition: null,
    condition_label: null,
    state: 'manually_waived',
    state_label: '人工豁免',
    evidence_document_ids: [],
    reason: '人工豁免',
  },
]

function draftBody(latestRun: unknown = null): unknown {
  return {
    template: {
      code: 'DEMO-CORP-OPERATING',
      name: '演示模板：企业经营贷',
      product: '经营贷',
      borrower_type: 'corporate',
      version: 1,
      demo_only: true,
      items: [],
    },
    no_template_reason: null,
    items,
    documents: [
      {
        id: 'doc-1',
        filename: '执照扫描.pdf',
        confirmed_category: 'basic_info',
        classification_candidates: [
          { category: 'basic_info', category_label: '基础信息', confidence: 0.9, method: 'content_keyword', method_version: '1' },
        ],
        seal_confirmed: false,
        signature_confirmed: false,
      },
    ],
    mappings: [{ id: 'map-1', document_id: 'doc-1', document_filename: '执照扫描.pdf', item_id: 'item-license', item_code: 'license', item_label: '营业执照', actor_id: 'user-1', created_at: '2026-08-07T00:00:00Z' }],
    waivers: [{ id: 'waiver-1', item_id: 'item-waived', item_code: 'credit_report', item_label: '企业信用报告', reason: '客户暂未提供', actor_id: 'user-1', created_at: '2026-08-07T00:00:00Z' }],
    condition_context: { collateral: false, guarantor: false },
    latest_run: latestRun,
    formal_run_blocked_reason: null,
  }
}

function json(body: unknown): Response {
  return Response.json(body)
}

afterEach(() => vi.restoreAllMocks())

async function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/applications/:id/completeness', component: CompletenessView }],
  })
  await router.push('/applications/app-1/completeness')
  await router.isReady()
  const wrapper = mount(CompletenessView, { global: { plugins: [router, ElementPlus] } })
  await flushPromises()
  return wrapper
}

/** Mount with a stable draft; every later fetch returns a fresh copy. */
const runs = [
  {
    id: 'run-historical',
    created_at: '2026-08-06T00:00:00Z',
    status: 'stale',
    stale: true,
    stale_reason: 'mapping_change',
    content_hash: '999999999999',
    template_code: 'DEMO-CORP-OPERATING',
    template_version: 1,
    actor_id: 'user-1',
  },
]

function mockDraft(latestRun: unknown = null) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    if (url.includes('/completeness-runs')) return json(runs)
    if (url.includes('/lifecycle')) {
      return json({ state: 'pending_review', version: 1, editable: true, can_complete: true, can_archive: true, can_reopen: false, completion_blockers: [] })
    }
    return json(draftBody(latestRun))
  })
}

test('草稿展示模板、清单项状态汇总与材料分类候选', async () => {
  document.cookie = 'icrm_csrf=test-csrf; path=/'
  mockDraft()
  const wrapper = await mountView()

  expect(wrapper.text()).toContain('材料完备性与正式报告')
  expect(wrapper.text()).toContain('DEMO-CORP-OPERATING')
  expect(wrapper.text()).toContain('演示模板')
  expect(wrapper.text()).toContain('营业执照')
  expect(wrapper.text()).toContain('待确认')
  expect(wrapper.text()).toContain('存在抵押物时适用')
  expect(wrapper.text()).toContain('人工豁免')
  expect(wrapper.text()).toContain('已满足 0')
  expect(wrapper.text()).toContain('缺失 1')
  expect(wrapper.text()).toContain('待确认 1')
  expect(wrapper.text()).toContain('不适用 1')
  expect(wrapper.text()).toContain('人工豁免 1')
  expect(wrapper.text()).toContain('执照扫描.pdf')
  expect(wrapper.text()).toContain('基础信息（0.90）')
  expect(wrapper.text()).toContain('客户暂未提供')
})

test('已确认映射可删除，并携带 CSRF 请求', async () => {
  document.cookie = 'icrm_csrf=test-csrf; path=/'
  const fetchMock = mockDraft()
  const wrapper = await mountView()

  await wrapper.get('[aria-label="删除映射-执照扫描.pdf-营业执照"]').trigger('click')
  await flushPromises()

  const del = fetchMock.mock.calls.find(([, init]) => init?.method === 'DELETE')
  expect(del?.[0]).toBe('/api/v1/applications/app-1/mappings/map-1')
  expect((del?.[1]?.headers as Headers)?.get('x-csrf-token')).toBe('test-csrf')
})

test('人工豁免必须填写理由', async () => {
  document.cookie = 'icrm_csrf=test-csrf; path=/'
  const fetchMock = mockDraft()
  const wrapper = await mountView()

  await wrapper.get('[aria-label="人工豁免-购销合同或用途证明材料"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('人工豁免必须填写理由')

  await wrapper.get('[aria-label="豁免理由-购销合同或用途证明材料"]').setValue('线下已核实原件')
  await wrapper.get('[aria-label="人工豁免-购销合同或用途证明材料"]').trigger('click')
  await flushPromises()

  const post = fetchMock.mock.calls.find(
    ([input, init]) => String(input).includes('/waivers') && init?.method === 'POST',
  )
  expect(JSON.parse(String(post?.[1]?.body))).toEqual({
    item_id: 'item-purpose',
    reason: '线下已核实原件',
  })
})

test('确认分类提交所选类别', async () => {
  document.cookie = 'icrm_csrf=test-csrf; path=/'
  const fetchMock = mockDraft()
  const wrapper = await mountView()

  // Drive the classification selection state directly (the Element Plus
  // dropdown is teleported to <body> and is flaky under jsdom).
  const vm = wrapper.vm as unknown as { classificationSelections: Record<string, string> }
  vm.classificationSelections['doc-1'] = 'basic_info'
  await wrapper.get('[aria-label="确认分类-执照扫描.pdf"]').trigger('click')
  await flushPromises()

  const post = fetchMock.mock.calls.find(
    ([input, init]) => String(input).includes('/classification') && init?.method === 'POST',
  )
  expect(post?.[0]).toBe('/api/v1/applications/app-1/documents/doc-1/classification')
  expect(JSON.parse(String(post?.[1]?.body))).toEqual({ category: 'basic_info' })
})

test('正式检查按钮禁用并展示阻断原因（生产模式拒绝演示模板）', async () => {
  document.cookie = 'icrm_csrf=test-csrf; path=/'
  const blocked = { ...(draftBody() as object), formal_run_blocked_reason: '生产模式拒绝使用演示模板生成正式报告' }
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    if (String(input).includes('/completeness-runs')) return json(runs)
    return json(blocked)
  })
  const wrapper = await mountView()

  expect(wrapper.text()).toContain('生产模式拒绝使用演示模板生成正式报告')
  const button = wrapper.get('[aria-label="执行正式完备性检查"]')
  expect(button.classes()).toContain('is-disabled')
})

test('正式检查执行并展示最新报告状态', async () => {
  document.cookie = 'icrm_csrf=test-csrf; path=/'
  const latestRun = {
    id: 'run-1',
    created_at: '2026-08-07T00:00:00Z',
    status: 'current',
    stale: false,
    stale_reason: null,
    content_hash: 'abc123def456',
    template_code: 'DEMO-CORP-OPERATING',
    template_version: 1,
    actor_id: 'user-1',
  }
  const fetchMock = mockDraft(latestRun)
  const wrapper = await mountView()

  await wrapper.get('[aria-label="执行正式完备性检查"]').trigger('click')
  await flushPromises()

  const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
  expect(post?.[0]).toBe('/api/v1/applications/app-1/completeness-runs')
  expect(wrapper.text()).toContain('有效')
  expect(wrapper.text()).toContain('abc123def456')
})

test('历史报告展示失效原因', async () => {
  document.cookie = 'icrm_csrf=test-csrf; path=/'
  mockDraft()
  const wrapper = await mountView()

  expect(wrapper.text()).toContain('已失效')
  expect(wrapper.text()).toContain('证据映射已变化')
  expect(wrapper.text()).toContain('999999999999')
})

test('无适用模板时提示且不渲染清单', async () => {
  document.cookie = 'icrm_csrf=test-csrf; path=/'
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    if (url.includes('/completeness-runs')) return json([])
    if (url.includes('/lifecycle')) {
      return json({ state: 'pending_review', version: 1, editable: true, can_complete: true, can_archive: true, can_reopen: false, completion_blockers: [] })
    }
    return json({
      template: null,
      no_template_reason: '没有适用于该产品与主借款人类型的已发布模板，无法生成正式报告',
      items: [],
      documents: [],
      mappings: [],
      waivers: [],
      condition_context: { collateral: false, guarantor: false },
      latest_run: null,
      formal_run_blocked_reason: '无已发布适用模板',
    })
  })
  const wrapper = await mountView()

  expect(wrapper.text()).toContain('没有适用于该产品与主借款人类型的已发布模板')
  expect(wrapper.text()).not.toContain('清单项（实时草稿）')
})
