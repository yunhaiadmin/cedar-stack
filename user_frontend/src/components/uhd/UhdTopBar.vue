<script setup lang="ts">
/**
 * UhdTopBar —— 登录后用户区统一顶栏（全站唯一一份实现）
 *
 * 用户 2026-11-14 亲点「你没固定，没统一」：此前概览/我的各写一份
 * 页头（一处有彩虹边框、一处没有）、其余页面或小字钮或干脆没有，
 * 且全部随页面滚走。本组件收口为一套：
 * - sticky 固定在顶部（占文档流，不遮挡首屏内容），玻璃底；
 * - 左：站标 + 标题（默认站名；「我的」页传 title="我的"，照友站两页口径）；
 * - 右：UhdWatchPill「网页观影」常驻。
 * 挂载点只有三处：App.vue（账户区）、StreamShell（片库面手机端；
 * 桌面走批 3 双胶囊顶栏）、AppHeader 只复用胶囊不复用整条。
 * 令牌见 styles/uhd-topbar.css。
 */
import { computed } from 'vue'
import { branding } from '@/composables/useBranding'
import UhdWatchPill from './UhdWatchPill.vue'

const props = withDefaults(defineProps<{ title?: string }>(), { title: '' })

const barTitle = computed(() => props.title || branding.site_name)
const brandInitial = computed(() => (branding.site_name.trim().charAt(0) || 'A').toUpperCase())
</script>

<template>
  <header class="uhd-topbar">
    <div class="uhd-topbar__brand">
      <img
        v-if="branding.logo_url"
        class="uhd-topbar__logo"
        :src="branding.logo_url"
        :alt="branding.site_name"
      />
      <span v-else class="uhd-topbar__mark" aria-hidden="true">{{ brandInitial }}</span>
      <span class="uhd-topbar__title">{{ barTitle }}</span>
    </div>
    <UhdWatchPill />
  </header>
</template>

<style scoped>
.uhd-topbar {
  position: sticky;
  top: 0;
  z-index: 80;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  height: calc(var(--utb-h) + env(safe-area-inset-top));
  padding: env(safe-area-inset-top) 16px 0;
  box-sizing: border-box;
  /* 玻璃底：半透 + blur/saturate + 底部边缘光 + 顶部内高光 + 柔影 */
  background: var(--utb-bg);
  backdrop-filter: var(--utb-glass-filter);
  -webkit-backdrop-filter: var(--utb-glass-filter);
  border-bottom: 1px solid var(--utb-edge);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.07),
    var(--utb-shadow);
  color: var(--utb-fg);
  font-family: var(--uhd-font-sans);
}
.uhd-topbar__brand {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}
.uhd-topbar__logo {
  width: 24px;
  height: 24px;
  object-fit: contain;
  flex: none;
}
.uhd-topbar__mark {
  font-size: 22px;
  font-weight: var(--uhd-fw-black);
  line-height: 1;
  flex: none;
}
.uhd-topbar__title {
  font-size: 18px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
