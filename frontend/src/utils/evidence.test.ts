import { expect, test } from 'vitest'
import { bboxStyle } from './evidence'

test('scales page coordinates onto the displayed preview size', () => {
  const style = bboxStyle([10, 20, 70, 40], { width: 300, height: 200 }, 600, 400)
  expect(style).toEqual({
    left: '20px',
    top: '40px',
    width: '120px',
    height: '40px',
  })
})

test('keeps native coordinates when preview is not scaled', () => {
  const style = bboxStyle([10, 20, 70, 40], { width: 120, height: 80 }, 120, 80)
  expect(style).toEqual({
    left: '10px',
    top: '20px',
    width: '60px',
    height: '20px',
  })
})
