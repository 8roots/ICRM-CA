<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { request, type QueueStatus } from '../api/client'

const queue = ref<QueueStatus | null>(null)
const error = ref('')
let timer: number | undefined

const statusLabels: Record<string, string> = {
  waiting: '等待处理', running: '处理中', success: '处理成功',
  partial_success: '部分成功', failed: '处理失败',
  manual_handling: '需人工处理', not_applicable: '不适用',
}
const errorLabels: Record<string, string> = {
  signature_mismatch: '材料签名与格式不匹配',
  mime_mismatch: 'MIME 与格式不匹配',
  unsupported_format: '不支持此格式',
  unsupported_legacy_office: '不支持旧版 Office',
  unsupported_macro: '不支持含宏材料',
  unsupported_archive: '不支持压缩包',
  encrypted_input: '材料已加密',
  object_store_unavailable: '对象存储不可用',
  worker_crash_attempts_exhausted: '处理进程中断且重试次数耗尽',
}

async function load() {
  try {
    queue.value = await request<QueueStatus>('/api/v1/admin/queue')
    error.value = ''
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '加载失败'
  }
}

onMounted(() => {
  load()
  timer = window.setInterval(load, 10000)
})
onUnmounted(() => window.clearInterval(timer))
</script>

<template>
  <section>
    <h1>任务队列</h1>
    <el-alert v-if="error" type="error" :title="error" :closable="true" @close="error = ''" />
    <template v-if="queue">
      <el-row :gutter="16">
        <el-col :span="6"><el-card><h3>等待</h3><p class="count">{{ queue.waiting }}</p></el-card></el-col>
        <el-col :span="6"><el-card><h3>处理中</h3><p class="count">{{ queue.running }}</p></el-card></el-col>
        <el-col :span="6"><el-card><h3>失败</h3><p class="count">{{ queue.failed }}</p></el-card></el-col>
        <el-col :span="6"><el-card><h3>需人工处理</h3><p class="count">{{ queue.manual_handling }}</p></el-card></el-col>
      </el-row>

      <h2>处理 Worker</h2>
      <el-table :data="queue.workers" empty-text="暂无 worker 心跳">
        <el-table-column prop="worker_id" label="Worker" />
        <el-table-column label="最后心跳">
          <template #default="scope">{{ new Date(scope.row.last_seen_at).toLocaleString() }}</template>
        </el-table-column>
        <el-table-column label="状态">
          <template #default="scope">
            <el-tag :type="scope.row.healthy ? 'success' : 'danger'">{{ scope.row.healthy ? '正常' : '失联' }}</el-tag>
          </template>
        </el-table-column>
      </el-table>

      <h2>最近失败与人工处理</h2>
      <el-table :data="queue.recent_failures" empty-text="暂无失败任务">
        <el-table-column prop="filename" label="材料" />
        <el-table-column label="状态">
          <template #default="scope">{{ statusLabels[scope.row.status] || scope.row.status }}</template>
        </el-table-column>
        <el-table-column label="错误">
          <template #default="scope">{{ errorLabels[scope.row.error_code || ''] || scope.row.error_code || '-' }}</template>
        </el-table-column>
        <el-table-column label="重试原因" prop="retry_reason" />
        <el-table-column label="尝试次数" prop="attempts" width="90" />
        <el-table-column label="创建时间">
          <template #default="scope">{{ new Date(scope.row.created_at).toLocaleString() }}</template>
        </el-table-column>
      </el-table>
    </template>
  </section>
</template>

<style scoped>
.count { font-size: 28px; font-weight: 600; margin: 0; }
</style>
