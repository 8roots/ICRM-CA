<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { request, type Application, type Document, type DocumentJob, type Lifecycle } from '../api/client'

const route = useRoute()
const application = ref<Application | null>(null)
const lifecycle = ref<Lifecycle | null>(null)
const documents = ref<Document[]>([])
const uploading = ref(false)
const retryReasons = ref<Record<string, string>>({})
const uploadErrors = ref<{ filename: string, message: string }[]>([])
const retryErrors = ref<Record<string, string>>({})
const actionError = ref('')
const actionNotice = ref('')
const reopenDialog = ref(false)
const reopenReason = ref('')
const archiveDialog = ref(false)
const completeDialog = ref(false)
let timer: number | undefined

const stateLabels: Record<string, string> = {
  draft: '草稿', processing: '处理中', pending_review: '待复核',
  review_complete: '辅助审查完成', archived: '已归档',
}
const blockerLabels: Record<string, string> = {
  running_jobs: '仍有材料正在处理',
  missing_redline_report: '尚无红线正式报告',
  stale_redline_report: '红线正式报告已失效，请重新评估',
  missing_completeness_report: '尚无完备性正式报告',
  stale_completeness_report: '完备性正式报告已失效，请重新检查',
}
const labels: Record<string, string> = {
  waiting: '等待处理', running: '处理中', success: '处理成功', failed: '处理失败',
  manual_handling: '需人工处理',
}
const errorLabels: Record<string, string> = {
  signature_mismatch: '材料签名与格式不匹配，请确认文件未损坏',
  mime_mismatch: 'MIME 与格式不匹配',
  unsupported_legacy_office: '不支持旧版 Office 材料，请转换为 DOCX/XLSX 后重新上传',
  unsupported_macro: '不支持含宏的材料，请另存为无宏的 DOCX/XLSX 后重新上传',
  unsupported_archive: '不支持压缩包材料，请解压后直接上传文件',
  unsupported_format: '不支持此格式',
  encrypted_input: '材料已加密，请解除密码保护后重新上传',
  object_store_unavailable: '对象存储暂时不可用',
}

function latestJob(document: Document): DocumentJob | undefined {
  return document.jobs.at(-1)
}

async function refresh() {
  documents.value = await request<Document[]>(`/api/v1/applications/${route.params.id}/documents`)
}

async function refreshLifecycle() {
  lifecycle.value = await request<Lifecycle>(`/api/v1/applications/${route.params.id}/lifecycle`)
  // keep the descriptions' state/version in sync with the latest lifecycle
  if (application.value && lifecycle.value) {
    application.value.lifecycle_state = lifecycle.value.state
    application.value.version = lifecycle.value.version
  }
}

async function upload(event: Event) {
  const files = Array.from((event.target as HTMLInputElement).files ?? [])
  uploading.value = true
  uploadErrors.value = []
  await Promise.all(files.map(async (file) => {
    try {
      const body = new FormData()
      body.append('file', file)
      const result = await request<{ document: Document }>(
        `/api/v1/applications/${route.params.id}/documents`,
        { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body },
      )
      const index = documents.value.findIndex((item) => item.id === result.document.id)
      if (index >= 0) documents.value[index] = result.document
      else documents.value.push(result.document)
    } catch (error) {
      uploadErrors.value.push({
        filename: file.name,
        message: error instanceof Error ? error.message : '上传失败',
      })
    }
  }))
  uploading.value = false
  try {
    await refreshLifecycle()
  } catch {
    // polling refresh keeps the lifecycle state in sync
  }
}

async function retry(document: Document, job: DocumentJob) {
  const reason = retryReasons.value[document.id]?.trim()
  if (!reason) return
  retryErrors.value[document.id] = ''
  try {
    const updated = await request<DocumentJob>(`/api/v1/jobs/${job.id}/retry`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ reason, selected_steps: ['validation'] }),
    })
    document.jobs[document.jobs.length - 1] = updated
    document.processing_status = updated.status
  } catch (error) {
    retryErrors.value[document.id] = error instanceof Error ? error.message : '重试失败'
  }
}

async function completeApplication() {
  actionError.value = ''
  completeDialog.value = false
  if (!lifecycle.value) return
  if (lifecycle.value.completion_blockers.length) {
    actionError.value = '尚不能标记完成：' + lifecycle.value.completion_blockers.map((b) => blockerLabels[b] || b).join('；')
    return
  }
  try {
    await request(`/api/v1/applications/${route.params.id}/complete`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ version: lifecycle.value.version }),
    })
    actionNotice.value = '已标记辅助审查完成'
    await refreshLifecycle()
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function reopenApplication() {
  actionError.value = ''
  const reason = reopenReason.value.trim()
  if (!reason) return
  reopenDialog.value = false
  if (!lifecycle.value) return
  try {
    await request(`/api/v1/applications/${route.params.id}/reopen`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ version: lifecycle.value.version, reason }),
    })
    reopenReason.value = ''
    actionNotice.value = '已重新打开申请'
    await refreshLifecycle()
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function archiveApplication() {
  actionError.value = ''
  archiveDialog.value = false
  if (!lifecycle.value) return
  try {
    await request(`/api/v1/applications/${route.params.id}/archive`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ version: lifecycle.value.version }),
    })
    actionNotice.value = '已归档'
    await refreshLifecycle()
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : '操作失败'
  }
}

onMounted(async () => {
  application.value = await request<Application>(`/api/v1/applications/${route.params.id}`)
  await refreshLifecycle()
  await refresh()
  timer = window.setInterval(refresh, 3000)
})
onUnmounted(() => window.clearInterval(timer))
</script>

<template>
  <section v-if="application">
    <router-link to="/applications">返回我的申请</router-link>
    <h1>{{ application.primary_borrower.name }}</h1>
    <el-descriptions border :column="2">
      <el-descriptions-item label="主借款人类型">
        {{ application.primary_borrower.type === 'corporate' ? '企业' : '个人' }}
      </el-descriptions-item>
      <el-descriptions-item label="产品">{{ application.product }}</el-descriptions-item>
      <el-descriptions-item label="申请日期">{{ application.application_date }}</el-descriptions-item>
      <el-descriptions-item label="拟签约日期">
        {{ application.proposed_signing_date || '未填写' }}
      </el-descriptions-item>
      <el-descriptions-item label="状态">{{ stateLabels[application.lifecycle_state] || application.lifecycle_state }}</el-descriptions-item>
      <el-descriptions-item label="版本">{{ application.version }}</el-descriptions-item>
    </el-descriptions>

    <el-alert
      v-if="lifecycle && !lifecycle.editable"
      type="info"
      title="该申请已进入只读状态，如需继续编辑请先重新打开"
      :closable="false"
      show-icon
    />
    <el-alert v-if="actionNotice" type="success" :title="actionNotice" :closable="true" @close="actionNotice = ''" />
    <el-alert v-if="actionError" type="error" :title="actionError" :closable="true" @close="actionError = ''" />

    <div class="lifecycle-actions">
      <el-button
        v-if="lifecycle && lifecycle.can_complete === true"
        data-test="complete-application"
        type="primary"
        @click="completeDialog = true"
      >标记辅助审查完成</el-button>
      <el-button
        v-if="lifecycle && lifecycle.can_archive === true"
        data-test="archive-application"
        type="warning"
        @click="archiveDialog = true"
      >归档</el-button>
      <el-button
        v-if="lifecycle && lifecycle.can_reopen === true"
        data-test="reopen-application"
        @click="reopenDialog = true"
      >重新打开</el-button>
    </div>

    <el-dialog v-model="completeDialog" title="标记辅助审查完成" width="480px">
      <p>确认已复核当前红线与完备性正式报告？存在缺件、风险提示或资料不足仍可完成，但将保持显式展示。</p>
      <template #footer>
        <el-button @click="completeDialog = false">取消</el-button>
        <el-button type="primary" @click="completeApplication">确认完成</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="reopenDialog" title="重新打开申请" width="480px">
      <el-input
        v-model="reopenReason"
        type="textarea"
        :rows="3"
        maxlength="2000"
        placeholder="必须填写重新打开的原因（将记入审计）"
        data-test="reopen-reason"
      />
      <template #footer>
        <el-button @click="reopenDialog = false">取消</el-button>
        <el-button type="primary" :disabled="!reopenReason.trim()" @click="reopenApplication">确认重新打开</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="archiveDialog" title="归档申请" width="480px">
      <p>归档后该申请进入只读状态，不再接受上传与修改；可随时带理由重新打开。</p>
      <template #footer>
        <el-button @click="archiveDialog = false">取消</el-button>
        <el-button type="warning" data-test="confirm-archive" @click="archiveApplication">确认归档</el-button>
      </template>
    </el-dialog>

    <h2>材料处理</h2>
    <p>
      <router-link
        class="candidate-link"
        :to="`/applications/${route.params.id}/candidates`"
      >字段候选复核与人工确认</router-link>
      <span class="link-sep">·</span>
      <router-link
        class="candidate-link"
        :to="`/applications/${route.params.id}/completeness`"
      >材料完备性与正式报告</router-link>
      <span class="link-sep">·</span>
      <router-link
        class="candidate-link"
        :to="`/applications/${route.params.id}/redline`"
      >红线评估与正式报告</router-link>
    </p>
    <label class="upload-button">
      {{ uploading ? '上传中…' : '批量上传材料' }}
      <input type="file" multiple :disabled="uploading || (lifecycle ? !lifecycle.editable : false)" @change="upload">
    </label>
    <el-alert
      v-for="error in uploadErrors"
      :key="error.filename"
      type="error"
      :title="`${error.filename}：${error.message}`"
      :closable="false"
    />
    <el-table :data="documents" empty-text="尚未上传材料">
      <el-table-column prop="filename" label="材料" />
      <el-table-column label="状态">
        <template #default="scope">{{ labels[scope.row.processing_status] || scope.row.processing_status }}</template>
      </el-table-column>
      <el-table-column label="处理结果">
        <template #default="scope">
          <template v-if="latestJob(scope.row)?.error_code">
            {{ errorLabels[latestJob(scope.row)?.error_code || ''] || latestJob(scope.row)?.error_code }}
          </template>
          <span v-if="latestJob(scope.row)?.retry_reason">；重试原因：{{ latestJob(scope.row)?.retry_reason }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作">
        <template #default="scope">
          <router-link
            v-if="['success', 'partial_success', 'failed'].includes(scope.row.processing_status)"
            :to="`/documents/${scope.row.id}/evidence`"
            :aria-label="`证据预览-${scope.row.filename}`"
          >证据预览</router-link>
          <a
            class="download-link"
            :href="`/api/v1/documents/${scope.row.id}/download`"
            download
            :aria-label="`下载原件-${scope.row.filename}`"
          >下载原件</a>
          <template v-if="['failed', 'manual_handling'].includes(scope.row.processing_status) && (lifecycle ? lifecycle.editable : true)">
            <el-input v-model="retryReasons[scope.row.id]" :aria-label="`重试原因-${scope.row.filename}`" placeholder="填写重试原因" />
            <el-button
              :aria-label="`重试-${scope.row.filename}`"
              @click="latestJob(scope.row) && retry(scope.row, latestJob(scope.row)!)"
            >重试</el-button>
            <p v-if="retryErrors[scope.row.id]" role="alert">{{ retryErrors[scope.row.id] }}</p>
          </template>
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>

<style scoped>
.upload-button { display: inline-block; margin-bottom: 16px; cursor: pointer; color: #1769aa; }
.upload-button input { display: block; margin-top: 8px; }
.download-link { margin-left: 8px; color: #1769aa; }
.candidate-link { color: #1769aa; }
.link-sep { margin: 0 8px; color: #c0c4cc; }
.lifecycle-actions { margin: 12px 0; }
</style>
