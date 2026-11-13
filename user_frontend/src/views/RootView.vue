<script setup lang="ts">
/**
 * 根路径 '/' 分发组件（2026-11-13 官网落点定版）：
 * 未登录渲染官网 LandingView（URL 保持 '/'，不跳 /welcome），
 * 已登录渲染发现页 DiscoveryView（内容与数据口径原样）。
 * 登录态读 user store 的响应式 isLoggedIn：同一页面内登录/登出即时切换，
 * 不靠首次加载判一次。两页本体不在本组件内做任何改动。
 */
import { computed, defineAsyncComponent } from 'vue'
import { useUserStore } from '@/stores/user'

defineOptions({ name: 'RootView' })

// 懒加载口径与原路由分片一致：两页仍各自成 chunk，不进首包
const LandingView = defineAsyncComponent(() => import('@/views/marketing/LandingView.vue'))
const DiscoveryView = defineAsyncComponent(() => import('@/views/panel/DiscoveryView.vue'))

const userStore = useUserStore()
const isLoggedIn = computed(() => userStore.isLoggedIn)
</script>

<template>
  <DiscoveryView v-if="isLoggedIn" />
  <LandingView v-else />
</template>
