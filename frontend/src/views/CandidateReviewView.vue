<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  request,
  type CandidateResponse,
  type CloudCallResponse,
  type ResolutionInput,
  type ResolutionResponse,
} from '../api/client'
import { formatTypedValue, resolutionErrors, toTypedValue } from '../utils/fieldReview'

const route = useRoute()
const applicationId = String(route.params.id)

const candidates = ref<CandidateResponse[]>([])
const resolutions = ref<ResolutionResponse[]>([])
const cloudCalls = ref<CloudCallResponse[]>([])
const loading = ref(false)
const actionError = ref('')

const dialogOpen = ref(false)
const dialogType = ref<'selected' | 'corrected' | 'manual'>('selected')
const selectedCandidate = ref<CandidateResponse | null>(null)
const dialogValue = ref('')
const dialogReason = ref('')
const manualFieldKey = ref('')
const submitting = ref(false)

interface FieldMeta {
  key: string
  label: string
  group: string
  group_label: string
  critical: boolean
}

const whitelist = ref<FieldMeta[]>([])

const fieldOptions = computed(() => {
  const seen = new Map<string, string>()
  for (const candidate of candidates.value) {
    if (!seen.has(candidate.field_key)) seen.set(candidate.field_key, candidate.field_label)
  }
  // manual entry is not limited to fields that produced candidates: the
  // officer must be able to confirm proposed-loan critical inputs by hand
  for (const field of whitelist.value) {
    if (!seen.has(field.key)) seen.set(field.key, field.label)
  }
  return [...seen.entries()].map(([value, label]) => ({ value, label }))
})

const sortedCandidates = computed(() =>
  [...candidates.value].sort((a, b) => b.confidence - a.confidence),
)
const groupLabels: Record<string, string> = {
  application: '申请', proposed_loan: '拟议贷款', identity: '主体身份',
  financial: '财务报表', transaction: '银行流水', credit: '征信', collateral: '抵押担保', evidence: '证据元数据',
}
const resolutionTypeLabels: Record<string, string> = {
  selected: '采用候选', corrected: '修正候选', manual: '人工录入',
}
const cloudStatusLabels: Record<string, string> = {
  success: '成功',
  cloud_unavailable: '云服务不可用',
  redaction_failed: '脱敏失败',
}
const extractorLabels: Record<string, string> = { local_rule: '本地规则', deepseek: 'DeepSeek' }

function sourceText(candidate: CandidateResponse): string {
  const ref = candidate.source_refs[0]
  if (!ref) return candidate.filename
  const locator = ref.locator as Record<string, unknown> | null
  let location = ''
  if (locator?.kind === 'markdown' && typeof locator.line_start === 'number') {
    location = `第 ${locator.line_start} 行起`
  } else if (locator?.kind === 'xlsx' && typeof locator.sheet === 'string') {
    location = `工作表 ${locator.sheet}`
  } else if (ref.page_number != null) {
    location = `第 ${ref.page_number} 页`
  }
  return `${candidate.filename}${location ? ` · ${location}` : ''}`
}

function jumpToEvidence(candidate: CandidateResponse) {
  const ref = candidate.source_refs[0]
  if (!ref) return
  const query: Record<string, string> = {}
  if (ref.block_id) query.block = ref.block_id
  if (ref.cell_id) query.cell = ref.cell_id
  const search = new URLSearchParams(query).toString()
  window.location.href = `/documents/${candidate.document_id}/evidence${search ? `?${search}` : ''}`
}

function openSelected(candidate: CandidateResponse) {
  selectedCandidate.value = candidate
  dialogType.value = 'selected'
  dialogValue.value = candidate.raw_text
  dialogReason.value = ''
  manualFieldKey.value = ''
  dialogOpen.value = true
}

function openCorrected(candidate: CandidateResponse) {
  selectedCandidate.value = candidate
  dialogType.value = 'corrected'
  dialogValue.value = candidate.raw_text
  dialogReason.value = ''
  manualFieldKey.value = ''
  dialogOpen.value = true
}

function openManual() {
  selectedCandidate.value = null
  dialogType.value = 'manual'
  dialogValue.value = ''
  dialogReason.value = ''
  manualFieldKey.value = fieldOptions.value[0]?.value ?? ''
  dialogOpen.value = true
}

async function submitResolution() {
  const candidate = selectedCandidate.value
  const payload: ResolutionInput = {
    resolution_type: dialogType.value,
    field_key: dialogType.value === 'manual' ? manualFieldKey.value : (candidate?.field_key ?? ''),
    candidate_id: dialogType.value === 'manual' ? null : (candidate?.id ?? null),
    value: dialogValue.value,
    reason: dialogReason.value || null,
  }
  const errors = resolutionErrors({
    resolution_type: payload.resolution_type,
    field_key: payload.field_key,
    candidate_id: payload.candidate_id ?? null,
    value: payload.value,
    reason: payload.reason ?? '',
  })
  if (errors.length > 0) {
    actionError.value = errors.join('；')
    return
  }
  submitting.value = true
  actionError.value = ''
  try {
    const created = await request<ResolutionResponse>(
      `/api/v1/applications/${applicationId}/resolutions`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify(payload),
      },
    )
    resolutions.value.push(created)
    dialogOpen.value = false
  } catch (caught) {
    actionError.value = caught instanceof Error ? caught.message : '提交失败'
  } finally {
    submitting.value = false
  }
}

async function refresh() {
  loading.value = true
  try {
    candidates.value = await request<CandidateResponse[]>(
      `/api/v1/applications/${applicationId}/candidates`,
    )
    resolutions.value = await request<ResolutionResponse[]>(
      `/api/v1/applications/${applicationId}/resolutions`,
    )
    cloudCalls.value = await request<CloudCallResponse[]>(
      `/api/v1/applications/${applicationId}/cloud-calls`,
    )
    try {
      whitelist.value = await request<FieldMeta[]>(`/api/v1/meta/fields`)
    } catch {
      whitelist.value = []
    }
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <section>
    <router-link to="/applications">返回申请列表</router-link>
    <h1>字段候选复核与人工确认</h1>
    <p class="candidate-note">
      字段候选由系统从材料中抽取，未经确认前不可用于正式规则计算；高置信度只影响排序，系统不会自动确认任何字段。
      候选值不可修改或删除，您的选择、修正或人工录入会生成为独立的确认记录。
    </p>

    <div class="toolbar">
      <el-button :aria-label="'刷新候选'" @click="refresh">刷新</el-button>
      <el-button type="primary" :aria-label="'人工录入确认值'" @click="openManual">
        人工录入确认值
      </el-button>
    </div>
    <p v-if="actionError" role="alert" class="error-text">{{ actionError }}</p>

    <h2>字段候选（按置信度排序）</h2>
    <el-table
      :data="sortedCandidates"
      v-loading="loading"
      empty-text="暂无字段候选。请等待材料解析完成，或点击刷新。"
    >
      <el-table-column label="字段" width="180">
        <template #default="scope">
          {{ scope.row.field_label }}
          <el-tag v-if="scope.row.critical" size="small" type="danger" class="critical-tag">关键</el-tag>
          <span v-if="scope.row.subject_label" class="muted">（{{ scope.row.subject_label }}）</span>
        </template>
      </el-table-column>
      <el-table-column label="组" width="100">
        <template #default="scope">{{ groupLabels[scope.row.group] || scope.row.group }}</template>
      </el-table-column>
      <el-table-column label="候选值" min-width="180">
        <template #default="scope">{{ formatTypedValue(toTypedValue(scope.row.typed_value)) }}</template>
      </el-table-column>
      <el-table-column label="原文" min-width="160">
        <template #default="scope">{{ scope.row.raw_text }}</template>
      </el-table-column>
      <el-table-column label="置信度" width="90">
        <template #default="scope">{{ scope.row.confidence.toFixed(2) }}</template>
      </el-table-column>
      <el-table-column label="来源" min-width="200">
        <template #default="scope">
          {{ extractorLabels[scope.row.extractor] || scope.row.extractor }}
          <span v-if="scope.row.extractor === 'deepseek'" class="muted">
            · {{ scope.row.model_version }} · {{ scope.row.prompt_version }}
          </span>
          <span v-else class="muted">· {{ scope.row.extractor_version }}</span>
        </template>
      </el-table-column>
      <el-table-column label="证据" min-width="180">
        <template #default="scope">
          <span class="muted">{{ sourceText(scope.row) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="scope">
          <el-button size="small" :aria-label="`跳转证据-${scope.row.field_label}`" @click="jumpToEvidence(scope.row)">
            定位证据
          </el-button>
          <el-button size="small" type="primary" :aria-label="`采用候选-${scope.row.field_label}`" @click="openSelected(scope.row)">
            采用
          </el-button>
          <el-button size="small" :aria-label="`修正候选-${scope.row.field_label}`" @click="openCorrected(scope.row)">
            修正
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <h2>确认记录</h2>
    <el-table :data="resolutions" empty-text="尚无人工确认记录">
      <el-table-column label="字段" width="150" prop="field_label" />
      <el-table-column label="方式" width="110">
        <template #default="scope">{{ resolutionTypeLabels[scope.row.resolution_type] }}</template>
      </el-table-column>
      <el-table-column label="确认值" min-width="180">
        <template #default="scope">{{ formatTypedValue(toTypedValue(scope.row.typed_value)) }}</template>
      </el-table-column>
      <el-table-column label="无材料来源" width="110">
        <template #default="scope">
          <el-tag v-if="scope.row.no_material_source" type="warning">人工录入，无材料来源</el-tag>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="理由" min-width="160" prop="reason" />
      <el-table-column label="时间" width="180" prop="created_at" />
    </el-table>

    <h2>云端抽取审计（仅负责人可见，内容已脱敏）</h2>
    <el-table :data="cloudCalls" empty-text="尚未调用云端抽取（本地未启用或没有缺失字段）">
      <el-table-column label="状态" width="130">
        <template #default="scope">{{ cloudStatusLabels[scope.row.status] || scope.row.status }}</template>
      </el-table-column>
      <el-table-column label="模型" width="150" prop="model" />
      <el-table-column label="提示词版本" width="150" prop="prompt_version" />
      <el-table-column label="脱敏版本" width="140" prop="redaction_version" />
      <el-table-column label="时间" width="180" prop="created_at" />
      <el-table-column label="脱敏请求/响应">
        <template #default="scope">
          <details class="audit-details">
            <summary>查看脱敏内容</summary>
            <pre class="audit-pre">{{ JSON.stringify(scope.row.redacted_request, null, 2) }}</pre>
            <pre v-if="scope.row.redacted_response" class="audit-pre">{{ JSON.stringify(scope.row.redacted_response, null, 2) }}</pre>
          </details>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="dialogOpen"
      :title="
        dialogType === 'selected'
          ? '采用候选'
          : dialogType === 'corrected'
            ? '修正候选'
            : '人工录入确认值'
      "
      width="520px"
    >
      <el-form label-width="90px">
        <el-form-item v-if="dialogType === 'manual'" label="字段">
          <el-select v-model="manualFieldKey" :aria-label="'选择字段'">
            <el-option v-for="option in fieldOptions" :key="option.value" :value="option.value" :label="option.label" />
          </el-select>
        </el-form-item>
        <el-form-item v-else label="候选">
          <span>{{ selectedCandidate?.field_label }}：{{ formatTypedValue(toTypedValue(selectedCandidate?.typed_value)) }}</span>
        </el-form-item>
        <el-form-item label="确认值">
          <el-input
            v-model="dialogValue"
            :disabled="dialogType === 'selected'"
            :aria-label="'确认值'"
            :placeholder="dialogType === 'manual' ? '填写无材料来源的确认值' : '如需修正，请在此修改'"
          />
        </el-form-item>
        <el-form-item v-if="dialogType === 'selected'" label="值来源">
          <span>沿用候选值，不改写候选本身</span>
        </el-form-item>
        <el-form-item v-if="dialogType === 'manual'" label="理由">
          <el-input
            v-model="dialogReason"
            :aria-label="'人工录入理由'"
            placeholder="人工录入值必须填写理由（必填）"
          />
        </el-form-item>
        <el-form-item v-if="dialogType === 'corrected'" label="理由（可选）">
          <el-input v-model="dialogReason" :aria-label="'修正理由'" placeholder="填写修正理由（可选）" />
        </el-form-item>
      </el-form>
      <el-alert
        v-if="dialogType === 'manual'"
        type="warning"
        :closable="false"
        title="该确认值没有材料出处，报告中会明确标记为人工录入。"
      />
      <template #footer>
        <el-button :aria-label="'取消确认'" @click="dialogOpen = false">取消</el-button>
        <el-button
          type="primary"
          :loading="submitting"
          :aria-label="'提交确认'"
          @click="submitResolution"
        >提交确认</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.toolbar { margin-bottom: 12px; }
.candidate-note { color: #606266; font-size: 12px; }
.muted { color: #909399; }
.critical-tag { margin-left: 4px; }
.error-text { color: #d64541; }
.audit-details { cursor: pointer; }
.audit-pre {
  background: #f5f7fa;
  padding: 8px;
  font-size: 12px;
  overflow: auto;
  max-height: 240px;
}
</style>
