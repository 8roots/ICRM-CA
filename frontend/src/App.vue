<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useAuthStore } from './stores/auth'

const auth = useAuthStore()
const router = useRouter()

async function logout() {
  await auth.logout()
  await router.push('/login')
}
</script>

<template>
  <el-container class="shell">
    <el-header class="header">
      <strong>智能信贷风控合规助手</strong>
      <nav v-if="auth.user">
        <router-link v-if="auth.user.role === 'approval_officer'" to="/applications">申请</router-link>
        <router-link v-if="auth.user.role === 'administrator'" to="/admin/users">账号管理</router-link>
        <router-link v-if="auth.user.role === 'administrator'" to="/admin/templates">模板管理</router-link>
        <el-button link @click="logout">退出</el-button>
      </nav>
    </el-header>
    <el-main><router-view /></el-main>
  </el-container>
</template>
