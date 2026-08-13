import type { BlockResponse, CellResponse } from '../api/client'

/** Format-native source reference carried by structured-format blocks and cells. */
export interface NativeLocator {
  kind: 'docx' | 'xlsx' | 'csv' | 'markdown'
  paragraph_path?: string | null
  sheet?: string | null
  cell_range?: string | null
  cell?: string | null
  row?: number | null
  column?: number | null
  column_name?: string | null
  encoding?: string | null
  heading_path?: string | null
  line_start?: number | null
  line_end?: number | null
}

/** Human-readable location of a parsed block (used as its preview caption). */
export function blockLocation(locator: NativeLocator | null): string {
  if (!locator) return ''
  switch (locator.kind) {
    case 'docx':
      return `路径 ${locator.paragraph_path ?? ''}`
    case 'xlsx':
      return `工作表 ${locator.sheet ?? ''} · 范围 ${locator.cell_range ?? ''}`
    case 'csv':
      return `第 ${locator.row ?? 1} 行起 · 编码 ${locator.encoding ?? ''}`
    case 'markdown':
      return `标题路径 ${locator.heading_path || '（根）'} · 第 ${locator.line_start ?? ''}-${locator.line_end ?? ''} 行`
    default:
      return ''
  }
}

/** Human-readable location of a single table cell. */
export function cellLocation(cell: CellResponse): string {
  const locator = cell.locator as NativeLocator | null
  if (locator?.kind === 'xlsx' && locator.sheet && locator.cell) {
    return `工作表 ${locator.sheet} · 单元格 ${locator.cell}`
  }
  if (locator?.kind === 'csv' && locator.row) {
    const column = locator.column_name ? `第 ${locator.column} 列（${locator.column_name}）` : `第 ${locator.column} 列`
    return `第 ${locator.row} 行 · ${column}`
  }
  return `第 ${cell.row} 行 · 第 ${cell.column} 列`
}

/** Sort a table block's cells into a row-major grid for rendering. */
export function tableGrid(block: BlockResponse): CellResponse[][] {
  const rows = new Map<number, Map<number, CellResponse>>()
  for (const cell of block.cells) {
    if (!rows.has(cell.row)) rows.set(cell.row, new Map())
    rows.get(cell.row)!.set(cell.column, cell)
  }
  return [...rows.keys()]
    .sort((a, b) => a - b)
    .map((row) => [...rows.get(row)!.keys()].sort((a, b) => a - b).map((column) => rows.get(row)!.get(column)!))
}
