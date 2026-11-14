<script setup lang="ts">
/** 包一层真实官网，用来观察 KeepAlive 退出。 */
import { onDeactivated, onUnmounted } from 'vue'
import LandingView from '@/views/marketing/LandingView.vue'

onDeactivated(() => {
  const w = window as unknown as { __ld?: number }
  w.__ld = (w.__ld || 0) + 1
})
onUnmounted(() => {
  const w = window as unknown as { __lu?: number }
  w.__lu = (w.__lu || 0) + 1
})
</script>

<template>
  <LandingView />
</template>
