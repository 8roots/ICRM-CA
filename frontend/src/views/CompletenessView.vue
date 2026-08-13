<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  request,
  type CompletenessDocumentResponse,
  type CompletenessItemResponse,
  type LiveDraftResponse,
  type MappingResponse,
  type RunDetailResponse,
  type RunSummaryResponse,
  type WaiverResponse,
} from '../api/client'
import {
  categoryLabel,
  categoryLabels,
  conditionLabel,
  staleReasonLabel,
  suggestedDocumentsForItem,
  suggestedItemsForDocument,
  summaryOf,
} from '../utils/completeness'

const route = useRoute()
const applicationId = String(route.params.id)

const draft = ref<LiveDraftResponse | null>(null)
const runs = ref<RunSummaryResponse[]>([])
const loading = ref(false)
const runsLoading = ref(false)
const actionError = ref('')
const running = ref(false)
const classificationSelections = ref<Record<string, string>>({})
const waiverReasons = ref<Record<string, string>>({})
const latestRun = ref<RunDetailResponse | null>(null)

const summary = computed(() => (draft.value ? summaryOf(draft.value) : null))

function refresh() {
  loading.value = true
  return request<LiveDraftResponse>(`/api/v1/applications/${applicationId}/completeness`)
    .then((body) => {
      draft.value = body
    })
    .finally(() => {
      loading.value = false
    })
}

async function refreshRuns() {
  runsLoading.value = true
  try {
    runs.value = await request<RunSummaryResponse[]>(
      `/api/v1/applications/${applicationId}/completeness-runs`,
    )
  } finally {
    runsLoading.value = false
  }
}

function jumpToEvidence(documentId: string) {
  window.location.href = `/documents/${documentId}/evidence`
}

async function confirmClassification(document: CompletenessDocumentResponse) {
  const category = classificationSelections.value[document.id]
  if (!category) {
    actionError.value = '请先选择要确认的类别'
    return
  }
  actionError.value = ''
  await request(`/api/v1/applications/${applicationId}/documents/${document.id}/classification`, {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({ category }),
  })
  await refresh()
}

async function createMapping(documentId: string, itemId: string) {
  actionError.value = ''
  try {
    await request<MappingResponse>(`/api/v1/applications/${applicationId}/mappings`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ document_id: documentId, item_id: itemId }),
    })
    await refresh()
  } catch (caught) {
    actionError.value = caught instanceof Error ? caught.message : '映射失败'
  }
}

async function removeMapping(mapping: MappingResponse) {
  actionError.value = ''
  try {
    await request(`/api/v1/applications/${applicationId}/mappings/${mapping.id}`, {
      method: 'DELETE',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    })
    await refresh()
  } catch (caught) {
    actionError.value = caught instanceof Error ? caught.message : '删除映射失败'
  }
}

async function createWaiver(item: CompletenessItemResponse) {
  const reason = waiverReasons.value[item.id]?.trim()
  if (!reason) {
    actionError.value = '人工豁免必须填写理由'
    return
  }
  actionError.value = ''
  try {
    await request<WaiverResponse>(`/api/v1/applications/${applicationId}/waivers`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ item_id: item.id, reason }),
    })
    waiverReasons.value[item.id] = ''
    await refresh()
  } catch (caught) {
    actionError.value = caught instanceof Error ? caught.message : '豁免失败'
  }
}


async function runFormal() {
  actionError.value = ''
  running.value = true
  try {
    const run = await request<RunDetailResponse>(
      `/api/v1/applications/${applicationId}/completeness-runs`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      },
    )
    latestRun.value = run
    await refresh()
  } catch (caught) {
    actionError.value = caught instanceof Error ? caught.message : '正式检查失败'
  } finally {
    running.value = false
  }
}

function itemById(id: string): CompletenessItemResponse | undefined {
  return draft.value?.items.find((item) => item.id === id)
}

function documentById(id: string): CompletenessDocumentResponse | undefined {
  return draft.value?.documents.find((document) => document.id === id)
}

const stateTagTypes: Record<string, 'success' | 'danger' | 'warning' | 'info'> = {
  satisfied: 'success',
  missing: 'danger',
  pending_confirmation: 'warning',
  not_applicable: 'info',
  manually_waived: 'warning',
}

onMounted(() => {
  refresh()
  refreshRuns()
})
</script>

<template>
  <section v-if="draft" v-loading="loading">
    <router-link to="/applications">返回申请列表</router-link>
    <h1>材料完备性与正式报告</h1>

    <div v-if="draft.template">
      <el-descriptions border :column="3" class="template-info">
        <el-descriptions-item label="模板">
          {{ draft.template.name }}（{{ draft.template.code }} · v{{ draft.template.version }}）
        </el-descriptions-item>
        <el-descriptions-item label="产品">{{ draft.template.product }}</el-descriptions-item>
        <el-descriptions-item label="主借款人类型">
          {{ draft.template.borrower_type === 'corporate' ? '企业' : '个人' }}
          <el-tag v-if="draft.template.demo_only" size="small" type="danger" class="demo-tag">演示模板</el-tag>
        </el-descriptions-item>
      </el-descriptions>

      <div v-if="summary" class="summary">
        <el-tag type="success">已满足 {{ summary.satisfied }}</el-tag>
        <el-tag type="danger">缺失 {{ summary.missing }}</el-tag>
        <el-tag type="warning">待确认 {{ summary.pending }}</el-tag>
        <el-tag type="info">不适用 {{ summary.notApplicable }}</el-tag>
        <el-tag>人工豁免 {{ summary.waived }}</el-tag>
      </div>

      <div class="toolbar">
        <el-button
          type="primary"
          :disabled="!!draft.formal_run_blocked_reason"
          :loading="running"
          :aria-label="'执行正式完备性检查'"
          @click="runFormal"
        >执行正式完备性检查</el-button>
        <el-button :aria-label="'刷新完备性草稿'" @click="refresh">刷新</el-button>
        <span v-if="draft.formal_run_blocked_reason" class="error-text">{{ draft.formal_run_blocked_reason }}</span>
      </div>

      <template v-if="draft.latest_run">
        <el-alert
          :type="draft.latest_run.stale ? 'warning' : 'success'"
          :closable="false"
          class="run-alert"
        >
          <template #title>
            最近一次正式报告：
            {{ draft.latest_run.stale ? '已失效（' + (staleReasonLabel(draft.latest_run.stale_reason) ?? '输入已变化') + '），请重新执行' : '有效' }}
          </template>
          <template #default>
            <span class="muted">
              编号 {{ draft.latest_run.id.slice(0, 8) }} · 模板 v{{ draft.latest_run.template_version }} ·
              内容哈希 {{ draft.latest_run.content_hash.slice(0, 12) }}…
            </span>
            <a
              class="print-link"
              :href="`/api/v1/applications/${applicationId}/completeness-runs/${draft.latest_run.id}/printable`"
              target="_blank"
            >打印版 HTML</a>
            <a
              class="print-link"
              :href="`/api/v1/applications/${applicationId}/completeness-runs/${draft.latest_run.id}`"
              target="_blank"
            >JSON</a>
          </template>
        </el-alert>
      </template>

      <p v-if="actionError" role="alert" class="error-text">{{ actionError }}</p>

      <h2>清单项（实时草稿）</h2>
      <el-table :data="draft.items" :aria-label="'清单项草稿'">
        <el-table-column label="类别" width="110">
          <template #default="scope">{{ categoryLabel(scope.row.category) }}</template>
        </el-table-column>
        <el-table-column label="清单项" min-width="200" prop="label" />
        <el-table-column label="要求" width="130">
          <template #default="scope">
            <span v-if="scope.row.requires_seal" class="muted">需印章</span>
            <span v-if="scope.row.requires_signature" class="muted">需签字</span>
            <span v-if="scope.row.condition_label" class="muted condition-text">{{ scope.row.condition_label }}</span>
            <span v-if="!scope.row.requires_seal && !scope.row.requires_signature && !scope.row.condition_label" class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="scope">
            <el-tag :type="stateTagTypes[scope.row.state]">{{ scope.row.state_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="证据材料" min-width="160">
          <template #default="scope">
            <span
              v-for="evidenceId in scope.row.evidence_document_ids"
              :key="evidenceId"
              class="evidence-chip"
            >{{ documentById(evidenceId)?.filename }}</span>
            <span v-if="scope.row.evidence_document_ids.length === 0" class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="建议映射" min-width="220">
          <template #default="scope">
            <div v-for="doc in suggestedDocumentsForItem(draft.documents, scope.row, draft.mappings)" :key="doc.id">
              <span class="muted">{{ doc.filename }}</span>
              <el-button
                size="small"
                type="primary"
                plain
                :aria-label="`确认映射-${doc.filename}-${scope.row.label}`"
                @click="createMapping(doc.id, scope.row.id)"
              >确认映射</el-button>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="scope">
            <template v-if="scope.row.state !== 'manually_waived'">
              <el-input
                v-model="waiverReasons[scope.row.id]"
                size="small"
                class="waiver-input"
                :aria-label="`豁免理由-${scope.row.label}`"
                placeholder="豁免理由（必填）"
              />
              <el-button
                size="small"
                type="warning"
                plain
                :aria-label="`人工豁免-${scope.row.label}`"
                @click="createWaiver(scope.row)"
              >豁免</el-button>
            </template>
            <template v-else>
              <span class="muted waiver-reason">理由：{{ draft.waivers.find((w) => w.item_id === scope.row.id)?.reason }}</span>
              <span class="muted">（豁免记录不可撤销）</span>
            </template>
          </template>
        </el-table-column>
      </el-table>

      <h2>材料分类与确认</h2>
      <el-table :data="draft.documents" :aria-label="'材料分类'">
        <el-table-column label="材料" min-width="180" prop="filename" />
        <el-table-column label="分类候选" min-width="180">
          <template #default="scope">
            <span v-for="candidate in scope.row.classification_candidates" :key="candidate.category" class="candidate-chip">
              {{ candidate.category_label }}（{{ candidate.confidence.toFixed(2) }}）
            </span>
            <span v-if="scope.row.classification_candidates.length === 0" class="muted">无候选</span>
          </template>
        </el-table-column>
        <el-table-column label="确认类别" width="140">
          <template #default="scope">
            <el-tag v-if="scope.row.confirmed_category" type="success">{{ categoryLabel(scope.row.confirmed_category) }}</el-tag>
            <span v-else class="muted">未确认</span>
          </template>
        </el-table-column>
        <el-table-column label="印章/签字" width="160">
          <template #default="scope">
            <span :class="scope.row.seal_confirmed ? 'ok-text' : 'pending-text'">
              印章{{ scope.row.seal_confirmed ? '已确认' : '未确认' }}
            </span>
            <span :class="scope.row.signature_confirmed ? 'ok-text' : 'pending-text'">
              签字{{ scope.row.signature_confirmed ? '已确认' : '未确认' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="scope">
            <el-select
              v-model="classificationSelections[scope.row.id]"
              :aria-label="`选择类别-${scope.row.filename}`"
              placeholder="选择类别"
              size="small"
            >
              <el-option v-for="(label, value) in categoryLabels" :key="value" :value="value" :label="label" />
            </el-select>
            <el-button
              size="small"
              type="primary"
              :aria-label="`确认分类-${scope.row.filename}`"
              @click="confirmClassification(scope.row)"
            >确认</el-button>
            <el-button
              size="small"
              :aria-label="`跳转证据-${scope.row.filename}`"
              @click="jumpToEvidence(scope.row.id)"
            >证据</el-button>
          </template>
        </el-table-column>
      </el-table>

      <h2>已确认映射（多对多证据）</h2>
      <el-table :data="draft.mappings" :aria-label="'已确认映射'" empty-text="尚无已确认的映射">
        <el-table-column label="材料" min-width="180">
          <template #default="scope">{{ scope.row.document_filename }}</template>
        </el-table-column>
        <el-table-column label="清单项" min-width="200">
          <template #default="scope">{{ scope.row.item_label }}（{{ scope.row.item_code }}）</template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="scope">
            <el-button
              size="small"
              type="danger"
              plain
              :aria-label="`删除映射-${scope.row.document_filename}-${scope.row.item_label}`"
              @click="removeMapping(scope.row)"
            >删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-alert
      v-else
      type="info"
      :closable="false"
      :title="draft.no_template_reason ?? '暂无适用模板'"
      class="no-template"
    />

    <h2>历史正式报告</h2>
    <el-table
      :data="runs"
      :aria-label="'历史正式报告'"
      v-loading="runsLoading"
      empty-text="尚未生成正式报告"
    >
      <el-table-column label="时间" width="180" prop="created_at" />
      <el-table-column label="模板" width="160">
        <template #default="scope">{{ scope.row.template_code }} · v{{ scope.row.template_version }}</template>
      </el-table-column>
      <el-table-column label="状态" width="130">
        <template #default="scope">
          <el-tag :type="scope.row.stale ? 'warning' : 'success'">
            {{ scope.row.stale ? '已失效' : '有效' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="失效原因" min-width="160">
        <template #default="scope">{{ staleReasonLabel(scope.row.stale_reason) ?? '—' }}</template>
      </el-table-column>
      <el-table-column label="内容哈希" min-width="160">
        <template #default="scope">
          <span class="muted">{{ scope.row.content_hash.slice(0, 12) }}…</span>
        </template>
      </el-table-column>
      <el-table-column label="查看" width="180">
        <template #default="scope">
          <a
            class="print-link"
            :href="`/api/v1/applications/${applicationId}/completeness-runs/${scope.row.id}/printable`"
            target="_blank"
          >打印版 HTML</a>
          <a
            class="print-link"
            :href="`/api/v1/applications/${applicationId}/completeness-runs/${scope.row.id}`"
            target="_blank"
          >JSON</a>
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>

<style scoped>
.toolbar { margin: 12px 0; }
.template-info { margin-top: 12px; }
.summary { margin: 12px 0; }
.summary .el-tag { margin-right: 8px; }
.demo-tag { margin-left: 6px; }
.run-alert { margin: 12px 0; }
.print-link { margin-left: 12px; color: #1769aa; }
.evidence-chip, .candidate-chip { margin-right: 8px; }
.condition-text { display: block; }
.waiver-input { width: 150px; margin-right: 6px; }
.waiver-reason { margin-right: 8px; }
.muted { color: #909399; }
.error-text { color: #d64541; }
.ok-text { color: #2e7d32; margin-right: 8px; }
.pending-text { color: #b88230; margin-right: 8px; }
.no-template { margin: 12px 0; }
</style>
