<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { request, type Application, type Document, type DocumentJob } from '../api/client'

const route = useRoute()
const application = ref<Application | null>(null)
const documents = ref<Document[]>([])
const uploading = ref(false)
const retryReasons = ref<Record<string, string>>({})
const uploadErrors = ref<{ filename: string, message: string }[]>([])
const retryErrors = ref<Record<string, string>>({})
let timer: number | undefined

const labels: Record<string, string> = {
  waiting: '等待处理', running: '处理中', success: '处理成功', failed: '处理失败',
  manual_handling: '需人工处理',
}
const errorLabels: Record<string, string> = {
  signature_mismatch: '材料签名与格式不匹配', mime_mismatch: 'MIME 与格式不匹配',
  unsupported_legacy_office: '不支持旧版 Office 材料', unsupported_macro: '不支持宏材料',
  unsupported_archive: '不支持压缩材料', unsupported_format: '不支持此格式',
  encrypted_input: '材料已加密', object_store_unavailable: '对象存储暂时不可用',
}

function latestJob(document: Document): DocumentJob | undefined {
  return document.jobs.at(-1)
}

async function refresh() {
  documents.value = await request<Document[]>(`/api/v1/applications/${route.params.id}/documents`)
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

onMounted(async () => {
  application.value = await request<Application>(`/api/v1/applications/${route.params.id}`)
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
      <el-descriptions-item label="状态">{{ application.lifecycle_state }}</el-descriptions-item>
      <el-descriptions-item label="版本">{{ application.version }}</el-descriptions-item>
    </el-descriptions>

    <h2>材料处理</h2>
    <label class="upload-button">
      {{ uploading ? '上传中…' : '批量上传材料' }}
      <input type="file" multiple :disabled="uploading" @change="upload">
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
          <template v-if="['failed', 'manual_handling'].includes(scope.row.processing_status)">
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
</style>
