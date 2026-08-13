import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, test, vi } from 'vitest'
import DocumentEvidenceView from '../views/DocumentEvidenceView.vue'

const output = {
  id: 'output-2',
  document_id: 'document-1',
  version: 2,
  status: 'partial_success',
  parser_version: 'PyMuPDF-1.28.2',
  model_version: 'paddleocr-3.7.0-test',
  pages: [
    {
      id: 'page-1',
      number: 1,
      width: 300,
      height: 200,
      status: 'success',
      error_code: null,
      blocks: [
        {
          id: 'block-1',
          order: 0,
          kind: 'paragraph',
          text: '某某企业年度财务报表',
          bbox: [10, 20, 200, 50],
          extraction_method: 'ocr',
          confidence: 0.99,
          cells: [],
        },
      ],
      seals: [
        {
          id: 'seal-1',
          text: '合同专用章',
          bbox: [60, 80, 200, 160],
          confidence: 0.93,
          model_version: 'paddleocr-3.7.0-test',
        },
      ],
    },
    {
      id: 'page-2',
      number: 2,
      width: 300,
      height: 200,
      status: 'failed',
      error_code: 'page_analysis_failed',
      blocks: [],
      seals: [],
    },
  ],
}
const jobs = [
  {
    id: 'job-1',
    document_id: 'document-1',
    status: 'partial_success',
    attempts: 1,
    error_code: 'partial_page_failure',
    retry_reason: null,
    steps: [
      { name: 'validation', status: 'success', error_code: null },
      { name: 'parsing_ocr', status: 'partial_success', error_code: 'partial_page_failure' },
      { name: 'seal_detection', status: 'partial_success', error_code: 'partial_page_failure' },
    ],
  },
]

afterEach(() => vi.restoreAllMocks())

async function mountEvidence() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/documents/:id/evidence', component: DocumentEvidenceView }],
  })
  await router.push('/documents/document-1/evidence')
  await router.isReady()
  const wrapper = mount(DocumentEvidenceView, { global: { plugins: [router, ElementPlus] } })
  await flushPromises()
  return wrapper
}

function imageResponse() {
  return new Response(new Blob(['png'], { type: 'image/png' }), { status: 200 })
}

test('展示最新输出版本、文字块、印章候选与候选措辞，不宣称真实性', async () => {
  document.cookie = 'icrm_csrf=test-csrf; path=/'
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(Response.json([output]))
    .mockResolvedValueOnce(Response.json(jobs))
    .mockResolvedValueOnce(Response.json([]))
    .mockResolvedValueOnce(imageResponse())

  const wrapper = await mountEvidence()

  expect(wrapper.text()).toContain('v2')
  expect(wrapper.text()).toContain('某某企业年度财务报表')
  expect(wrapper.text()).toContain('印章候选（未经人工确认）')
  expect(wrapper.text()).toContain('合同专用章')
  expect(wrapper.text()).toContain('不判断印章真实性、归属或法律效力')
  expect(wrapper.text()).not.toContain('真实有效')
  expect(wrapper.text()).toContain('部分页面解析失败')
})

test('选中文字块后高亮对应原页区域', async () => {
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(Response.json([output]))
    .mockResolvedValueOnce(Response.json(jobs))
    .mockResolvedValueOnce(Response.json([]))
    .mockResolvedValueOnce(imageResponse())

  const wrapper = await mountEvidence()
  const box = wrapper.get('[aria-label="文字块-某某企业年度财务报表"]')
  expect(box.classes()).not.toContain('selected')
  await wrapper.get('[aria-label="定位文字块-某某企业年度财务报表"]').trigger('click')
  await flushPromises()
  expect(wrapper.get('[aria-label="文字块-某某企业年度财务报表"]').classes()).toContain('selected')
})

test('提交印章候选确认与签字人工确认', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(Response.json([output]))
    .mockResolvedValueOnce(Response.json(jobs))
    .mockResolvedValueOnce(Response.json([]))
    .mockResolvedValueOnce(imageResponse())
    .mockResolvedValueOnce(Response.json({
      id: 'review-seal',
      output_id: 'output-2',
      seal_candidate_id: 'seal-1',
      kind: 'seal_presence',
      status: 'present',
      reason: '原页可见红色印章',
      actor_id: 'user-1',
      created_at: '2026-08-07T00:00:00Z',
    }, { status: 201 }))
    .mockResolvedValueOnce(Response.json({
      id: 'review-signature',
      output_id: 'output-2',
      seal_candidate_id: null,
      kind: 'signature_presence',
      status: 'absent',
      reason: '原页未见签字',
      actor_id: 'user-1',
      created_at: '2026-08-07T00:00:00Z',
    }, { status: 201 }))

  const wrapper = await mountEvidence()

  await wrapper.get('[aria-label="确认印章候选-合同专用章"]').trigger('click')
  await flushPromises()
  await wrapper.get('[aria-label="印章确认理由"]').setValue('原页可见红色印章')
  await wrapper.get('[aria-label="提交印章确认"]').trigger('click')
  await flushPromises()

  await wrapper.get('[aria-label="签字确认理由"]').setValue('原页未见签字')
  const signatureCard = wrapper.findAll('.review-card')[1]
  await signatureCard.find('input[value="absent"]').setValue(true)
  await wrapper.get('[aria-label="提交签字确认"]').trigger('click')
  await flushPromises()

  const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')
  expect(posts).toHaveLength(2)
  const [sealCall, signatureCall] = posts.map(([, init]) => JSON.parse(String(init?.body)))
  expect(sealCall).toEqual({
    kind: 'seal_presence',
    status: 'present',
    seal_candidate_id: 'seal-1',
    reason: '原页可见红色印章',
  })
  expect(signatureCall).toEqual({
    kind: 'signature_presence',
    status: 'absent',
    seal_candidate_id: null,
    reason: '原页未见签字',
  })
  expect(wrapper.text()).toContain('原页可见红色印章')
})
