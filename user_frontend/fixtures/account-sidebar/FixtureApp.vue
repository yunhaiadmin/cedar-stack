<script setup lang="ts">
/**
 * 只挂 UhdAccountLayout。侧栏数据是组件自己的静态分组。
 * 390 下产品没有打开侧栏的入口；「夹具切换侧栏显示」只存在于本页，用来看覆盖是否挤主栏。
 */
import { ref } from 'vue'
import UhdAccountLayout from '@/components/uhd/UhdAccountLayout.vue'

const forceOpen = ref(false)
</script>

<template>
  <p class="fixture-banner">本地隔离夹具 · 不是登录会话 · 不连接生产账户</p>
  <button id="fixture-toggle" type="button" @click="forceOpen = !forceOpen">
    夹具切换侧栏显示（{{ forceOpen ? '当前强制显示' : '当前按产品折叠' }}）
  </button>
  <div :class="{ 'fixture-side-open': forceOpen }">
    <UhdAccountLayout title="夹具页" eyebrow="FIXTURE">
      <article id="fixture-body">
        <p>主栏夹具正文。没有用户、余额或套餐。</p>
        <button id="fixture-main-btn" type="button">主栏按钮</button>
        <div class="fixture-spacer" />
      </article>
    </UhdAccountLayout>
  </div>
</template>

<style scoped>
.fixture-banner {
  margin: 0;
  padding: 8px 12px;
  background: #f5d90a;
  color: #1a1a1a;
  font: 600 14px/1.4 sans-serif;
}
#fixture-toggle {
  margin: 8px 12px;
  min-height: 44px;
  padding: 0 14px;
}
.fixture-spacer {
  height: 1400px;
}
@media (max-width: 768px) {
  .fixture-side-open :deep(.uhd-side) {
    display: flex !important;
  }
}
</style>
