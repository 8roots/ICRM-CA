import { describe, expect, test } from 'vitest'
import type { TypedValue } from '../api/client'
import { formatTypedValue, resolutionErrors } from '../utils/fieldReview'

const typed = (overrides: Partial<TypedValue>): TypedValue => ({
  type: 'text',
  value: '',
  ...overrides,
})

describe('formatTypedValue', () => {
  test('renders amounts with currency and unit', () => {
    expect(formatTypedValue(typed({ type: 'amount', value: '8000000', currency: 'CNY', unit: '10000' }))).toBe(
      '人民币8000000万元',
    )
    expect(formatTypedValue(typed({ type: 'amount', value: '5000', currency: null, unit: '1' }))).toBe('5000元')
  })

  test('renders rates with period and method', () => {
    expect(formatTypedValue(typed({ type: 'rate', value: '3.85', period: '年', method: 'nominal' }))).toBe(
      '3.85%（年利率·名义利率）',
    )
    expect(formatTypedValue(typed({ type: 'rate', value: '6.0', period: '月', method: 'effective' }))).toBe(
      '6.0%（月利率·实际利率）',
    )
  })

  test('renders dates, months, and rows', () => {
    expect(formatTypedValue(typed({ type: 'date', value: '2026-08-07' }))).toBe('2026-08-07')
    expect(formatTypedValue(typed({ type: 'integer', value: '24', unit: '月' }))).toBe('24个月')
    expect(formatTypedValue(typed({ type: 'row', value: '营业收入' }))).toBe('营业收入')
    expect(formatTypedValue(null)).toBe('')
  })
})

describe('resolutionErrors', () => {
  const base = { resolution_type: 'selected', field_key: 'loan_amount', candidate_id: 'c-1', value: '', reason: '' } as const

  test('selected requires a candidate', () => {
    expect(resolutionErrors({ ...base, candidate_id: null })).toEqual(['请先选择一个候选值'])
    expect(resolutionErrors(base)).toEqual([])
  })

  test('corrected and manual require a value', () => {
    expect(resolutionErrors({ ...base, resolution_type: 'corrected', candidate_id: 'c-1', value: ' ' })).toEqual([
      '请填写确认值',
    ])
  })

  test('manual requires a reason', () => {
    const errors = resolutionErrors({
      ...base,
      resolution_type: 'manual',
      candidate_id: null,
      value: '100万元',
      reason: '',
    })
    expect(errors).toEqual(['人工录入值必须填写理由'])
  })
})
