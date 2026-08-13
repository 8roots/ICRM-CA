import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, test, vi } from 'vitest'
import CandidateReviewView from '../views/CandidateReviewView.vue'

const candidates = [
  {
    id: 'c-low',
    document_id: 'doc-1',
    filename: '材料.md',
    output_id: 'output-1',
    output_version: 1,
    field_key: 'loan_purpose',
    field_label: '贷款用途',
    group: 'proposed_loan',
    critical: false,
    subject_role: 'primary_borrower',
    subject_label: '主借款人',
    raw_text: '补充流动资金',
    typed_value: { type: 'text', value: '补充流动资金' },
    confidence: 0.5,
    extractor: 'local_rule',
    extractor_version: 'icrm-local-rules-1',
    model_version: 'none',
    prompt_version: null,
    source_refs: [
      {
        document_id: 'doc-1',
        output_id: 'output-1',
        output_version: 1,
        page_number: null,
        block_id: 'block-2',
        block_order: 2,
        cell_id: null,
        locator: { kind: 'markdown', line_start: 8 },
      },
    ],
  },
  {
    id: 'c-high',
    document_id: 'doc-1',
    filename: '材料.md',
    output_id: 'output-1',
    output_version: 1,
    field_key: 'loan_amount',
    field_label: '贷款金额',
    group: 'proposed_loan',
    critical: true,
    subject_role: 'primary_borrower',
    subject_label: '主借款人',
    raw_text: '800万元',
    typed_value: { type: 'amount', value: '8000000', currency: 'CNY', unit: '10000' },
    confidence: 0.92,
    extractor: 'local_rule',
    extractor_version: 'icrm-local-rules-1',
    model_version: 'none',
    prompt_version: null,
    source_refs: [
      {
        document_id: 'doc-1',
        output_id: 'output-1',
        output_version: 1,
        page_number: null,
        block_id: 'block-1',
        block_order: 1,
        cell_id: null,
        locator: { kind: 'markdown', line_start: 5 },
      },
    ],
  },
]

afterEach(() => vi.restoreAllMocks())

async function mountReview() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/applications/:id/candidates', component: CandidateReviewView }],
  })
  await router.push('/applications/app-1/candidates')
  await router.isReady()
  const wrapper = mount(CandidateReviewView, { global: { plugins: [router, ElementPlus] } })
  await flushPromises()
  return wrapper
}

function baseMocks() {
  return vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(Response.json(candidates))
    .mockResolvedValueOnce(Response.json([]))
    .mockResolvedValueOnce(Response.json([]))
}

test('候选按置信度降序展示，并标注字段、来源与置信度', async () => {
  document.cookie = 'icrm_csrf=test-csrf; path=/'
  baseMocks()
  const wrapper = await mountReview()

  expect(wrapper.text()).toContain('字段候选复核与人工确认')
  expect(wrapper.text()).toContain('贷款金额')
  expect(wrapper.text()).toContain('贷款用途')
  expect(wrapper.text()).toContain('关键')
  expect(wrapper.text()).toContain('人民币8000000万元')
  expect(wrapper.text()).toContain('本地规则')
  expect(wrapper.text()).toContain('0.92')
  const rows = wrapper.findAll('.el-table__body tr').map((row) => row.text())
  const highIndex = rows.findIndex((text) => text.includes('贷款金额'))
  const lowIndex = rows.findIndex((text) => text.includes('贷款用途'))
  expect(highIndex).toBeGreaterThan(-1)
  expect(lowIndex).toBeGreaterThan(highIndex)
  expect(wrapper.text()).toContain('无材料来源')
  expect(wrapper.text()).toContain('尚未调用云端抽取')
})

test('人工录入必须填写理由，提交时携带无来源标记', async () => {
  document.cookie = 'icrm_csrf=test-csrf; path=/'
  const fetchMock = baseMocks()
    .mockResolvedValueOnce(
      Response.json(
        {
          id: 'resolution-1',
          application_id: 'app-1',
          candidate_id: null,
          field_key: 'loan_purpose',
          field_label: '贷款用途',
          subject_role: 'primary_borrower',
          resolution_type: 'manual',
          typed_value: { type: 'text', value: '补充流动资金' },
          no_material_source: true,
          reason: '电话与客户确认',
          actor_id: 'user-1',
          created_at: '2026-08-07T00:00:00Z',
        },
        { status: 201 },
      ),
    )
  const wrapper = await mountReview()

  await wrapper.get('[aria-label="人工录入确认值"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('没有材料出处')
  await wrapper.get('[aria-label="确认值"]').setValue('补充流动资金')
  await wrapper.get('[aria-label="提交确认"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('人工录入值必须填写理由')

  await wrapper.get('[aria-label="人工录入理由"]').setValue('电话与客户确认')
  await wrapper.get('[aria-label="提交确认"]').trigger('click')
  await flushPromises()

  const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
  expect(JSON.parse(String(post?.[1]?.body))).toEqual({
    resolution_type: 'manual',
    field_key: 'loan_purpose',
    candidate_id: null,
    value: '补充流动资金',
    reason: '电话与客户确认',
  })
  expect(wrapper.text()).toContain('电话与客户确认')
})

test('采用候选提交 selected 确认记录', async () => {
  document.cookie = 'icrm_csrf=test-csrf; path=/'
  const fetchMock = baseMocks().mockResolvedValueOnce(
    Response.json(
      {
        id: 'resolution-2',
        application_id: 'app-1',
        candidate_id: 'c-high',
        field_key: 'loan_amount',
        field_label: '贷款金额',
        subject_role: 'primary_borrower',
        resolution_type: 'selected',
        typed_value: { type: 'amount', value: '8000000', currency: 'CNY', unit: '10000' },
        no_material_source: false,
        reason: null,
        actor_id: 'user-1',
        created_at: '2026-08-07T00:00:00Z',
      },
      { status: 201 },
    ),
  )
  const wrapper = await mountReview()

  await wrapper.get('[aria-label="采用候选-贷款金额"]').trigger('click')
  await flushPromises()
  await wrapper.get('[aria-label="提交确认"]').trigger('click')
  await flushPromises()

  const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
  const body = JSON.parse(String(post?.[1]?.body)) as { resolution_type: string; candidate_id: string }
  expect(body.resolution_type).toBe('selected')
  expect(body.candidate_id).toBe('c-high')
  expect(wrapper.text()).toContain('采用候选')
})
