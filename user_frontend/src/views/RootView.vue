<script setup lang="ts">
/**
 * 根路径 '/' 分发组件（2026-11-13 官网落点定版＋同日登录落点修复）：
 * 未登录渲染官网 LandingView（URL 保持 '/'，不跳 /welcome）。
 * 已登录的落点是片库首页 /library（UHD 式新首页）：路由守卫在进入 '/'
 * 前就把已登录用户重定向过去（见 router/index.ts），本组件只做兜底——
 * 若登录态在停留 '/' 期间变活（如他处完成登录回流），立即 replace 过去，
 * 不再渲染任何旧发现页（DiscoveryView 已连根删除）。
 */
import { computed, defineAsyncComponent, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { isPanelV4Enabled } from '@/composables/usePanelApi'

defineOptions({ name: 'RootView' })

// 懒加载口径与原路由分片一致：官网仍自成 chunk，不进首包
const LandingView = defineAsyncComponent(() => import('@/views/marketing/LandingView.vue'))

const userStore = useUserStore()
const router = useRouter()
const isLoggedIn = computed(() => userStore.isLoggedIn)

// 兜底重定向：守卫已覆盖常规进入路径，这里只兜「停留在 '/' 时登录态变活」。
// panelV4 关闭（回退开关）时 /library 路由不存在，不跳、继续渲染官网面。
watch(
  isLoggedIn,
  (loggedIn) => {
    if (loggedIn && isPanelV4Enabled()) {
      void router.replace('/library')
    }
  },
  { immediate: true },
)
</script>

<template>
  <LandingView v-if="!isLoggedIn" />
</template>
