<script setup lang="ts">
/** 真实账户壳。路由由夹具路由器决定，不代表登录会话。 */
import UhdAccountLayout from '@/components/uhd/UhdAccountLayout.vue'
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
