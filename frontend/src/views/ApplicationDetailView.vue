<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { request, type Application } from '../api/client'

const route = useRoute()
const application = ref<Application | null>(null)

onMounted(async () => {
  application.value = await request<Application>(`/api/v1/applications/${route.params.id}`)
})
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
  </section>
</template>
