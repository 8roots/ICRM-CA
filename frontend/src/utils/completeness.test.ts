import { describe, expect, it } from 'vitest'
import type {
  CompletenessDocumentResponse,
  CompletenessItemResponse,
  LiveDraftResponse,
} from '../api/client'
import {
  categoryLabel,
  conditionLabel,
  staleReasonLabel,
  suggestedDocumentsForItem,
  suggestedItemsForDocument,
  summaryOf,
} from './completeness'

function item(overrides: Partial<CompletenessItemResponse>): CompletenessItemResponse {
  return {
    code: 'license',
    label: '营业执照',
    category: 'basic_info',
    category_label: '基础信息',
    order: 1,
    requires_seal: false,
    requires_signature: false,
    condition: null,
    condition_label: null,
    state: 'missing',
    state_label: '缺失',
    evidence_document_ids: [],
    reason: '',
    id: 'item-1',
    ...overrides,
  }
}

function document(overrides: Partial<CompletenessDocumentResponse>): CompletenessDocumentResponse {
  return {
    id: 'doc-1',
    filename: '执照.pdf',
    confirmed_category: 'basic_info',
    classification_candidates: [],
    seal_confirmed: false,
    signature_confirmed: false,
    ...overrides,
  }
}

describe('labels', () => {
  it('renders category and condition labels', () => {
    expect(categoryLabel('basic_info')).toBe('基础信息')
    expect(categoryLabel(null)).toBe('未确认')
    expect(categoryLabel('unknown')).toBe('unknown')
    expect(conditionLabel({ requires: 'collateral' })).toBe('存在抵押物时适用')
    expect(conditionLabel(null)).toBeNull()
    expect(conditionLabel({ requires: 'nope' })).toBeNull()
  })

  it('renders stale reasons', () => {
    expect(staleReasonLabel('mapping_change')).toBe('证据映射已变化')
    expect(staleReasonLabel('template_changed')).toBe('适用模板版本已变化')
    expect(staleReasonLabel(null)).toBeNull()
  })
})

describe('mapping suggestions', () => {
  const items = [
    item({ id: 'item-license', code: 'license', category: 'basic_info' }),
    item({ id: 'item-purpose', code: 'purpose_contract', category: 'purpose' }),
  ]

  it('suggests items matching a confirmed document category', () => {
    const documents = [document({ id: 'doc-1', confirmed_category: 'basic_info' })]
    const suggestions = suggestedItemsForDocument(items, documents[0], [])
    expect(suggestions.map((s) => s.id)).toEqual(['item-license'])
  })

  it('excludes already-mapped items', () => {
    const documents = [document({ id: 'doc-1', confirmed_category: 'basic_info' })]
    const suggestions = suggestedItemsForDocument(items, documents[0], [
      { document_id: 'doc-1', item_id: 'item-license' },
    ])
    expect(suggestions).toEqual([])
  })

  it('returns nothing for unconfirmed documents', () => {
    const documents = [document({ id: 'doc-1', confirmed_category: null })]
    expect(suggestedItemsForDocument(items, documents[0], [])).toEqual([])
  })

  it('suggests documents matching an item category', () => {
    const documents = [
      document({ id: 'doc-1', confirmed_category: 'basic_info' }),
      document({ id: 'doc-2', confirmed_category: 'purpose' }),
    ]
    const suggestions = suggestedDocumentsForItem(documents, items[0], [])
    expect(suggestions.map((s) => s.id)).toEqual(['doc-1'])
  })

  it('excludes documents already mapped to the item', () => {
    const documents = [document({ id: 'doc-1', confirmed_category: 'basic_info' })]
    const suggestions = suggestedDocumentsForItem(documents, items[0], [
      { document_id: 'doc-1', item_id: 'item-license' },
    ])
    expect(suggestions).toEqual([])
  })
})

describe('summaryOf', () => {
  it('counts every documented state', () => {
    const draft: LiveDraftResponse = {
      template: null,
      no_template_reason: null,
      items: [
        item({ state: 'satisfied' }),
        item({ state: 'missing' }),
        item({ state: 'pending_confirmation' }),
        item({ state: 'not_applicable' }),
        item({ state: 'manually_waived' }),
        item({ state: 'missing' }),
      ],
      documents: [],
      mappings: [],
      waivers: [],
      condition_context: { collateral: false, guarantor: false },
      latest_run: null,
      formal_run_blocked_reason: null,
    }
    expect(summaryOf(draft)).toEqual({
      satisfied: 1,
      missing: 2,
      pending: 1,
      notApplicable: 1,
      waived: 1,
      total: 6,
    })
  })
})
