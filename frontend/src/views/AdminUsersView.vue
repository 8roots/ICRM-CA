<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { request, type ManagedUser } from '../api/client'

const users = ref<ManagedUser[]>([])
const error = ref('')
const form = reactive({ username: '', password: '', enabled: true })

onMounted(load)

async function load() {
  users.value = await request<ManagedUser[]>('/api/v1/admin/users')
}

async function createUser() {
  error.value = ''
  try {
    const created = await request<ManagedUser>('/api/v1/admin/users', {
      method: 'POST',
      body: JSON.stringify(form),
    })
    users.value.push(created)
    form.username = ''
    form.password = ''
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '创建失败'
  }
}

async function setEnabled(user: ManagedUser, enabled: boolean) {
  const updated = await request<ManagedUser>(`/api/v1/admin/users/${user.id}`, {
    method: 'PATCH',
    body: JSON.stringify({ enabled, version: user.version }),
  })
  Object.assign(user, updated)
}
</script>

<template>
  <section>
    <h1>审批人员账号</h1>
    <el-card>
      <form @submit.prevent="createUser">
        <label>用户名<input v-model="form.username" aria-label="新用户名" required /></label>
        <label>初始密码<input v-model="form.password" aria-label="初始密码" type="password" minlength="12" required /></label>
        <label><input v-model="form.enabled" type="checkbox" /> 启用</label>
        <p v-if="error" role="alert">{{ error }}</p>
        <el-button native-type="submit" type="primary">创建审批人员</el-button>
      </form>
    </el-card>
    <el-table :data="users">
      <el-table-column label="用户名" prop="username" />
      <el-table-column label="角色" prop="role" />
      <el-table-column label="状态">
        <template #default="scope">
          <el-switch
            :model-value="scope.row.enabled"
            :disabled="scope.row.role === 'administrator'"
            aria-label="启用账号"
            @change="(value: boolean) => setEnabled(scope.row, value)"
          />
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>
