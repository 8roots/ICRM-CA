<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  request,
  type EvaluationResponse,
  type LiveRedlineResponse,
  type RedlineRunDetail,
  type RedlineRunSummary,
  type RuleContextResponse,
} from '../api/client'

const route = useRoute()
const applicationId = String(route.params.id)

interface CriticalPayload {
  missing: string[]
  confirmed: {
    field_key: string
    label: string
    value: string | null
    raw_text: string | null
    manual: boolean
  }[]
}

const preview = ref<LiveRedlineResponse | null>(null)
const runs = ref<RedlineRunSummary[]>([])
const loading = ref(false)
const runsLoading = ref(false)
const running = ref(false)
const actionError = ref('')
const contextInput = ref('')
const latestRun = ref<RedlineRunDetail | null>(null)

const criticalData = computed<CriticalPayload>(() => {
  if (!preview.value) return { missing: [], confirmed: [] }
  return preview.value.critical as unknown as CriticalPayload
})

const stateTagTypes: Record<string, 'success' | 'danger' | 'warning' | 'info'> = {
  triggered: 'danger',
  not_triggered: 'success',
  risk_warning: 'warning',
  insufficient_data: 'warning',
  not_applicable: 'info',
  indeterminate: 'info',
}

const selectionExplanation = computed(
  () => preview.value?.selection.explanation ?? '',
)

function refresh() {
  loading.value = true
  return request<LiveRedlineResponse>(`/api/v1/applications/${applicationId}/redline`)
    .then((body) => {
      preview.value = body
      if (!body.rule_context && !contextInput.value) contextInput.value = '全国'
    })
    .finally(() => {
      loading.value = false
    })
}

async function refreshRuns() {
  runsLoading.value = true
  try {
    runs.value = await request<RedlineRunSummary[]>(
      `/api/v1/applications/${applicationId}/redline-runs`,
    )
  } finally {
    runsLoading.value = false
  }
}

async function confirmContext() {
  const context = contextInput.value.trim()
  if (!context) {
    actionError.value = '请先填写规则上下文'
    return
  }
  actionError.value = ''
  try {
    const confirmed = await request<RuleContextResponse>(
      `/api/v1/applications/${applicationId}/rule-context`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ context }),
      },
    )
    preview.value!.rule_context = confirmed.context
    await refresh()
  } catch (caught) {
    actionError.value = caught instanceof Error ? caught.message : '确认失败'
  }
}

async function runFormal() {
  actionError.value = ''
  running.value = true
  try {
    latestRun.value = await request<RedlineRunDetail>(
      `/api/v1/applications/${applicationId}/redline-runs`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      },
    )
    await refresh()
    await refreshRuns()
  } catch (caught) {
    actionError.value = caught instanceof Error ? caught.message : '正式评估失败'
  } finally {
    running.value = false
  }
}

function staleReasonLabel(reason: string | null | undefined): string | null {
  const labels: Record<string, string> = {
    critical_input_change: '关键输入已变化',
    rule_context_change: '规则上下文已变化',
    application_change: '申请信息已变化',
    new_run: '已生成新报告',
    rule_changed: '适用规则已变化，存在更新规则',
    lpr_changed: '适用 LPR 已变化',
  }
  return reason ? (labels[reason] ?? reason) : null
}

const editable = ref(true)

onMounted(() => {
  refresh()
  refreshRuns()
  request<{ editable: boolean }>(`/api/v1/applications/${applicationId}/lifecycle`)
    .then((lifecycle) => { editable.value = lifecycle.editable !== false })
    .catch(() => { editable.value = true })
})
</script>

<template>
  <section v-if="preview" v-loading="loading">
    <router-link to="/applications">返回申请列表</router-link>
    <h1>红线评估与正式报告</h1>

    <el-alert
      type="info"
      :closable="false"
      class="disclaimer"
      title="红线结果仅供审批辅助，需人工复核；系统不认定、也不暗示本笔贷款合规或获批。"
    />

    <el-alert
      v-if="!editable"
      type="info"
      title="申请已归档或完成，处于只读状态。"
      :closable="false"
    />

    <h2>规则上下文（必须显式确认，系统不根据地址推断）</h2>
    <div class="toolbar">
      <el-input
        v-model="contextInput"
        class="context-input"
        :disabled="!editable"
        :aria-label="'规则上下文'"
        placeholder="例如：全国"
      />
      <el-button type="primary" :disabled="!editable" :aria-label="'确认规则上下文'" @click="confirmContext">
        {{ preview.rule_context ? '更新规则上下文' : '确认规则上下文' }}
      </el-button>
      <el-tag v-if="preview.rule_context" type="success" class="context-tag">
        已确认：{{ preview.rule_context }}
      </el-tag>
      <span v-else class="muted">尚未确认</span>
    </div>

    <h2>主规则包选择</h2>
    <el-alert
      :type="preview.selection.reason === 'unique' ? 'success' : 'warning'"
      :closable="false"
      :title="selectionExplanation"
      class="selection-alert"
    >
      <template v-if="preview.selection.reason === 'multiple_match'" #default>
        <p class="muted">同时匹配：</p>
        <p v-for="candidate in preview.selection.candidates" :key="candidate.id" class="muted">
          {{ candidate.code }} v{{ candidate.version }}（{{ candidate.name }}）
        </p>
      </template>
    </el-alert>
    <el-descriptions v-if="preview.selection.rule" border :column="2" class="rule-info">
      <el-descriptions-item label="规则">
        {{ preview.selection.rule.name }}（{{ preview.selection.rule.code }} · v{{ preview.selection.rule.version }}）
        <el-tag v-if="preview.selection.rule.demo_only" size="small" type="danger" class="demo-tag">演示规则</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="计算类型">{{ preview.selection.rule.calc_type_label }}</el-descriptions-item>
      <el-descriptions-item label="生效区间">
        {{ preview.selection.rule.effective_from }} 至 {{ preview.selection.rule.effective_until || '长期' }}
      </el-descriptions-item>
      <el-descriptions-item label="法律依据">{{ preview.selection.rule.legal_basis }}</el-descriptions-item>
      <el-descriptions-item label="法务复核">
        {{ preview.selection.rule.reviewer }} · {{ preview.selection.rule.reviewed_at }}
      </el-descriptions-item>
    </el-descriptions>

    <h2>LPR 时点</h2>
    <el-descriptions border :column="2" class="lpr-info">
      <el-descriptions-item label="一年期 LPR">
        <template v-if="preview.lpr.value">
          {{ preview.lpr.value }}%（{{ preview.lpr.effective_date }} 生效）
        </template>
        <template v-else>未取到适用 LPR（资料不足）</template>
      </el-descriptions-item>
      <el-descriptions-item label="选取方式">
        <el-tag v-if="preview.lpr.provisional" type="warning">按评估日期预估（无拟签约日期）</el-tag>
        <el-tag v-else type="success">按拟签约日期选取</el-tag>
      </el-descriptions-item>
    </el-descriptions>

    <h2>关键输入（确认值）</h2>
    <el-alert
      v-if="criticalData.missing.length > 0"
      type="warning"
      :closable="false"
      class="critical-alert"
    >
      <template #title>以下关键输入缺失或未经确认，无法得出“未触发硬规则”结论：</template>
      <template #default>
        <span v-for="key in criticalData.missing" :key="key" class="missing-chip">{{ key }}</span>
        <router-link class="candidate-link" :to="`/applications/${applicationId}/candidates`">
          前往字段候选复核与人工确认
        </router-link>
      </template>
    </el-alert>
    <el-table v-else :data="criticalData.confirmed" :aria-label="'已确认关键输入'">
      <el-table-column prop="label" label="字段" />
      <el-table-column prop="value" label="确认值" />
      <el-table-column label="来源">
        <template #default="scope">
          <el-tag v-if="scope.row.manual" type="warning" size="small">人工录入（无材料出处）</el-tag>
          <el-tag v-else type="success" size="small">材料确认</el-tag>
        </template>
      </el-table-column>
    </el-table>

    <h2>实时评估预览</h2>
    <div class="summary">
      <el-tag :type="stateTagTypes[preview.state]">{{ preview.state_label }}</el-tag>
      <span v-if="preview.primary?.reason" class="reason-text">{{ preview.primary.reason }}</span>
    </div>
    <el-table
      v-if="preview.primary && preview.primary.steps.length > 0"
      :data="preview.primary.steps"
      :aria-label="'计算步骤'"
    >
      <el-table-column prop="label" label="步骤" width="220" />
      <el-table-column prop="detail" label="计算过程" />
    </el-table>

    <template v-if="preview.references.length > 0">
      <h2>司法风险参考线（仅提示，不构成硬规则结论）</h2>
      <el-table :data="preview.references" :aria-label="'司法风险参考线'">
        <el-table-column label="参考线" width="200">
          <template #default="scope">{{ scope.row.rule.code }} · v{{ scope.row.rule.version }}</template>
        </el-table-column>
        <el-table-column label="名称" min-width="220">
          <template #default="scope">{{ scope.row.rule.name }}</template>
        </el-table-column>
        <el-table-column label="状态" width="180">
          <template #default="scope">
            <el-tag :type="scope.row.evaluation.state === 'risk_warning' ? 'warning' : 'info'">
              {{ scope.row.evaluation.state_label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="说明" min-width="240">
          <template #default="scope">{{ scope.row.evaluation.reason }}</template>
        </el-table-column>
      </el-table>
    </template>

    <div class="toolbar">
      <el-button
        type="primary"
        :disabled="!!preview.formal_run_blocked_reason || !editable"
        :loading="running"
        :aria-label="'执行正式红线评估'"
        @click="runFormal"
      >执行正式红线评估</el-button>
      <el-button :aria-label="'刷新红线预览'" @click="refresh">刷新</el-button>
      <span v-if="preview.formal_run_blocked_reason" class="error-text">{{ preview.formal_run_blocked_reason }}</span>
    </div>

    <template v-if="preview.latest_run">
      <el-alert
        :type="preview.latest_run.stale ? 'warning' : 'success'"
        :closable="false"
        class="run-alert"
      >
        <template #title>
          最近一次正式报告：
          {{ preview.latest_run.stale ? '已失效（' + (staleReasonLabel(preview.latest_run.stale_reason) ?? '输入已变化') + '），请重新执行' : '有效' }}
        </template>
        <template #default>
          <span class="muted">
            编号 {{ preview.latest_run.id.slice(0, 8) }} ·
            状态 {{ preview.latest_run.state }} · 内容哈希 {{ preview.latest_run.content_hash.slice(0, 12) }}…
          </span>
          <a
            class="print-link"
            :href="`/api/v1/applications/${applicationId}/redline-runs/${preview.latest_run.id}/printable`"
            target="_blank"
          >打印版 HTML</a>
          <a
            class="print-link"
            :href="`/api/v1/applications/${applicationId}/redline-runs/${preview.latest_run.id}`"
            target="_blank"
          >JSON</a>
        </template>
      </el-alert>
    </template>

    <p v-if="actionError" role="alert" class="error-text">{{ actionError }}</p>

    <h2>历史正式报告</h2>
    <el-table
      :data="runs"
      :aria-label="'历史红线报告'"
      v-loading="runsLoading"
      empty-text="尚未生成正式红线报告"
    >
      <el-table-column label="时间" width="180" prop="created_at" />
      <el-table-column label="规则" width="200">
        <template #default="scope">
          {{ scope.row.rule_code ? `${scope.row.rule_code} · v${scope.row.rule_version}` : '无法确定适用规则' }}
        </template>
      </el-table-column>
      <el-table-column label="状态" width="130">
        <template #default="scope">
          <el-tag :type="stateTagTypes[scope.row.state]">{{ scope.row.state }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="报告有效性" width="130">
        <template #default="scope">
          <el-tag :type="scope.row.stale ? 'warning' : 'success'">
            {{ scope.row.stale ? '已失效' : '有效' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="失效原因" min-width="160">
        <template #default="scope">{{ staleReasonLabel(scope.row.stale_reason) ?? '—' }}</template>
      </el-table-column>
      <el-table-column label="内容哈希" min-width="150">
        <template #default="scope">
          <span class="muted">{{ scope.row.content_hash.slice(0, 12) }}…</span>
        </template>
      </el-table-column>
      <el-table-column label="查看" width="180">
        <template #default="scope">
          <a
            class="print-link"
            :href="`/api/v1/applications/${applicationId}/redline-runs/${scope.row.id}/printable`"
            target="_blank"
          >打印版 HTML</a>
          <a
            class="print-link"
            :href="`/api/v1/applications/${applicationId}/redline-runs/${scope.row.id}`"
            target="_blank"
          >JSON</a>
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>

<style scoped>
.toolbar { margin: 12px 0; }
.context-input { width: 260px; margin-right: 8px; }
.context-tag { margin-left: 8px; }
.disclaimer { margin: 12px 0; }
.selection-alert { margin: 12px 0; }
.rule-info, .lpr-info { margin-top: 8px; }
.demo-tag { margin-left: 6px; }
.critical-alert { margin: 12px 0; }
.missing-chip { margin-right: 8px; font-weight: 600; }
.candidate-link { margin-left: 12px; color: #1769aa; }
.summary { margin: 12px 0; }
.reason-text { margin-left: 12px; color: #444; }
.run-alert { margin: 12px 0; }
.print-link { margin-left: 12px; color: #1769aa; }
.muted { color: #909399; }
.error-text { color: #d64541; }
</style>
