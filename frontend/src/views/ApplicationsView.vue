<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { request, type Application, type ApplicationInput } from '../api/client'

const applications = ref<Application[]>([])
const error = ref('')
const form = reactive<ApplicationInput>({
  primary_borrower: { type: 'corporate', name: '' },
  product: '',
  application_date: new Date().toISOString().slice(0, 10),
  proposed_signing_date: null,
})

onMounted(async () => {
  applications.value = await request<Application[]>('/api/v1/applications')
})

async function createApplication() {
  error.value = ''
  try {
    const created = await request<Application>('/api/v1/applications', {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify(form),
    })
    applications.value.unshift(created)
    form.primary_borrower.name = ''
    form.product = ''
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '创建失败'
  }
}
</script>

<template>
  <section>
    <h1>我的申请</h1>
    <el-card>
      <h2>新建申请</h2>
      <form @submit.prevent="createApplication">
        <label>主借款人类型
          <select v-model="form.primary_borrower.type" aria-label="主借款人类型">
            <option value="corporate">企业</option>
            <option value="individual">个人</option>
          </select>
        </label>
        <label>主借款人名称<input v-model="form.primary_borrower.name" aria-label="主借款人名称" required /></label>
        <label>产品<input v-model="form.product" aria-label="产品" required /></label>
        <label>申请日期<input v-model="form.application_date" aria-label="申请日期" type="date" required /></label>
        <label>拟签约日期<input v-model="form.proposed_signing_date" aria-label="拟签约日期" type="date" /></label>
        <p v-if="error" role="alert">{{ error }}</p>
        <el-button data-test="create-application" native-type="submit" type="primary">创建申请</el-button>
      </form>
    </el-card>
    <el-table :data="applications" empty-text="暂无申请">
      <el-table-column label="主借款人">
        <template #default="scope">
          <router-link :to="`/applications/${scope.row.id}`">{{ scope.row.primary_borrower.name }}</router-link>
        </template>
      </el-table-column>
      <el-table-column label="类型">
        <template #default="scope">{{ scope.row.primary_borrower.type === 'corporate' ? '企业' : '个人' }}</template>
      </el-table-column>
      <el-table-column label="产品" prop="product" />
      <el-table-column label="状态" prop="lifecycle_state" />
      <el-table-column label="申请日期" prop="application_date" />
    </el-table>
  </section>
</template>
