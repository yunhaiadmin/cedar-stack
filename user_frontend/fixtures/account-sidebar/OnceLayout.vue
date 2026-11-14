<script setup lang="ts">
/** 不进 KeepAlive，用来观察账户壳卸载。不是登录会话。 */
import { onUnmounted } from 'vue'
import UhdAccountLayout from '@/components/uhd/UhdAccountLayout.vue'

onUnmounted(() => {
  const w = window as unknown as { __once?: number }
  w.__once = (w.__once || 0) + 1
})
</script>

<template>
  <UhdAccountLayout title="一次性夹具" eyebrow="ONCE">
    <article id="fixture-body">
      <h2 id="fixture-content">一次性主栏</h2>
      <button id="fixture-main-btn" type="button">主栏按钮</button>
    </article>
  </UhdAccountLayout>
</template>
