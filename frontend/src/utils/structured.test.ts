import { describe, expect, test } from 'vitest'
import type { BlockResponse, CellResponse } from '../api/client'
import { blockLocation, cellLocation, tableGrid } from '../utils/structured'

const cell = (id: string, row: number, column: number, text: string, locator: CellResponse['locator'] = null): CellResponse => ({
  id,
  row,
  column,
  text,
  bbox: null,
  locator,
})

const block = (cells: CellResponse[], locator: BlockResponse['locator'] = null): BlockResponse => ({
  id: 'block-1',
  order: 0,
  kind: 'table',
  text: '日期',
  bbox: null,
  extraction_method: 'xlsx_text',
  confidence: null,
  cells,
  locator,
})

describe('blockLocation', () => {
  test('renders each format-native location without inventing pages', () => {
    expect(blockLocation({ kind: 'docx', paragraph_path: 'body/3' })).toBe('路径 body/3')
    expect(blockLocation({ kind: 'xlsx', sheet: '流水明细', cell_range: 'A1:D8' })).toBe(
      '工作表 流水明细 · 范围 A1:D8',
    )
    expect(blockLocation({ kind: 'csv', row: 1, column: 1, encoding: 'gb18030' })).toBe(
      '第 1 行起 · 编码 gb18030',
    )
    expect(
      blockLocation({ kind: 'markdown', heading_path: '一、企业概况', line_start: 3, line_end: 9 }),
    ).toBe('标题路径 一、企业概况 · 第 3-9 行')
    expect(blockLocation(null)).toBe('')
  })
})

describe('cellLocation', () => {
  test('uses xlsx sheet and cell reference', () => {
    const item = cell('c1', 2, 4, '1234.5', { kind: 'xlsx', sheet: '流水明细', cell: 'D2' })
    expect(cellLocation(item)).toBe('工作表 流水明细 · 单元格 D2')
  })

  test('uses csv row, column and column name', () => {
    const item = cell('c2', 3, 2, '-50', {
      kind: 'csv',
      row: 3,
      column: 2,
      column_name: '金额',
    })
    expect(cellLocation(item)).toBe('第 3 行 · 第 2 列（金额）')
  })

  test('falls back to canonical row/column indexes', () => {
    expect(cellLocation(cell('c3', 4, 1, '文本'))).toBe('第 4 行 · 第 1 列')
  })
})

describe('tableGrid', () => {
  test('sorts cells into a row-major grid', () => {
    const grid = tableGrid(
      block([
        cell('a1', 1, 1, '日期'),
        cell('a2', 1, 2, '金额'),
        cell('b1', 2, 1, '2026-08-01'),
        cell('b2', 2, 2, '1,234.56'),
      ]),
    )
    expect(grid.map((row) => row.map((item) => item.text))).toEqual([
      ['日期', '金额'],
      ['2026-08-01', '1,234.56'],
    ])
  })

  test('keeps ragged rows aligned by column index', () => {
    const grid = tableGrid(block([cell('a1', 1, 1, '表头'), cell('b2', 2, 2, '值')]))
    expect(grid).toHaveLength(2)
    expect(grid[0].map((item) => item.id)).toEqual(['a1'])
    expect(grid[1].map((item) => item.id)).toEqual(['b2'])
  })
})
