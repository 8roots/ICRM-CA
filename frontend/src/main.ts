import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './style.css'
import App from './App.vue'
import { pinia } from './pinia'
import { router } from './router'
import { useAuthStore } from './stores/auth'

async function bootstrap() {
  const app = createApp(App)
  app.use(pinia)
  app.use(router)
  app.use(ElementPlus)

  const auth = useAuthStore(pinia)
  await auth.load()
  if (!auth.user && router.currentRoute.value.path !== '/login') await router.replace('/login')
  if (auth.user && router.currentRoute.value.path === '/login') {
    await router.replace(auth.user.role === 'administrator' ? '/admin/users' : '/applications')
  }
  app.mount('#app')
}

void bootstrap()
