<script setup lang="ts">
/**
 * 404（浅色版，2026-11-13 全页面 1:1 总令收口）——总兑现清单 404 行：
 * 友站是「彩虹光晕浅色整页」；旧版误做深色（背景 --uhd-d-bg），本单改浅：
 * 浅底（--uhd-l-bg）+ 四团粉彩光晕整页 + 深色字 + 约 180px 彩虹渐变 404 数字
 * （白名单位置）+ 双胶囊按钮（返回上一页＝次按钮 / 回到首页＝主按钮，
 * 主钮走招牌口径：彩虹边框＋粉彩底）+「进入用户后台」文字链。
 * 页面独立于应用壳（App.vue 对 not-found 路由名不渲染全局顶栏/底坞）。
 * 逻辑不变：返回上一页（无历史则回首页），主按钮回首页。
 */
import { RouterLink, useRouter } from 'vue-router'
import { ArrowLeft, Home, Compass } from 'lucide-vue-next'
import UhdButton from '@/components/uhd/UhdButton.vue'

const router = useRouter()

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/')
}
</script>

<template>
  <main class="nf-page">
    <div class="nf-blobs" aria-hidden="true" />
    <div class="nf-inner">
      <p class="nf-eyebrow">PAGE NOT FOUND</p>
      <p class="nf-code" aria-hidden="true">404</p>
      <h1 class="nf-title">这一页不在片库里</h1>
      <p class="nf-desc">
        抱歉，您访问的页面不存在或已被移除。<br />
        也许它换了地址，或者从来就没有存在过。
      </p>
      <div class="nf-actions">
        <UhdButton variant="secondary" @click="goBack">
          <ArrowLeft :size="16" aria-hidden="true" />
          <span>返回上一页</span>
        </UhdButton>
        <UhdButton variant="primary" @click="router.push('/')">
          <Home :size="16" aria-hidden="true" />
          <span>回到首页</span>
        </UhdButton>
      </div>
      <RouterLink to="/game/account" class="nf-account-link">
        <Compass :size="15" aria-hidden="true" />
        <span>进入用户后台</span>
      </RouterLink>
    </div>
  </main>
</template>

<style scoped>
.nf-page {
  position: relative;
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 5rem 1rem 3rem;
  background: var(--uhd-l-bg);
  color: var(--uhd-l-text);
  font-family: var(--uhd-font-sans);
  overflow: hidden;
  isolation: isolate;
}

/* 彩虹光晕整页（浅色口径：粉彩光斑全不透明度铺满，与登录页浅色面同手法） */
.nf-blobs {
  position: absolute;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  background:
    radial-gradient(560px 380px at 18% 12%, var(--uhd-blob-lilac), transparent 70%),
    radial-gradient(520px 360px at 85% 20%, var(--uhd-blob-sky), transparent 70%),
    radial-gradient(620px 420px at 70% 88%, var(--uhd-blob-peach), transparent 70%),
    radial-gradient(480px 340px at 12% 85%, var(--uhd-blob-mint), transparent 70%);
  filter: blur(10px);
}

.nf-inner {
  text-align: center;
  animation: uhd-blur-in var(--uhd-dur-blur-in) var(--uhd-ease-pop) both;
}

.nf-eyebrow {
  margin: 0 0 0.75rem;
  font-size: var(--uhd-fs-aux);
  font-weight: var(--uhd-fw-semibold);
  letter-spacing: var(--uhd-ls-eyebrow);
  color: var(--uhd-l-text-2);
}

/* 彩虹渐变数字（白名单位置：404 数字，浅色面同样用全光谱压深色字阶） */
.nf-code {
  margin: 0 0 0.75rem;
  font-family: var(--uhd-font-display);
  font-size: var(--uhd-fs-404);
  font-weight: var(--uhd-fw-black);
  line-height: 1;
  background: var(--uhd-rainbow);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.nf-title {
  margin: 0 0 0.75rem;
  font-size: 36px;
  font-weight: var(--uhd-fw-extrabold);
  color: var(--uhd-l-text);
}

.nf-desc {
  max-width: 28rem;
  margin: 0 auto 2rem;
  font-size: var(--uhd-fs-lead);
  line-height: var(--uhd-lh-body);
  color: var(--uhd-l-text-2);
}

.nf-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 0.75rem;
}

.nf-account-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 1.75rem;
  font-size: var(--uhd-fs-body);
  color: var(--uhd-l-text-2);
  text-decoration: none;
}
.nf-account-link:hover {
  color: var(--uhd-l-text);
}

@media (max-width: 560px) {
  .nf-code { font-size: 120px; }
  .nf-title { font-size: 28px; }
}
@media (prefers-reduced-motion: reduce) {
  .nf-inner { animation: none; }
}
</style>
