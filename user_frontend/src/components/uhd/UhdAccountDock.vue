<script setup lang="ts">
/**
 * UhdAccountDock —— 用户中心底部导航（手机，UHD app 形态）
 *
 * 只在 ≤768px 且用户中心区域（/game/* 与 /shop）出现：单个居中悬浮的
 * 液态玻璃胶囊（blur 24px、radius 28），内含两个页签——
 *   概览（/game/account，账户概览页）
 *   我的（/game/account/menu，全部功能菜单列表页）
 * 选中页签为深底胶囊填充＋白字，未选灰字。与片库区底部导航
 * （StreamBottomNav，4 页签＋搜索块）是两套，按路由区域切换显示：
 * App.vue 在用户中心区域渲染本组件、不再渲染 PanelTabbar；
 * 片库区域的切换由 App.vue 的 isStreamArea 一侧负责，两者不并存。
 * 桌面 ≥769px 不渲染（用户中心走左栏框架）。
 */
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { Home, User } from 'lucide-vue-next'

const route = useRoute()

const TABS = [
  { label: '概览', to: '/game/account', icon: Home },
  { label: '我的', to: '/game/account/menu', icon: User },
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
        <component :is="tab.icon" :size="19" :stroke-width="activeTo === tab.to ? 2.2 : 1.8" aria-hidden="true" />
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
/* 液态玻璃胶囊：radius 28、blur 24px（与片库底坞同语言） */
.uad-pill {
  pointer-events: auto;
  display: flex;
  gap: 2px;
  padding: 4px;
  border-radius: 28px;
  background: var(--uhd-glass-d);
  border: 1px solid var(--uhd-glass-border);
  backdrop-filter: blur(24px) saturate(160%);
  -webkit-backdrop-filter: blur(24px) saturate(160%);
  box-shadow: 0 12px 32px -12px rgba(0, 0, 0, 0.45);
}
.uad-tab {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-width: 104px;
  padding: 9px 20px;
  border-radius: 24px;
  color: var(--uhd-d-text-2);
  text-decoration: none;
  font-family: var(--uhd-font-sans);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  transition: background var(--uhd-dur-ui) ease, color var(--uhd-dur-ui) ease;
}
.uad-tab--on {
  background: var(--uhd-d-surface-raised);
  color: var(--uhd-d-text);
  font-weight: var(--uhd-fw-semibold);
}
</style>
