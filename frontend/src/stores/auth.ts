import { defineStore } from 'pinia'
import { request, type CurrentUser } from '../api/client'

export const useAuthStore = defineStore('auth', {
  state: () => ({ user: null as CurrentUser | null }),
  actions: {
    async load() {
      try {
        this.user = await request<CurrentUser>('/api/v1/auth/me')
      } catch {
        this.user = null
      }
    },
    async login(username: string, password: string) {
      await request<void>('/api/v1/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      this.user = await request<CurrentUser>('/api/v1/auth/me')
    },
    async logout() {
      await request<void>('/api/v1/auth/logout', { method: 'POST' })
      this.user = null
    },
  },
})
