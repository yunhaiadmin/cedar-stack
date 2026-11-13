<script setup lang="ts">
/**
 * UhdAccountDock —— 用户中心底部导航（手机，UHD app 形态）
 *
 * 只在 ≤768px 且用户中心区域（/game/* 与 /shop）出现：单个居中悬浮的
 * 液态玻璃胶囊，内含两个页签（图标在上、文字在下）——
 *   概览（/game/account，账户概览页）
 *   我的（/game/account/menu，全部功能菜单列表页）
 * 玻璃配方（2026-11-13 1:1 返工，用户点名）：半透底＋blur(24px)
 * saturate(180%)＋1px 边缘光＋inset 顶部高光＋大柔影，浅/深各一份，
 * 跟随用户外观（html[data-theme='light'] 为浅色，缺省深色）。
 * 激活页签为整段胶囊浅填充，不用实心块。与片库区底部导航
 * （StreamBottomNav）是两套，按路由区域切换显示（见 App.vue）。
 * 桌面 ≥769px 不渲染（用户中心走左栏框架）。
 */
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { Home, CircleUserRound } from 'lucide-vue-next'

const route = useRoute()

const TABS = [
  { label: '概览', to: '/game/account', icon: Home },
  { label: '我的', to: '/game/account/menu', icon: CircleUserRound },
] as const

/** 概览只认概览页本身；用户中心其余页面（含菜单页）都归「我的」侧 */
const activeTo = computed(() => (route.path === '/game/account' ? '/game/account' : '/game/account/menu'))
</script>

<template>
  <nav class="uad" aria-label="用户中心导航（手机）">
    <div class="uad-pill">
      <RouterLink
        v-for="tab in TABS"
        :key="tab.to"
        :to="tab.to"
        class="uad-tab"
        :class="{ 'uad-tab--on': activeTo === tab.to }"
        :aria-current="activeTo === tab.to ? 'page' : undefined"
      >
        <component :is="tab.icon" :size="23" :stroke-width="activeTo === tab.to ? 2.2 : 1.8" aria-hidden="true" />
        <span class="uad-tab__label">{{ tab.label }}</span>
      </RouterLink>
    </div>
  </nav>
</template>

<style scoped>
/* 容器：fixed 居中悬浮、底部 = 安全区 + 10px；桌面不渲染 */
.uad {
  display: none;
}
@media (max-width: 768px) {
  .uad {
    display: flex;
    justify-content: center;
    position: fixed;
    left: 12px;
    right: 12px;
    bottom: calc(env(safe-area-inset-bottom) + 10px);
    z-index: 65;
    pointer-events: none;
  }
}
/* 液态玻璃胶囊（深色为缺省）：半透底＋强模糊饱和＋边缘光＋顶部高光＋大柔影 */
.uad-pill {
  pointer-events: auto;
  display: flex;
  gap: 2px;
  padding: 5px;
  border-radius: 33px;
  background: rgba(30, 30, 33, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.16);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.12),
    0 16px 40px rgba(0, 0, 0, 0.5);
}
/* 浅色玻璃：跟随用户外观（data-theme='light'） */
html[data-theme='light'] .uad-pill {
  background: rgba(255, 255, 255, 0.6);
  border-color: rgba(255, 255, 255, 0.65);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.55),
    0 16px 40px rgba(0, 0, 0, 0.14);
}
.uad-tab {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  min-width: 96px;
  padding: 7px 22px;
  border-radius: 28px;
  color: rgba(255, 255, 255, 0.55);
  text-decoration: none;
  font-family: var(--uhd-font-sans);
  transition: background 0.25s ease, color 0.25s ease, transform 0.25s ease;
}
html[data-theme='light'] .uad-tab {
  color: rgba(17, 17, 17, 0.55);
}
.uad-tab__label {
  font-size: 12px;
  font-weight: var(--uhd-fw-medium);
  line-height: 1.15;
}
/* 激活段：整段胶囊浅填充（原站口径），不用实心块 */
.uad-tab--on {
  background: rgba(255, 255, 255, 0.13);
  color: #ffffff;
}
.uad-tab--on .uad-tab__label {
  font-weight: var(--uhd-fw-semibold);
}
html[data-theme='light'] .uad-tab--on {
  background: rgba(0, 0, 0, 0.07);
  color: #111111;
}
</style>
