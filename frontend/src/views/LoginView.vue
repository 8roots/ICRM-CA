<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const username = ref('')
const password = ref('')
const error = ref('')
const auth = useAuthStore()
const router = useRouter()

async function submit() {
  error.value = ''
  try {
    await auth.login(username.value, password.value)
    await router.push(auth.user?.role === 'administrator' ? '/admin/users' : '/applications')
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '登录失败'
  }
}
</script>

<template>
  <el-card class="login-card">
    <h1>登录</h1>
    <form @submit.prevent="submit">
      <label>用户名<input v-model="username" aria-label="用户名" autocomplete="username" /></label>
      <label>密码<input v-model="password" aria-label="密码" type="password" autocomplete="current-password" /></label>
      <p v-if="error" role="alert">{{ error }}</p>
      <el-button native-type="submit" type="primary">登录</el-button>
    </form>
  </el-card>
</template>
