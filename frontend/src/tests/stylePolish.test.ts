import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test } from 'vitest'

// Deterministic release UI-polish checks (no browser needed): the stylesheet
// must carry a keyboard focus-visible outline and a print stylesheet that
// drops the header chrome, and the document root must declare zh-CN.

function read(pathFromProject: string): string {
  return readFileSync(resolve(process.cwd(), pathFromProject), 'utf-8')
}

test('style.css provides a keyboard focus-visible outline', () => {
  expect(read('src/style.css')).toMatch(/:focus-visible\s*\{[^}]*outline/)
})

test('style.css provides a print stylesheet hiding the header chrome', () => {
  const css = read('src/style.css')
  expect(css).toMatch(/@media print\s*\{/)
  expect(css).toMatch(/\.header\s*\{\s*display:\s*none/)
})

test('index.html declares the Simplified-Chinese language', () => {
  expect(read('index.html')).toMatch(/<html lang="zh-CN">/)
})
