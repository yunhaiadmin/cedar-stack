<script setup lang="ts">
import { useId } from 'vue'

/**
 * Aetrix 品牌标（2026-11-16 定版）：扁平粗体 A（平顶、无横杠）＋彩虹播放三角编织穿过 A——
 * 三角藏在左腿之后、从凹口透出、跨过右腿内侧、尖端止于右腿外缘。
 * A 的颜色走 currentColor（由使用处表面决定明暗），三角为固定彩虹渐变。
 * 矢量母版同源文件：public/favicon.svg、public/icons/logo-mark-light.svg、public/icons/logo-mark-dark.svg。
 * svg 类名避开页面级的 .brand-mark 容器类（管理端 Layout/Login/Setup 用它给容器染色，会经子组件根节点 scope 命中 svg）。
 * 渐变/clip 的 id 必须每实例唯一：同页多个本组件时 url(#id) 只认文档中第一个，
 * 若第一个恰在 display:none 子树里（如响应式隐藏的导航），其余实例的填充会失效。
 *
 * variant="play"：只渲染彩虹播放三角本体（无 A 字母、无编织 clip，viewBox 收紧
 * 到三角自身 bbox），供会员卡右上用——友站同位只有一颗小三角标（2026-11-18
 * 按友站干净帧实测改版，量值见 UhdMemberCard 的 .uhd-member__logo 注）。
 */
withDefaults(defineProps<{ variant?: 'full' | 'play' }>(), { variant: 'full' })
const uid = useId().replace(/[^a-zA-Z0-9-]/g, '')
const gradId = `brandmark-rainbow-${uid}`
const clipId = `brandmark-weave-${uid}`
</script>

<template>
  <svg
    class="aetrix-brand-mark"
    :viewBox="variant === 'play' ? '280 400 431 390' : '180 188 640 625'"
    fill="none"
    aria-hidden="true"
    focusable="false"
  >
    <defs>
      <linearGradient :id="gradId" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="#ff2442" />
        <stop offset="0.25" stop-color="#ff8a1e" />
        <stop offset="0.45" stop-color="#ffcf00" />
        <stop offset="0.62" stop-color="#2ecc40" />
        <stop offset="0.8" stop-color="#2478ff" />
        <stop offset="1" stop-color="#8a2be2" />
      </linearGradient>
      <clipPath :id="clipId">
        <polygon points="628,0 1000,0 1000,1000 218,1000" />
      </clipPath>
    </defs>
    <path v-if="variant === 'play'" d="M420 400 L280 790 L711 546 Z" :fill="`url(#${gradId})`" />
    <template v-else>
      <path d="M438 188 H562 L820 813 H708 L500 313 L295 813 H180 Z" fill="currentColor" />
      <g :clip-path="`url(#${clipId})`">
        <path d="M420 400 L280 790 L711 546 Z" :fill="`url(#${gradId})`" />
      </g>
    </template>
  </svg>
</template>

<style scoped>
.aetrix-brand-mark {
  display: block;
  width: 100%;
  height: 100%;
}
</style>
