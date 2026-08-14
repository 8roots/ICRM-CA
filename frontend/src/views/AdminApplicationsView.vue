<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { request, type AdminApplication, type ManagedUser } from '../api/client'

const applications = ref<AdminApplication[]>([])
const officers = ref<ManagedUser[]>([])
const error = ref('')
const notice = ref('')
const reassignDialog = ref(false)
const reassignTarget = ref<AdminApplication | null>(null)
const reassignOwner = ref('')
const hardDeleteStep = ref(0) // 0 = reason, 1 = confirmation token
const hardDeleteTarget = ref<AdminApplication | null>(null)
const deleteReason = ref('')
const deleteToken = ref('')
const deleteExpiry = ref('')

const stateLabels: Record<string, string> = {
  draft: '草稿', processing: '处理中', pending_review: '待复核',
  review_complete: '辅助审查完成', archived: '已归档',
}

onMounted(async () => {
  await load()
  officers.value = (await request<ManagedUser[]>('/api/v1/admin/users')).filter(
    (user) => user.role === 'approval_officer',
  )
})

async function load() {
  applications.value = await request<AdminApplication[]>('/api/v1/admin/applications')
}

function openReassign(application: AdminApplication) {
  reassignTarget.value = application
  reassignOwner.value = application.owner_id
  reassignDialog.value = true
}

async function confirmReassign() {
  error.value = ''
  if (!reassignTarget.value || !reassignOwner.value) return
  try {
    await request(`/api/v1/applications/${reassignTarget.value.id}/reassign`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ version: reassignTarget.value.version, owner_id: reassignOwner.value }),
    })
    reassignDialog.value = false
    notice.value = `已将申请重新分配给 ${officers.value.find((o) => o.id === reassignOwner.value)?.username || reassignOwner.value}`
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '操作失败'
  }
}

function copyToken() {
  if (typeof navigator !== 'undefined' && navigator.clipboard) {
    navigator.clipboard.writeText(deleteToken.value)
  }
}

function openHardDelete(application: AdminApplication) {
  hardDeleteTarget.value = application
  deleteReason.value = ''
  deleteToken.value = ''
  deleteExpiry.value = ''
  hardDeleteStep.value = 0
}

async function requestHardDelete() {
  error.value = ''
  if (!hardDeleteTarget.value) return
  try {
    const result = await request<{ confirmation_token: string, expires_at: string }>(
      `/api/v1/applications/${hardDeleteTarget.value.id}/hard-delete-requests`,
      { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ reason: deleteReason.value }) },
    )
    deleteToken.value = result.confirmation_token
    deleteExpiry.value = new Date(result.expires_at).toLocaleString()
    hardDeleteStep.value = 1
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '操作失败'
  }
}

async function confirmHardDelete() {
  error.value = ''
  if (!hardDeleteTarget.value || !deleteToken.value) return
  try {
    await request(`/api/v1/applications/${hardDeleteTarget.value.id}/hard-delete`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ confirmation_token: deleteToken.value }),
    })
    hardDeleteStep.value = 0
    deleteToken.value = ''
    notice.value = `已硬删除申请 ${hardDeleteTarget.value.borrower_name}（仅保留审计墓碑）`
    hardDeleteTarget.value = null
    await load()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '操作失败'
  }
}
</script>

<template>
  <section>
    <h1>申请元数据</h1>
    <el-alert type="info" title="管理员仅查看业务元数据；材料原件、候选与报告仅对当前负责人可见。" :closable="false" show-icon />
    <el-alert v-if="notice" type="success" :title="notice" :closable="true" @close="notice = ''" />
    <el-alert v-if="error" type="error" :title="error" :closable="true" @close="error = ''" />
    <el-table :data="applications" empty-text="暂无申请">
      <el-table-column label="主借款人" prop="borrower_name" />
      <el-table-column label="类型">
        <template #default="scope">{{ scope.row.borrower_type === 'corporate' ? '企业' : '个人' }}</template>
      </el-table-column>
      <el-table-column label="产品" prop="product" />
      <el-table-column label="申请日期" prop="application_date" />
      <el-table-column label="负责人" prop="owner_username" />
      <el-table-column label="状态">
        <template #default="scope">{{ stateLabels[scope.row.lifecycle_state] || scope.row.lifecycle_state }}</template>
      </el-table-column>
      <el-table-column label="操作">
        <template #default="scope">
          <el-button size="small" data-test="reassign-button" @click="openReassign(scope.row)">重新分配</el-button>
          <el-button size="small" type="danger" data-test="hard-delete-button" @click="openHardDelete(scope.row)">硬删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="reassignDialog" title="重新分配负责人" width="480px">
      <p>原负责人将立即失去该申请的访问权限；管理员本身不获得材料访问权限。</p>
      <el-select v-model="reassignOwner" data-test="reassign-owner" placeholder="选择新的审批人员" style="width: 100%">
        <el-option
          v-for="officer in officers"
          :key="officer.id"
          :label="officer.username"
          :value="officer.id"
        />
      </el-select>
      <template #footer>
        <el-button @click="reassignDialog = false">取消</el-button>
        <el-button type="primary" @click="confirmReassign">确认重新分配</el-button>
      </template>
    </el-dialog>

    <el-dialog
      :model-value="hardDeleteStep === 0"
      title="整笔硬删除（第一步：原因）"
      width="520px"
      @close="hardDeleteStep = 0"
    >
      <el-alert
        type="error"
        title="将永久删除该申请的全部材料原件、解析结果、候选、确认值与报告，仅保留不含业务敏感内容的审计墓碑。此操作不可撤销。"
        :closable="false"
        show-icon
      />
      <el-input
        v-model="deleteReason"
        type="textarea"
        :rows="3"
        maxlength="2000"
        placeholder="必须填写删除原因"
        data-test="delete-reason"
      />
      <template #footer>
        <el-button @click="hardDeleteStep = 0">取消</el-button>
        <el-button type="danger" :disabled="!deleteReason.trim()" @click="requestHardDelete">申请删除令牌</el-button>
      </template>
    </el-dialog>

    <el-dialog
      :model-value="hardDeleteStep === 1"
      title="整笔硬删除（第二步：二次确认）"
      width="520px"
      @close="hardDeleteStep = 0"
    >
      <p>复制以下确认令牌，并确认执行删除。令牌在 <strong>{{ deleteExpiry }}</strong> 前有效。</p>
      <el-input :model-value="deleteToken" readonly data-test="delete-token">
        <template #append>
          <el-button @click="copyToken">复制</el-button>
        </template>
      </el-input>
      <template #footer>
        <el-button @click="hardDeleteStep = 0">取消</el-button>
        <el-button type="danger" data-test="confirm-hard-delete" @click="confirmHardDelete">确认硬删除</el-button>
      </template>
    </el-dialog>
  </section>
</template>
