<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  request,
  type CreateTemplateRequest,
  type TemplateItemInput,
  type TemplateResponse,
} from '../api/client'
import { categoryLabel, categoryLabels, conditionLabels } from '../utils/completeness'
const templates = ref<TemplateResponse[]>([])
const loading = ref(false)
const actionError = ref('')
const actionSuccess = ref('')

const dialogOpen = ref(false)
const submitting = ref(false)
const editingId = ref<string | null>(null)
const form = ref<{
  code: string
  name: string
  product: string
  borrower_type: 'corporate' | 'individual'
  demo_only: boolean
  items: TemplateItemInput[]
}>({ code: '', name: '', product: '经营贷', borrower_type: 'corporate', demo_only: false, items: [] })

const statusLabels: Record<string, string> = { draft: '草稿', published: '已发布', retired: '已停用' }
const borrowerLabels: Record<string, string> = { corporate: '企业', individual: '个人' }

const grouped = computed(() => {
  const byCode = new Map<string, TemplateResponse[]>()
  for (const template of templates.value) {
    const list = byCode.get(template.code) ?? []
    list.push(template)
    byCode.set(template.code, list)
  }
  return [...byCode.entries()].map(([code, versions]) => ({
    code,
    versions: versions.sort((a, b) => b.version - a.version),
  }))
})

function emptyItem(): TemplateItemInput {
  return {
    code: '',
    label: '',
    category: 'basic_info',
    requires_seal: false,
    requires_signature: false,
    condition: null,
  }
}

function openCreate() {
  editingId.value = null
  form.value = {
    code: '',
    name: '',
    product: '经营贷',
    borrower_type: 'corporate',
    demo_only: false,
    items: [emptyItem()],
  }
  actionError.value = ''
  dialogOpen.value = true
}

function openEdit(template: TemplateResponse) {
  editingId.value = template.id
  form.value = {
    code: template.code,
    name: template.name,
    product: template.product,
    borrower_type: template.borrower_type as 'corporate' | 'individual',
    demo_only: template.demo_only,
    items: template.items.map((item) => ({
      code: item.code,
      label: item.label,
      category: item.category as TemplateItemInput['category'],
      requires_seal: item.requires_seal,
      requires_signature: item.requires_signature,
      condition: item.condition as Record<string, unknown> | null,
    })),
  }
  actionError.value = ''
  dialogOpen.value = true
}

function addItem() {
  form.value.items.push(emptyItem())
}

function removeItem(index: number) {
  form.value.items.splice(index, 1)
}

async function refresh() {
  loading.value = true
  try {
    templates.value = await request<TemplateResponse[]>('/api/v1/admin/completeness-templates')
  } finally {
    loading.value = false
  }
}

function postAction(path: string, successMessage: string) {
  actionSuccess.value = ''
  actionError.value = ''
  return request<TemplateResponse>(path, {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
  })
    .then((updated) => {
      actionSuccess.value = successMessage
      refresh()
      return updated
    })
    .catch((caught) => {
      actionError.value = caught instanceof Error ? caught.message : '操作失败'
    })
}

async function submitCreate() {
  actionError.value = ''
  if (!form.value.code.trim() || !form.value.name.trim() || !form.value.product.trim()) {
    actionError.value = '请填写模板编码、名称与产品'
    return
  }
  const items = form.value.items.filter((item) => item.code.trim() && item.label.trim())
  if (items.length === 0) {
    actionError.value = '至少需要一个清单项'
    return
  }
  submitting.value = true
  try {
    const payload: CreateTemplateRequest = {
      code: form.value.code.trim().toUpperCase(),
      name: form.value.name.trim(),
      product: form.value.product.trim(),
      borrower_type: form.value.borrower_type,
      demo_only: form.value.demo_only,
      items,
    }
    if (editingId.value) {
      await request<TemplateResponse>(`/api/v1/admin/completeness-templates/${editingId.value}`, {
        method: 'PUT',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ name: payload.name, items: payload.items }),
      })
      dialogOpen.value = false
      actionSuccess.value = '草稿已更新'
    } else {
      await request<TemplateResponse>('/api/v1/admin/completeness-templates', {
        method: 'POST',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify(payload),
      })
      dialogOpen.value = false
      actionSuccess.value = '模板已创建（草稿）'
    }
    await refresh()
  } catch (caught) {
    actionError.value = caught instanceof Error ? caught.message : '保存失败'
  } finally {
    submitting.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <section>
    <h1>完备性模板管理</h1>
    <p class="template-note">
      模板按“产品 × 主借款人类型”版本化。已发布版本不可修改，只能复制为新草稿版本后再发布；同一产品与类型同时只允许一个已发布版本。
    </p>
    <div class="toolbar">
      <el-button type="primary" :aria-label="'新建模板'" @click="openCreate">新建模板</el-button>
      <el-button :aria-label="'刷新模板列表'" @click="refresh">刷新</el-button>
    </div>
    <p v-if="actionError" role="alert" class="error-text">{{ actionError }}</p>
    <p v-if="actionSuccess" role="status" class="success-text">{{ actionSuccess }}</p>

    <div v-for="group in grouped" :key="group.code" class="template-group">
      <h2>{{ group.code }}</h2>
      <el-table :data="group.versions" v-loading="loading" :aria-label="`模板版本-${group.code}`">
        <el-table-column label="版本" width="80">
          <template #default="scope">v{{ scope.row.version }}</template>
        </el-table-column>
        <el-table-column label="名称" prop="name" min-width="160" />
        <el-table-column label="产品" prop="product" width="120" />
        <el-table-column label="类型" width="90">
          <template #default="scope">{{ borrowerLabels[scope.row.borrower_type] }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="scope">
            <el-tag :type="scope.row.status === 'published' ? 'success' : scope.row.status === 'retired' ? 'info' : 'warning'">
              {{ statusLabels[scope.row.status] }}
            </el-tag>
            <el-tag v-if="scope.row.demo_only" size="small" type="danger" class="demo-tag">演示</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="清单项" width="90">
          <template #default="scope">{{ scope.row.items.length }} 项</template>
        </el-table-column>
        <el-table-column label="内容哈希" min-width="180">
          <template #default="scope">
            <span class="muted">{{ scope.row.content_hash.slice(0, 12) }}…</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="scope">
            <el-button
              v-if="scope.row.status === 'draft'"
              size="small"
              :aria-label="`编辑-${group.code}-v${scope.row.version}`"
              @click="openEdit(scope.row)"
            >编辑</el-button>
            <el-button
              v-if="scope.row.status === 'draft'"
              size="small"
              type="primary"
              :aria-label="`发布-${group.code}-v${scope.row.version}`"
              @click="postAction(`/api/v1/admin/completeness-templates/${scope.row.id}/publish`, '已发布')"
            >发布</el-button>
            <el-button
              v-if="scope.row.status === 'published'"
              size="small"
              :aria-label="`复制-${group.code}-v${scope.row.version}`"
              @click="postAction(`/api/v1/admin/completeness-templates/${scope.row.id}/copy`, '已复制为新草稿版本')"
            >复制</el-button>
            <el-button
              v-if="scope.row.status === 'published'"
              size="small"
              type="danger"
              plain
              :aria-label="`停用-${group.code}-v${scope.row.version}`"
              @click="postAction(`/api/v1/admin/completeness-templates/${scope.row.id}/retire`, '已停用')"
            >停用</el-button>
          </template>
        </el-table-column>
      </el-table>
      <details class="item-details">
        <summary>查看清单项</summary>
        <ul>
          <li v-for="item in group.versions[0].items" :key="item.code">
            {{ item.code }} · {{ item.label }}（{{ categoryLabels[item.category] }}）
            <span v-if="item.requires_seal" class="muted">· 需印章</span>
            <span v-if="item.requires_signature" class="muted">· 需签字</span>
            <span v-if="item.condition" class="muted">· {{ categoryLabel(String(item.condition.requires)) }}</span>
          </li>
        </ul>
      </details>
    </div>
    <p v-if="!loading && grouped.length === 0">暂无模板。演示模板会在开发模式启动时自动发布。</p>

    <el-dialog v-model="dialogOpen" :title="editingId ? '编辑完备性模板（草稿）' : '新建完备性模板'" width="760px">
      <el-form label-width="110px">
        <el-form-item label="模板编码">
          <el-input
            v-model="form.code"
            :aria-label="'模板编码'"
            :disabled="editingId !== null"
            placeholder="例如 CORP-OPERATING-2026（大写字母/数字/下划线）"
          />
        </el-form-item>
        <el-form-item label="模板名称">
          <el-input v-model="form.name" :aria-label="'模板名称'" placeholder="例如 企业流动资金贷材料清单" />
        </el-form-item>
        <el-form-item label="产品">
          <el-input v-model="form.product" :aria-label="'模板产品'" placeholder="例如 经营贷" />
        </el-form-item>
        <el-form-item label="主借款人类型">
          <el-radio-group v-model="form.borrower_type" :aria-label="'主借款人类型'">
            <el-radio value="corporate">企业</el-radio>
            <el-radio value="individual">个人</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="仅演示">
          <el-switch v-model="form.demo_only" :aria-label="'仅演示模板'" />
          <span class="muted form-hint">演示模板在正式报告中被拒绝（生产模式）</span>
        </el-form-item>
        <el-form-item label="清单项">
          <el-table :data="form.items" :aria-label="'清单项编辑'" size="small">
            <el-table-column label="编号" width="150">
              <template #default="scope">
                <el-input v-model="scope.row.code" :aria-label="`清单项编号-${scope.$index}`" />
              </template>
            </el-table-column>
            <el-table-column label="名称" min-width="150">
              <template #default="scope">
                <el-input v-model="scope.row.label" :aria-label="`清单项名称-${scope.$index}`" />
              </template>
            </el-table-column>
            <el-table-column label="类别" width="110">
              <template #default="scope">
                <el-select v-model="scope.row.category" :aria-label="`清单项类别-${scope.$index}`">
                  <el-option v-for="(label, value) in categoryLabels" :key="value" :value="value" :label="label" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="需印章" width="80" align="center">
              <template #default="scope">
                <el-checkbox v-model="scope.row.requires_seal" :aria-label="`需印章-${scope.$index}`" />
              </template>
            </el-table-column>
            <el-table-column label="需签字" width="80" align="center">
              <template #default="scope">
                <el-checkbox v-model="scope.row.requires_signature" :aria-label="`需签字-${scope.$index}`" />
              </template>
            </el-table-column>
            <el-table-column label="条件" width="140">
              <template #default="scope">
                <el-select
                  v-model="scope.row.condition"
                  :aria-label="`清单项条件-${scope.$index}`"
                  clearable
                >
                  <el-option
                    v-for="(label, value) in conditionLabels"
                    :key="value"
                    :value="{ requires: value }"
                    :label="label"
                  />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="" width="60">
              <template #default="scope">
                <el-button
                  size="small"
                  type="danger"
                  plain
                  :disabled="form.items.length <= 1"
                  :aria-label="`删除清单项-${scope.$index}`"
                  @click="removeItem(scope.$index)"
                >删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-button size="small" :aria-label="'添加清单项'" @click="addItem">+ 添加清单项</el-button>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :aria-label="'取消新建模板'" @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="submitting" :aria-label="'提交新建模板'" @click="submitCreate">
          {{ editingId ? '保存草稿' : '创建草稿' }}
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.toolbar { margin-bottom: 12px; }
.template-note { color: #606266; font-size: 12px; }
.template-group { margin-bottom: 20px; }
.template-group h2 { font-size: 16px; }
.demo-tag { margin-left: 4px; }
.item-details { margin-top: 8px; }
.item-details summary { cursor: pointer; color: #1769aa; }
.muted { color: #909399; }
.form-hint { margin-left: 8px; }
.error-text { color: #d64541; }
.success-text { color: #2e7d32; }
</style>
