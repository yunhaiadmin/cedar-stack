<script setup lang="ts">
/**
 * UhdAccountShellGate —— 桌面并壳闸门（2026-11-14 桌面用户中心统一壳单）
 *
 * 背景：概览/订阅服务/订阅记录/邀请/团队/安装原本是手机形态的整页
 * （自带 100dvh 底色与返回顶栏），桌面宽屏上无侧栏壳，点进去像换了
 * 一个后台、还回不去。本闸门只负责一件事：
 * - ≥769px（桌面，与 UhdAccountLayout 侧栏显隐同断点）：把页面内容
 *   装进 UhdAccountLayout，用户中心全域同壳同侧栏；
 * - ≤768px（手机）：原样透传，页面保持现行 app 形态一字不动
 *   （底坞＋单列＋吸底条等都不受影响）。
 *
 * 用法：页面根模板包一层即可，页面自身不感知壳的存在：
 *   <UhdAccountShellGate><div class="xx-page">…</div></UhdAccountShellGate>
 * 壳内贴合（扣高度、隐藏壳内冗余的移动返回顶栏、结算条让开侧栏）
 * 由 UhdAccountLayout 的全局样式按页根类名统一处理。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import UhdAccountLayout from './UhdAccountLayout.vue'

const DESKTOP_QUERY = '(min-width: 769px)'

const isDesktop = ref(
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia(DESKTOP_QUERY).matches
    : true,
)

let mql: MediaQueryList | null = null
function onChange(e: MediaQueryListEvent) {
  isDesktop.value = e.matches
}

onMounted(() => {
  mql = window.matchMedia(DESKTOP_QUERY)
  isDesktop.value = mql.matches
  mql.addEventListener('change', onChange)
})
onBeforeUnmount(() => {
  mql?.removeEventListener('change', onChange)
})
</script>

<template>
  <UhdAccountLayout v-if="isDesktop">
    <slot />
  </UhdAccountLayout>
  <slot v-else />
</template>
