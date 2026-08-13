import type { components } from './generated'

export type CurrentUser = components['schemas']['UserResponse']
export type Application = components['schemas']['ApplicationResponse']
export type ApplicationInput = components['schemas']['ApplicationFields']
export type ManagedUser = components['schemas']['ManagedUserResponse']
export type Document = components['schemas']['DocumentResponse']
export type DocumentJob = components['schemas']['JobResponse']
export type OutputResponse = components['schemas']['OutputResponse']
export type PageResponse = components['schemas']['PageResponse']
export type BlockResponse = components['schemas']['BlockResponse']
export type CellResponse = components['schemas']['CellResponse']
export type SealCandidateResponse = components['schemas']['SealCandidateResponse']
export type EvidenceReviewResponse = components['schemas']['EvidenceReviewResponse']
export type EvidenceReviewInput = components['schemas']['EvidenceReviewRequest']

function csrfToken(): string {
  const cookie = document.cookie
    .split('; ')
    .find((item) => item.startsWith('icrm_csrf='))
  return cookie ? decodeURIComponent(cookie.split('=')[1] ?? '') : ''
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (options.method && options.method !== 'GET') headers.set('X-CSRF-Token', csrfToken())
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin' })
  if (!response.ok) {
    const detail = await response.json().catch(() => null)
    throw new Error(detail?.detail ?? `请求失败 (${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}
