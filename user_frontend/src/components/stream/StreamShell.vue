<script setup lang="ts">
/**
 * StreamShell —— 片库面根壳（批 3）
 * 职责：近白/近黑底 + 顶部彩虹光晕、双胶囊顶栏、手机液态玻璃底部导航、
 * 外观三态（跟随系统 → 浅色 → 深色，单键循环，与原站实测一致）。
 * 外观偏好只存片库面自己的键（aetrix-stream-theme），不碰全站 aetrix-theme：
 * 片库面是独立色板 C，不跟办事面/营销面的主题口径互相污染。
 * 深浅只改 .stream-scope 上的 data-stream-theme，令牌见 uhd-tokens.css 色板 C。
 */
import { computed, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import StreamTopbar from './StreamTopbar.vue'
import StreamBottomNav from './StreamBottomNav.vue'
import UhdTopBar from '@/components/uhd/UhdTopBar.vue'

type StreamPref = 'system' | 'light' | 'dark'
const PREF_KEY = 'aetrix-stream-theme'

const pref = ref<StreamPref>('system')
const systemDark = ref(false)
let mq: MediaQueryList | null = null

function readPref(): StreamPref {
  try {
    const v = localStorage.getItem(PREF_KEY)
    return v === 'light' || v === 'dark' || v === 'system' ? v : 'system'
  } catch {
    return 'system'
  }
}

const resolved = computed<'light' | 'dark'>(() => {
  if (pref.value === 'system') return systemDark.value ? 'dark' : 'light'
  return pref.value
})

function cycleTheme() {
  pref.value = pref.value === 'system' ? 'light' : pref.value === 'light' ? 'dark' : 'system'
  try {
    localStorage.setItem(PREF_KEY, pref.value)
  } catch { /* 隐私模式存不下就算了，本次会话仍生效 */ }
}

function onSystemChange(e: MediaQueryListEvent) {
  systemDark.value = e.matches
}

/* Teleport 到 body 的浮层（如 PlayerSchemeSheet）拿不到 .stream-scope 的
   令牌级联，用 body 标记桥接片库面深色态，浮层 CSS 按 body.uhd-stream-dark 取深色值。 */
watch(
  resolved,
  (v) => document.body.classList.toggle('uhd-stream-dark', v === 'dark'),
  { immediate: true },
)

onMounted(() => {
  pref.value = readPref()
  mq = window.matchMedia('(prefers-color-scheme: dark)')
  systemDark.value = mq.matches
  mq.addEventListener('change', onSystemChange)
})
onBeforeUnmount(() => {
  mq?.removeEventListener('change', onSystemChange)
  document.body.classList.remove('uhd-stream-dark')
})

provide('stream-theme', { pref, resolved, cycleTheme })

/* 统一顶栏（2026-11-14）：片库面手机端原先整组无顶栏（D2-01 按原站
   形态隐藏双胶囊），与账户区「网页观影」常驻钮不统一、用户亲点整改。
   手机端改挂全站同一套 UhdTopBar（sticky 玻璃条 + 观影胶囊，样式由
   .stream-scope 作用域令牌重映射，见 uhd-topbar.css）；桌面 ≥768px
   仍走批 3 双胶囊 StreamTopbar，本条由下方 CSS 隐藏。
   详情页（media-detail）有自己的头部（详情页对齐单），不在此挂。 */
const route = useRoute()
const showUnifiedTopbar = computed(() => route.name !== 'media-detail')
</script>

<template>
  <div class="stream-scope" :data-stream-theme="resolved">
    <div class="stream-halo" aria-hidden="true"></div>
    <UhdTopBar v-if="showUnifiedTopbar" class="stream-utb" />
    <StreamTopbar :pref="pref" :resolved="resolved" @cycle-theme="cycleTheme" />
    <main class="stream-main">
      <slot />
    </main>
    <StreamBottomNav />
  </div>
</template>

<style>
/* 片库面全局骨架（非 scoped：.stream-scope 是跨组件的根类，子组件样式各自 scoped） */
.stream-scope {
  position: relative;
  min-height: 100vh;
  min-height: 100dvh;
  background: var(--uhd-s-bg);
  color: var(--uhd-s-text);
  font-family: var(--uhd-font-sans);
  transition: background-color var(--uhd-dur-ui) var(--uhd-ease-standard);
}
/* 顶部柔和彩虹光晕：固定在页面顶部、内容之下（原站实测：极淡粉/绿/蓝辉光） */
.stream-halo {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 560px;
  background: var(--uhd-s-halo);
  pointer-events: none;
  z-index: 0;
}
.stream-main {
  position: relative;
  z-index: 1;
}
/* 手机端给悬浮底部导航让位（安全区 + 10px + 玻璃块高约 76px） */
@media (max-width: 767px) {
  .stream-main {
    padding-bottom: calc(env(safe-area-inset-bottom) + 104px);
  }
  /* 中和全局 mobile.css 的老触控规则（≤768px 时 button/a 一律
     min-height:44px）：片库面按原站源码数值走（数字圆 36、胶囊 34/40、
     页签高度由内容决定），不许被全局规则撑高。仅限片库面作用域。 */
  .stream-scope button,
  .stream-scope a {
    min-height: 0;
  }
  /* D2-01：原站手机版没有顶部胶囊（源码实证＋录屏全程无），
     <768px 只留底部玻璃导航，顶栏双胶囊整组隐藏。
     （带 .stream-scope 前缀提权重：顶栏自身 display:flex 同权重靠后加载会胜出） */
  .stream-scope .stream-topbar {
    display: none;
  }
}
/* 统一顶栏只在手机端出现（桌面走双胶囊）；z-index 压过片库内容，
   sticky 定位在组件内，本处只管断点显隐 */
@media (min-width: 768px) {
  .stream-scope .stream-utb {
    display: none;
  }
}
</style>
