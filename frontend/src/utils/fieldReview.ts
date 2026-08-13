import type { TypedValue } from '../api/client'

/** Display unit multiplier (as stored) back to its Chinese label. */
const UNIT_LABELS: Record<string, string> = {
  '1': '元',
  '1000': '千元',
  '10000': '万元',
  '100000000': '亿元',
}

const CURRENCY_LABELS: Record<string, string> = {
  CNY: '人民币',
  USD: '美元',
  EUR: '欧元',
  HKD: '港币',
}

const METHOD_LABELS: Record<string, string> = {
  nominal: '名义利率',
  effective: '实际利率',
  floating: '浮动利率',
}

/** Human-readable rendering of a normalized typed value. */
export function formatTypedValue(typed: TypedValue | null | undefined): string {
  if (!typed) return ''
  switch (typed.type) {
    case 'amount': {
      const currency = typed.currency ? CURRENCY_LABELS[typed.currency] ?? typed.currency : ''
      const unit = typed.unit ? UNIT_LABELS[typed.unit] ?? '' : ''
      return `${currency}${typed.value}${unit}`.trim()
    }
    case 'rate': {
      const period = typed.period === '日' || typed.period === '月' ? `${typed.period}利率` : '年利率'
      const method = typed.method ? METHOD_LABELS[typed.method] ?? '' : ''
      return `${typed.value}%（${period}${method ? `·${method}` : ''}）`
    }
    case 'date':
      return typed.value
    case 'integer':
      return typed.unit === '月' ? `${typed.value}个月` : typed.value
    case 'row':
      return typed.value
    default:
      return typed.value
  }
}

/** Coerce an API typed_value object into the client TypedValue shape. */
export function toTypedValue(value: Record<string, unknown> | null | undefined): TypedValue | null {
  if (!value || typeof value.value !== 'string' || typeof value.type !== 'string') return null
  return value as unknown as TypedValue
}

export type ResolutionDraft = {
  resolution_type: 'selected' | 'corrected' | 'manual'
  field_key: string
  candidate_id: string | null
  value: string
  reason: string
}

/** Client-side validation of a resolution draft before submission. */
export function resolutionErrors(draft: ResolutionDraft): string[] {
  const errors: string[] = []
  if (!draft.field_key) errors.push('缺少字段')
  if (draft.resolution_type === 'selected' && !draft.candidate_id) errors.push('请先选择一个候选值')
  if ((draft.resolution_type === 'corrected' || draft.resolution_type === 'manual') && !draft.value.trim()) {
    errors.push('请填写确认值')
  }
  if (draft.resolution_type === 'manual' && !draft.reason.trim()) {
    errors.push('人工录入值必须填写理由')
  }
  return errors
}
