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
export type CandidateResponse = components['schemas']['CandidateResponse']
export type ResolutionResponse = components['schemas']['ResolutionResponse']
export type ResolutionInput = components['schemas']['ResolutionRequest']
export type CloudCallResponse = components['schemas']['CloudCallResponse']
export type ClassificationCandidateResponse = components['schemas']['ClassificationCandidateResponse']
export type CompletenessDocumentResponse = components['schemas']['CompletenessDocumentResponse']
export type CompletenessItemResponse = components['schemas']['CompletenessItemResponse']
export type LiveDraftResponse = components['schemas']['LiveDraftResponse']
export type MappingResponse = components['schemas']['MappingResponse']
export type WaiverResponse = components['schemas']['WaiverResponse']
export type RunSummaryResponse = components['schemas']['app__completeness_api__RunSummaryResponse']
export type RunDetailResponse = components['schemas']['app__completeness_api__RunDetailResponse']
export type TemplateResponse = components['schemas']['TemplateResponse']
export type TemplateItemInput = components['schemas']['TemplateItemInput']
export type CreateTemplateRequest = components['schemas']['CreateTemplateRequest']
export type RulePackageResponse = components['schemas']['RulePackageResponse']
export type RulePackageInput = components['schemas']['RulePackageInput']
export type UpdateRulePackageRequest = components['schemas']['UpdateRulePackageRequest']
export type LprImportResponse = components['schemas']['LprImportResponse']
export type LiveRedlineResponse = components['schemas']['LiveRedlineResponse']
export type SelectionResponse = components['schemas']['SelectionResponse']
export type EvaluationResponse = components['schemas']['EvaluationResponse']
export type LprInfoResponse = components['schemas']['LprInfoResponse']
export type RuleContextResponse = components['schemas']['RuleContextResponse']
export type RedlineRunSummary = components['schemas']['app__redline_api__RunSummaryResponse']
export type RedlineRunDetail = components['schemas']['app__redline_api__RunDetailResponse']
export type TypedValue = {
  type: string
  value: string
  raw_text?: string | null
  unit?: string | null
  currency?: string | null
  period?: string | null
  method?: string | null
  date?: string | null
  columns?: Record<string, string> | null
}

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
