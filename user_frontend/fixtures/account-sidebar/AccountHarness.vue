<script setup lang="ts">
/** 真实账户壳。路由由夹具路由器决定，不代表登录会话。 */
import { onDeactivated, onUnmounted } from 'vue'
import UhdAccountLayout from '@/components/uhd/UhdAccountLayout.vue'

defineOptions({ name: 'AccountHarness' })
onDeactivated(() => {
  const w = window as unknown as { __ad?: number }
  w.__ad = (w.__ad || 0) + 1
})
onUnmounted(() => {
  const w = window as unknown as { __au?: number }
  w.__au = (w.__au || 0) + 1
})
</script>

<template>
  <UhdAccountLayout title="夹具页" eyebrow="FIXTURE">
    <article id="fixture-body">
      <h2 id="fixture-content">主栏内容</h2>
      <p>没有用户、余额或套餐。</p>
      <button id="fixture-main-btn" type="button">主栏按钮</button>
    </article>
  </UhdAccountLayout>
</template>
