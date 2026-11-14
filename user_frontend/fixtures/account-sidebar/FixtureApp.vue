<script setup lang="ts">
/**
 * 只挂真实的 UhdAccountLayout。打开/关闭必须点组件自己的「导航」按钮。
 * 本页不改侧栏的 display，也不读取登录会话。
 */
import UhdAccountLayout from '@/components/uhd/UhdAccountLayout.vue'
</script>

<template>
  <p class="fixture-banner">本地隔离夹具 · 不是登录会话 · 不连接生产账户</p>
  <UhdAccountLayout title="夹具页" eyebrow="FIXTURE">
    <article id="fixture-body">
      <p>主栏夹具正文。没有用户、余额或套餐。</p>
      <button id="fixture-main-btn" type="button">主栏按钮</button>
      <div class="fixture-spacer" />
    </article>
  </UhdAccountLayout>
</template>

<style scoped>
.fixture-banner {
  margin: 0;
  padding: 8px 12px;
  background: #f5d90a;
  color: #1a1a1a;
  font: 600 14px/1.4 sans-serif;
}
.fixture-spacer {
  height: 1400px;
}
</style>
