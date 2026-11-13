<script setup lang="ts">
/**
 * 官网 LandingView 的加载占位（RootView 的 defineAsyncComponent 用）：
 * chunk 还在路上时先给一副浅色纸面骨架——胶囊导航＋大标题＋按钮行＋卡片行，
 * 不许白屏干等。纯占位无数据，加载完成即被真实页面替换。
 */
defineOptions({ name: 'LandingSkeleton' })
</script>

<template>
  <div class="ld-sk" role="status" aria-label="加载中">
    <div class="ld-sk__nav" />
    <div class="ld-sk__hero">
      <div class="ld-sk__line ld-sk__line--title" />
      <div class="ld-sk__line ld-sk__line--title2" />
      <div class="ld-sk__line ld-sk__line--sub" />
      <div class="ld-sk__btns">
        <div class="ld-sk__btn ld-sk__btn--primary" />
        <div class="ld-sk__btn" />
      </div>
    </div>
    <div class="ld-sk__cards">
      <div class="ld-sk__card" />
      <div class="ld-sk__card" />
      <div class="ld-sk__card" />
    </div>
  </div>
</template>

<style scoped>
.ld-sk {
  min-height: 100vh;
  background: #f5f1ea;
  padding: 20px clamp(16px, 5vw, 48px) 48px;
  box-sizing: border-box;
}
.ld-sk__nav {
  width: min(420px, 72vw);
  height: 44px;
  margin: 0 auto;
  border-radius: 999px;
  background: #eae3d5;
  animation: ld-sk-pulse 1.4s ease-in-out infinite;
}
.ld-sk__hero {
  max-width: 880px;
  margin: clamp(56px, 12vh, 120px) auto 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}
.ld-sk__line {
  border-radius: 10px;
  background: #eae3d5;
  animation: ld-sk-pulse 1.4s ease-in-out infinite;
}
.ld-sk__line--title {
  width: min(560px, 84vw);
  height: 52px;
}
.ld-sk__line--title2 {
  width: min(420px, 66vw);
  height: 52px;
  animation-delay: 0.1s;
}
.ld-sk__line--sub {
  width: min(480px, 74vw);
  height: 18px;
  margin-top: 8px;
  animation-delay: 0.2s;
}
.ld-sk__btns {
  display: flex;
  gap: 12px;
  margin-top: 16px;
}
.ld-sk__btn {
  width: 132px;
  height: 46px;
  border-radius: 999px;
  background: #eae3d5;
  animation: ld-sk-pulse 1.4s ease-in-out infinite;
  animation-delay: 0.3s;
}
.ld-sk__btn--primary {
  background: #ddd2bd;
}
.ld-sk__cards {
  max-width: 1080px;
  margin: clamp(48px, 9vh, 88px) auto 0;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}
.ld-sk__card {
  height: 168px;
  border-radius: 20px;
  background: #eae3d5;
  animation: ld-sk-pulse 1.4s ease-in-out infinite;
}
.ld-sk__card:nth-child(2) {
  animation-delay: 0.12s;
}
.ld-sk__card:nth-child(3) {
  animation-delay: 0.24s;
}
@media (max-width: 720px) {
  .ld-sk__cards {
    grid-template-columns: 1fr;
  }
  .ld-sk__card:nth-child(n + 2) {
    display: none;
  }
}
@keyframes ld-sk-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.45;
  }
}
@media (prefers-reduced-motion: reduce) {
  .ld-sk__nav,
  .ld-sk__line,
  .ld-sk__btn,
  .ld-sk__card {
    animation: none;
  }
}
</style>
