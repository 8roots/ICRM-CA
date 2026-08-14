<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { request, type AuditEvent } from '../api/client'

const events = ref<AuditEvent[]>([])
const eventType = ref('')
const error = ref('')
const pageSize = ref(100)
const offset = ref(0)

const typeLabels: Record<string, string> = {
  'auth.login': '登录', 'auth.login_failed': '登录失败', 'auth.logout': '退出',
  'application.created': '创建申请', 'application.updated': '修改申请',
  'application.completed': '标记辅助审查完成', 'application.reopened': '重新打开',
  'application.archived': '归档', 'application.reassigned': '重新分配负责人',
  'application.hard_delete_requested': '请求硬删除', 'application.hard_deleted': '硬删除',
  'document.uploaded': '上传材料', 'document.downloaded': '下载材料',
  'document.viewed': '查看材料', 'resolution.created': '字段确认',
  'evidence_review.created': '证据复核', 'rule_context.confirmed': '确认规则上下文',
  'classification.confirmed': '确认材料分类', 'mapping.created': '清单映射',
  'mapping.deleted': '删除清单映射', 'waiver.created': '人工豁免',
  'completeness.run_created': '完备性正式报告', 'redline.run_created': '红线正式报告',
  'template.published': '模板发布', 'template.retired': '模板停用',
  'rule.approved': '规则批准', 'rule.retired': '规则停用',
  'lpr.published': 'LPR 发布', 'cloud.call_recorded': '云调用',
  'job.retried': '任务重试',
}
const resourceLabels: Record<string, string> = {
  application: '申请', document: '材料', template: '模板', rule: '规则',
  lpr: 'LPR', user: '用户', auth: '认证',
}

async function load() {
  error.value = ''
  const params = new URLSearchParams({ limit: String(pageSize.value), offset: String(offset.value) })
  if (eventType.value) params.set('event_type', eventType.value)
  try {
    events.value = await request<AuditEvent[]>(`/api/v1/audit/events?${params.toString()}`)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '加载失败'
  }
}

async function filter() {
  offset.value = 0
  await load()
}

onMounted(load)
</script>

<template>
  <section>
    <h1>审计日志</h1>
    <p class="hint">追加式审计：事件不可修改或删除；内容仅包含非敏感元数据，云调用详情在受限的云调用记录中单独授权查看。</p>
    <el-alert v-if="error" type="error" :title="error" :closable="true" @close="error = ''" />
    <div class="filters">
      <el-select v-model="eventType" clearable placeholder="事件类型" style="width: 260px" @change="filter">
        <el-option v-for="(label, value) in typeLabels" :key="value" :label="label" :value="value" />
      </el-select>
      <el-select v-model="pageSize" style="width: 140px" @change="filter">
        <el-option :value="100" label="每页 100" />
        <el-option :value="500" label="每页 500" />
        <el-option :value="1000" label="每页 1000" />
      </el-select>
    </div>
    <el-table :data="events" empty-text="暂无事件">
      <el-table-column label="时间" width="170">
        <template #default="scope">{{ new Date(scope.row.created_at).toLocaleString() }}</template>
      </el-table-column>
      <el-table-column label="事件">
        <template #default="scope">{{ typeLabels[scope.row.event_type] || scope.row.event_type }}</template>
      </el-table-column>
      <el-table-column label="操作者">
        <template #default="scope">{{ scope.row.actor_username || '系统' }}</template>
      </el-table-column>
      <el-table-column label="对象">
        <template #default="scope">
          {{ resourceLabels[scope.row.resource_type] || scope.row.resource_type }}
          <span v-if="scope.row.resource_id" class="mono">{{ scope.row.resource_id.slice(0, 8) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="关联 ID">
        <template #default="scope"><span class="mono">{{ scope.row.correlation_id || '-' }}</span></template>
      </el-table-column>
    </el-table>
    <div class="pager">
      <el-button :disabled="offset === 0" @click="offset -= pageSize; load()">上一页</el-button>
      <el-button @click="offset += pageSize; load()">下一页</el-button>
    </div>
  </section>
</template>

<style scoped>
.hint { color: #909399; font-size: 13px; }
.filters { display: flex; gap: 12px; margin-bottom: 12px; }
.mono { font-family: monospace; color: #909399; margin-left: 6px; }
.pager { margin-top: 12px; }
</style>
