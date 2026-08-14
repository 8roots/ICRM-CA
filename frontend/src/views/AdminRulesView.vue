<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  request,
  type LprImportResponse,
  type RulePackageInput,
  type RulePackageResponse,
} from '../api/client'

const rules = ref<RulePackageResponse[]>([])
const imports = ref<LprImportResponse[]>([])
const loading = ref(false)
const actionError = ref('')
const actionSuccess = ref('')

const statusLabels: Record<string, string> = { draft: '草稿', approved: '已批准', retired: '已停用' }
const kindLabels: Record<string, string> = { hard: '硬规则', reference: '风险参考线' }
const calcTypeOptions = [
  { value: 'annual_rate_limit', label: '年化利率上限' },
  { value: 'lpr_multiple_limit', label: 'LPR 倍数上限' },
  { value: 'effective_cost_limit', label: '综合年化成本上限' },
]

const grouped = computed(() => {
  const byCode = new Map<string, RulePackageResponse[]>()
  for (const rule of rules.value) {
    const list = byCode.get(rule.code) ?? []
    list.push(rule)
    byCode.set(rule.code, list)
  }
  return [...byCode.entries()].map(([code, versions]) => ({
    code,
    versions: versions.sort((a, b) => b.version - a.version),
  }))
})

interface RuleForm {
  code: string
  name: string
  kind: 'hard' | 'reference'
  lender_qualification: string
  rule_context: string
  product: string
  effective_from: string
  effective_until: string | null
  calc_type: string
  params: Record<string, string | number>
  legal_basis: string
  reviewer: string
  reviewed_at: string
  demo_only: boolean
}

function emptyForm(): RuleForm {
  return {
    code: '',
    name: '',
    kind: 'hard',
    lender_qualification: 'small_loan_company',
    rule_context: '全国',
    product: '经营贷',
    effective_from: '2026-01-01',
    effective_until: null,
    calc_type: 'annual_rate_limit',
    params: { threshold_pct: '24' },
    legal_basis: '',
    reviewer: '',
    reviewed_at: '2026-01-01',
    demo_only: false,
  }
}

const dialogOpen = ref(false)
const editingId = ref<string | null>(null)
const form = ref<RuleForm>(emptyForm())
const submitting = ref(false)

const lprUploading = ref(false)
const lprFile = ref<File | null>(null)
const lprAuthority = ref('全国银行间同业拆借中心')

async function refresh() {
  loading.value = true
  try {
    const [ruleList, importList] = await Promise.all([
      request<RulePackageResponse[]>('/api/v1/admin/rule-packages'),
      request<LprImportResponse[]>('/api/v1/admin/lpr-imports'),
    ])
    rules.value = ruleList
    imports.value = importList
  } finally {
    loading.value = false
  }
}

function paramsFor(calcType: string): Record<string, string | number> {
  if (calcType === 'annual_rate_limit') return { threshold_pct: '24' }
  if (calcType === 'lpr_multiple_limit') return { multiplier: '4' }
  return { threshold_pct: '36', overdue_days: 90 }
}

function openCreate() {
  editingId.value = null
  form.value = emptyForm()
  dialogOpen.value = true
}

function openEdit(rule: RulePackageResponse) {
  editingId.value = rule.id
  form.value = {
    code: rule.code,
    name: rule.name,
    kind: rule.kind as 'hard' | 'reference',
    lender_qualification: rule.lender_qualification,
    rule_context: rule.rule_context,
    product: rule.product,
    effective_from: rule.effective_from,
    effective_until: rule.effective_until,
    calc_type: rule.calc_type,
    params: { ...rule.params } as Record<string, string | number>,
    legal_basis: rule.legal_basis,
    reviewer: rule.reviewer,
    reviewed_at: rule.reviewed_at,
    demo_only: rule.demo_only,
  }
  dialogOpen.value = true
}

async function submitForm() {
  actionError.value = ''
  actionSuccess.value = ''
  submitting.value = true
  try {
    if (editingId.value) {
      await request(`/api/v1/admin/rule-packages/${editingId.value}`, {
        method: 'PUT',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({
          name: form.value.name,
          effective_from: form.value.effective_from,
          effective_until: form.value.effective_until || null,
          params: form.value.params,
          legal_basis: form.value.legal_basis,
          reviewer: form.value.reviewer,
          reviewed_at: form.value.reviewed_at,
        }),
      })
    } else {
      const payload: RulePackageInput = {
        code: form.value.code,
        name: form.value.name,
        kind: form.value.kind,
        lender_qualification: form.value.lender_qualification,
        rule_context: form.value.rule_context,
        product: form.value.product,
        effective_from: form.value.effective_from,
        effective_until: form.value.effective_until || null,
        calc_type: form.value.calc_type,
        params: form.value.params,
        legal_basis: form.value.legal_basis,
        reviewer: form.value.reviewer,
        reviewed_at: form.value.reviewed_at,
        demo_only: form.value.demo_only,
      }
      await request('/api/v1/admin/rule-packages', {
        method: 'POST',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify(payload),
      })
    }
    dialogOpen.value = false
    await refresh()
  } catch (caught) {
    actionError.value = caught instanceof Error ? caught.message : '保存失败'
  } finally {
    submitting.value = false
  }
}

async function lifecycle(rule: RulePackageResponse, action: 'approve' | 'copy' | 'retire') {
  actionError.value = ''
  actionSuccess.value = ''
  try {
    await request(`/api/v1/admin/rule-packages/${rule.id}/${action}`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    })
    await refresh()
  } catch (caught) {
    actionError.value = caught instanceof Error ? caught.message : '操作失败'
  }
}

function pickLprFile(event: Event) {
  lprFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
}

async function uploadLpr() {
  if (!lprFile.value) {
    actionError.value = '请先选择 LPR CSV 文件'
    return
  }
  actionError.value = ''
  actionSuccess.value = ''
  lprUploading.value = true
  try {
    const body = new FormData()
    body.append('file', lprFile.value)
    body.append('source_authority', lprAuthority.value)
    const imported = await request<LprImportResponse>('/api/v1/admin/lpr-imports', {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body,
    })
    actionSuccess.value = `已导入 ${imported.row_count} 条 LPR，状态：草稿（需发布后生效）`
    lprFile.value = null
    await refresh()
  } catch (caught) {
    actionError.value = caught instanceof Error ? caught.message : '导入失败'
  } finally {
    lprUploading.value = false
  }
}

async function publishLpr(batch: LprImportResponse) {
  actionError.value = ''
  try {
    await request(`/api/v1/admin/lpr-imports/${batch.id}/publish`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    })
    await refresh()
  } catch (caught) {
    actionError.value = caught instanceof Error ? caught.message : '发布失败'
  }
}

onMounted(refresh)
</script>

<template>
  <section v-loading="loading">
    <h1>规则包与 LPR 管理</h1>

    <p v-if="actionError" role="alert" class="error-text">{{ actionError }}</p>
    <p v-if="actionSuccess" role="status" class="success-text">{{ actionSuccess }}</p>

    <div class="toolbar">
      <el-button type="primary" :aria-label="'新建规则包'" @click="openCreate">新建规则包</el-button>
      <span class="muted note">已批准版本不可编辑；修改需复制为新版本。计算类型由系统代码定义，无法输入任意公式。</span>
    </div>

    <h2>规则包</h2>
    <el-table :data="rules" :aria-label="'规则包列表'" empty-text="尚无规则包">
      <el-table-column prop="code" label="编码" width="200" />
      <el-table-column prop="name" label="名称" min-width="220" />
      <el-table-column label="类型" width="100">
        <template #default="scope">{{ kindLabels[scope.row.kind] }}</template>
      </el-table-column>
      <el-table-column prop="version" label="版本" width="70" />
      <el-table-column label="状态" width="90">
        <template #default="scope">
          <el-tag :type="scope.row.status === 'approved' ? 'success' : scope.row.status === 'draft' ? 'info' : 'warning'">
            {{ statusLabels[scope.row.status] }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="范围" min-width="200">
        <template #default="scope">
          {{ scope.row.rule_context }} · {{ scope.row.product }}
          <el-tag v-if="scope.row.demo_only" size="small" type="danger">演示</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="计算类型" width="160">
        <template #default="scope">{{ scope.row.calc_type_label }}</template>
      </el-table-column>
      <el-table-column label="生效区间" width="200">
        <template #default="scope">
          {{ scope.row.effective_from }} 至 {{ scope.row.effective_until || '长期' }}
        </template>
      </el-table-column>
      <el-table-column label="内容哈希" width="140">
        <template #default="scope">
          <span class="muted">{{ scope.row.content_hash.slice(0, 12) }}…</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="260" fixed="right">
        <template #default="scope">
          <el-button v-if="scope.row.status === 'draft'" size="small" type="primary" plain :aria-label="`编辑-${scope.row.code}`" @click="openEdit(scope.row)">编辑</el-button>
          <el-button v-if="scope.row.status === 'draft'" size="small" type="success" plain :aria-label="`批准-${scope.row.code}`" @click="lifecycle(scope.row, 'approve')">批准</el-button>
          <el-button v-if="scope.row.status !== 'draft'" size="small" :aria-label="`复制-${scope.row.code}`" @click="lifecycle(scope.row, 'copy')">复制</el-button>
          <el-button v-if="scope.row.status === 'approved'" size="small" type="warning" plain :aria-label="`停用-${scope.row.code}`" @click="lifecycle(scope.row, 'retire')">停用</el-button>
        </template>
      </el-table-column>
    </el-table>

    <h2>LPR 导入与发布</h2>
    <div class="toolbar">
      <input type="file" accept=".csv" :aria-label="'选择 LPR CSV'" @change="pickLprFile">
      <el-input v-model="lprAuthority" class="authority-input" :aria-label="'LPR 发布机构'" placeholder="发布机构" />
      <el-button type="primary" :loading="lprUploading" :aria-label="'导入 LPR CSV'" @click="uploadLpr">导入并校验</el-button>
      <span class="muted note">CSV 列：effective_date,tenor,value,publication_date,source_url；导入后为草稿，需显式发布。</span>
    </div>
    <el-table :data="imports" :aria-label="'LPR 导入批次'" empty-text="尚无 LPR 导入记录">
      <el-table-column prop="filename" label="文件" min-width="180" />
      <el-table-column prop="source_authority" label="发布机构" min-width="180" />
      <el-table-column prop="row_count" label="行数" width="80" />
      <el-table-column label="状态" width="100">
        <template #default="scope">
          <el-tag :type="scope.row.status === 'published' ? 'success' : 'info'">
            {{ scope.row.status === 'published' ? '已发布' : '草稿' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="演示数据" width="100">
        <template #default="scope">
          <el-tag v-if="scope.row.demo_only" size="small" type="danger">演示</el-tag>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="导入时间" width="180" />
      <el-table-column label="内容" min-width="240">
        <template #default="scope">
          <span v-for="entry in scope.row.entries.slice(0, 3)" :key="entry.id" class="entry-chip">
            {{ entry.effective_date }} {{ entry.tenor }} {{ entry.value }}%
          </span>
          <span v-if="scope.row.entries.length > 3" class="muted">…</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="scope">
          <el-button
            v-if="scope.row.status === 'draft'"
            size="small"
            type="success"
            plain
            :aria-label="`发布-${scope.row.filename}`"
            @click="publishLpr(scope.row)"
          >发布</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogOpen" :title="editingId ? '编辑规则包（草稿）' : '新建规则包'" width="720px">
      <el-form label-width="130px">
        <el-form-item label="编码">
          <el-input v-model="form.code" :disabled="!!editingId" :aria-label="'规则包编码'" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" :aria-label="'规则包名称'" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.kind" :disabled="!!editingId" :aria-label="'规则类型'">
            <el-option value="hard" label="硬规则（经法务批准）" />
            <el-option value="reference" label="司法风险参考线" />
          </el-select>
        </el-form-item>
        <el-form-item label="放贷机构资质">
          <el-input v-model="form.lender_qualification" :disabled="!!editingId" :aria-label="'放贷机构资质'" />
        </el-form-item>
        <el-form-item label="规则上下文">
          <el-input v-model="form.rule_context" :disabled="!!editingId" :aria-label="'规则上下文'" />
        </el-form-item>
        <el-form-item label="产品">
          <el-input v-model="form.product" :disabled="!!editingId" :aria-label="'产品'" />
        </el-form-item>
        <el-form-item label="生效区间">
          <el-input v-model="form.effective_from" class="date-input" :aria-label="'生效起始日期'" />
          <span class="muted">至</span>
          <el-input v-model="form.effective_until" class="date-input" placeholder="长期（留空）" :aria-label="'生效截止日期'" />
        </el-form-item>
        <el-form-item label="计算类型">
          <el-select v-model="form.calc_type" :disabled="!!editingId" :aria-label="'计算类型'" @change="form.params = paramsFor($event)">
            <el-option v-for="option in calcTypeOptions" :key="option.value" :value="option.value" :label="option.label" />
          </el-select>
          <span class="muted note">计算逻辑由系统代码定义，此处仅配置参数与阈值。</span>
        </el-form-item>
        <template v-if="form.calc_type === 'annual_rate_limit'">
          <el-form-item label="年化利率阈值 %">
            <el-input v-model.number="form.params.threshold_pct" :aria-label="'年化利率阈值'" />
          </el-form-item>
        </template>
        <template v-else-if="form.calc_type === 'lpr_multiple_limit'">
          <el-form-item label="LPR 倍数">
            <el-input v-model.number="form.params.multiplier" :aria-label="'LPR 倍数'" />
          </el-form-item>
        </template>
        <template v-else>
          <el-form-item label="综合年化成本阈值 %">
            <el-input v-model.number="form.params.threshold_pct" :aria-label="'综合年化成本阈值'" />
          </el-form-item>
          <el-form-item label="逾期情景天数">
            <el-input v-model.number="form.params.overdue_days" :aria-label="'逾期情景天数'" />
            <span class="muted note">0 表示不评估逾期情景。</span>
          </el-form-item>
        </template>
        <el-form-item label="法律依据">
          <el-input v-model="form.legal_basis" type="textarea" :rows="3" :aria-label="'法律依据'" />
        </el-form-item>
        <el-form-item label="法务复核人">
          <el-input v-model="form.reviewer" :aria-label="'法务复核人'" />
        </el-form-item>
        <el-form-item label="复核日期">
          <el-input v-model="form.reviewed_at" :aria-label="'复核日期'" />
        </el-form-item>
        <el-form-item label="演示规则">
          <el-switch v-model="form.demo_only" :disabled="!!editingId" :aria-label="'演示规则'" />
          <span class="muted note">演示规则不得用于生产模式正式报告。</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :aria-label="'取消' " @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="submitting" :aria-label="'保存规则包'" @click="submitForm">保存</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.toolbar { margin: 12px 0; }
.authority-input { width: 240px; margin: 0 8px; }
.note { margin-left: 12px; font-size: 12px; }
.date-input { width: 130px; margin-right: 6px; }
.entry-chip { margin-right: 8px; }
.muted { color: #909399; }
.error-text { color: #d64541; }
.success-text { color: #2e7d32; }
</style>
