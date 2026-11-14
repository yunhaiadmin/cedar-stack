<script setup lang="ts">
/**
 * UhdWatchPill —— 「网页观影」常驻入口胶囊（全站唯一一份实现）
 *
 * 形态真源：用户 2026-11-14 圈图（UHDNow 概览/我的两页右上角同一颗钮）：
 * 彩虹边框 + 玻璃底胶囊，左侧彩色播放三角，文案「网页观影」。
 * 落点 /library：网页播放器没有独立入口页（/play/:id 须带具体条目），
 * 片库首页是网页点播的起点，且与此前概览/我的/邀请/记录四处既有
 * 入口的落点口径一致。
 * 尺寸/填充/玻璃令牌见 styles/uhd-topbar.css（--utb-*），不许在别处
 * 再写第二份观影钮样式（此前全站曾有四份不同写法，本组件即收口）。
 */
import { RouterLink } from 'vue-router'
</script>

<template>
  <RouterLink to="/library" class="utb-pill" aria-label="网页观影：前往片库">
    <svg class="utb-pill__icon" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
      <defs>
        <linearGradient id="utb-pill-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#2f8fe0" />
          <stop offset="0.5" stop-color="#e8766f" />
          <stop offset="1" stop-color="#ecd04f" />
        </linearGradient>
      </defs>
      <path d="M7 4.2v15.6c0 .8.9 1.3 1.6.9l11.6-7.8c.6-.4.6-1.3 0-1.7L8.6 3.3C7.9 2.9 7 3.4 7 4.2Z" fill="url(#utb-pill-g)" />
    </svg>
    <span>网页观影</span>
  </RouterLink>
</template>

<style scoped>
/* 玻璃六要素：blur + saturate（--utb-glass-filter）、半透底（--utb-pill-fill）、
   内高光（inset 0 1px 0 白）、边缘光（彩虹 border-box 描边 + 内亮边）、
   大柔影（--utb-shadow）。彩虹描边用 padding-box 填充 + border-box 描边的
   双背景写法（与全站彩虹钮同配方）。 */
.utb-pill {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  height: var(--utb-pill-h);
  padding: 0 var(--utb-pill-px);
  border: 1.5px solid transparent;
  border-radius: 999px;
  background:
    linear-gradient(var(--utb-pill-fill), var(--utb-pill-fill)) padding-box,
    var(--uhd-rainbow) border-box;
  backdrop-filter: var(--utb-glass-filter);
  -webkit-backdrop-filter: var(--utb-glass-filter);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.35),
    var(--utb-shadow);
  color: var(--utb-pill-fg);
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  font-family: var(--uhd-font-sans);
  text-decoration: none;
  white-space: nowrap;
  flex: none;
  transition: transform var(--uhd-dur-ui, 160ms) var(--uhd-ease-standard, ease);
}
.utb-pill:active {
  transform: scale(0.97);
}
.utb-pill__icon {
  flex: none;
}
</style>
