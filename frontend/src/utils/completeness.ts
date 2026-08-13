import type {
  CompletenessDocumentResponse,
  CompletenessItemResponse,
  LiveDraftResponse,
} from '../api/client'

export const categoryLabels: Record<string, string> = {
  basic_info: '基础信息',
  operation: '经营',
  loan_application: '贷款申请',
  purpose: '用途',
  credit: '征信',
  collateral: '抵押担保',
  other: '其他',
}

export const stateLabels: Record<string, string> = {
  satisfied: '已满足',
  missing: '缺失',
  pending_confirmation: '待确认',
  not_applicable: '不适用',
  manually_waived: '人工豁免',
}

export const conditionLabels: Record<string, string> = {
  collateral: '存在抵押物时适用',
  guarantor: '存在保证人时适用',
}

export const runStaleLabels: Record<string, string> = {
  mapping_change: '证据映射已变化',
  waiver_change: '人工豁免已变化',
  classification_change: '材料分类已变化',
  evidence_review_change: '印章/签字确认已变化',
  condition_context_change: '条件上下文已变化',
  template_changed: '适用模板版本已变化',
  new_run: '已生成新报告',
}

export function categoryLabel(category: string | null | undefined): string {
  return category ? (categoryLabels[category] ?? category) : '未确认'
}

export function conditionLabel(condition: Record<string, unknown> | null | undefined): string | null {
  if (!condition || typeof condition.requires !== 'string') return null
  return conditionLabels[condition.requires] ?? null
}

/** Items suggested for a document based on its confirmed category. */
export function suggestedItemsForDocument(
  items: CompletenessItemResponse[],
  document: CompletenessDocumentResponse,
  mappings: { document_id: string; item_id: string }[],
): CompletenessItemResponse[] {
  if (!document.confirmed_category) return []
  const mappedItemIds = new Set(
    mappings
      .filter((mapping) => mapping.document_id === document.id)
      .map((mapping) => mapping.item_id),
  )
  return items.filter(
    (item) =>
      item.category === document.confirmed_category && !mappedItemIds.has(item.id),
  )
}

/** Documents suggested as evidence for an item based on confirmed categories. */
export function suggestedDocumentsForItem(
  documents: CompletenessDocumentResponse[],
  item: CompletenessItemResponse,
  mappings: { document_id: string; item_id: string }[],
): CompletenessDocumentResponse[] {
  const mappedDocumentIds = new Set(
    mappings
      .filter((mapping) => mapping.item_id === item.id)
      .map((mapping) => mapping.document_id),
  )
  return documents.filter(
    (document) =>
      document.confirmed_category === item.category && !mappedDocumentIds.has(document.id),
  )
}

export function staleReasonLabel(reason: string | null | undefined): string | null {
  return reason ? (runStaleLabels[reason] ?? reason) : null
}

export function summaryOf(draft: LiveDraftResponse): {
  satisfied: number
  missing: number
  pending: number
  notApplicable: number
  waived: number
  total: number
} {
  const counts = { satisfied: 0, missing: 0, pending: 0, notApplicable: 0, waived: 0 }
  for (const item of draft.items) {
    switch (item.state) {
      case 'satisfied':
        counts.satisfied += 1
        break
      case 'missing':
        counts.missing += 1
        break
      case 'pending_confirmation':
        counts.pending += 1
        break
      case 'not_applicable':
        counts.notApplicable += 1
        break
      case 'manually_waived':
        counts.waived += 1
        break
    }
  }
  return { ...counts, total: draft.items.length }
}

/** Which item ids are currently waived for the given waiver records. */
export function waivedItemIds(
  waivers: { item_id: string }[],
): Set<string> {
  return new Set(waivers.map((waiver) => waiver.item_id))
}
