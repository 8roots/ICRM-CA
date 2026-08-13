export interface EvidencePageSize {
  width: number
  height: number
}

export interface EvidenceItem {
  bbox: [number, number, number, number]
}

export interface BboxStyle {
  left: string
  top: string
  width: string
  height: string
}

/** Scale a page-coordinate bounding box onto a displayed image of `display` pixels. */
export function bboxStyle(
  bbox: [number, number, number, number],
  page: EvidencePageSize,
  displayWidth: number,
  displayHeight: number,
): BboxStyle {
  const scaleX = displayWidth / page.width
  const scaleY = displayHeight / page.height
  return {
    left: `${bbox[0] * scaleX}px`,
    top: `${bbox[1] * scaleY}px`,
    width: `${(bbox[2] - bbox[0]) * scaleX}px`,
    height: `${(bbox[3] - bbox[1]) * scaleY}px`,
  }
}
