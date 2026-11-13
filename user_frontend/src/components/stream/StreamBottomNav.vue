<script setup lang="ts">
/**
 * StreamBottomNav —— 手机版底部导航（批 3，存档末节源码实证）
 * 只在 <768px 出现；悬浮：左右留 12px、底部 = 安全区 + 10px。
 * 两个液态玻璃块（blur 24px、radius 28）：
 * ①主胶囊 4 页签（首页/电影/剧集/收藏），图标 22px 在上、文字在下，
 *   激活 = 文字转强色 + 图标 strokeWidth 2.2（未激活 1.8）+ 背后白 10% 滑块跟随；
 * ②独立搜索块 56×56，点开全屏搜索层（bg/80 + backdrop-blur-xl，仅手机版）。
 * 页签集合是片库面自己的导航（与全局 PanelTabbar 的五项主导航不是同一套，
 * 片库面路由下 App.vue 不再渲染 PanelTabbar，避免双坞并存）。
 */
import { computed, defineComponent, h, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { Clapperboard, Film, Heart, Search, Tv, X } from 'lucide-vue-next'

const route = useRoute()
const router = useRouter()

/* D2-09：原站首页页签用站标作图标（非房子图形）——这里用 Aetrix 的 A 形标，
   描边风格与 lucide 图标同口径（size/stroke-width 由页签渲染处传）。 */
const AetrixMark = defineComponent({
  props: {
    size: { type: [Number, String], default: 24 },
    strokeWidth: { type: [Number, String], default: 2 },
  },
  setup(props) {
    return () =>
      h(
        'svg',
        {
          width: props.size,
          height: props.size,
          viewBox: '0 0 24 24',
          fill: 'none',
          stroke: 'currentColor',
          'stroke-width': props.strokeWidth,
          'stroke-linecap': 'round',
          'stroke-linejoin': 'round',
          'aria-hidden': 'true',
        },
        [
          h('path', { d: 'M4.5 19.5 12 4l7.5 15.5' }),
          h('path', { d: 'M7.6 14.5h8.8' }),
        ],
      )
  },
})

const TABS = [
  { label: '首页', to: '/library', icon: AetrixMark },
  { label: '电影', to: '/library/movies', icon: Film },
  { label: '剧集', to: '/library/series', icon: Tv },
  { label: '收藏', to: '/favorites', icon: Heart },
] as const

const activeIndex = computed(() => {
  const p = route.path
  if (p === '/library/movies') return 1
  if (p === '/library/series') return 2
  if (p === '/favorites') return 3
  return 0
})

/* 详情/搜索页不属于任何页签时滑块回首页位（与顶栏口径一致） */

/* ---------- 全屏搜索层（仅手机版） ---------- */
const searchOpen = ref(false)
const q = ref('')
function submitSearch() {
  const term = q.value.trim()
  if (!term) return
  searchOpen.value = false
  void router.push({ path: '/search', query: { q: term } })
}
</script>

<template>
  <nav class="sbn" aria-label="片库导航（手机）">
    <div class="sbn-main">
      <span
        class="sbn-slider"
        :style="{ transform: `translateX(${activeIndex * 100}%)` }"
        aria-hidden="true"
      ></span>
      <RouterLink
        v-for="(tab, i) in TABS"
        :key="tab.to"
        :to="tab.to"
        class="sbn-tab"
        :class="{ 'sbn-tab--on': i === activeIndex }"
        :aria-current="i === activeIndex ? 'page' : undefined"
      >
        <component :is="tab.icon" :size="22" :stroke-width="i === activeIndex ? 2.2 : 1.8" />
        <span class="sbn-tab__label">{{ tab.label }}</span>
      </RouterLink>
    </div>
    <div class="sbn-search-block">
      <button type="button" class="sbn-search-btn" aria-label="搜索" @click="searchOpen = true">
        <Search :size="22" />
      </button>
    </div>

    <!-- 全屏搜索层：仅手机版（桌面走顶栏展开搜索） -->
    <div v-if="searchOpen" class="sbn-overlay">
      <div class="sbn-overlay__bar">
        <Search :size="20" aria-hidden="true" />
        <input
          v-model="q"
          class="sbn-overlay__input"
          type="search"
          placeholder="搜索电影、剧集…"
          aria-label="搜索电影、剧集"
          autofocus
          @keydown.enter.prevent="submitSearch"
        />
        <button type="button" class="sbn-overlay__x" aria-label="关闭搜索" @click="searchOpen = false">
          <X :size="20" />
        </button>
      </div>
      <p class="sbn-overlay__hint">输入片名后按回车查看全部结果</p>
      <p class="sbn-overlay__brand"><Clapperboard :size="16" aria-hidden="true" /> 片库搜索</p>
    </div>
  </nav>
</template>

<style scoped>
/* 容器：fixed、左右 12px、底部安全区 + 10px；桌面（≥768px）不渲染 */
.sbn {
  display: none;
}
@media (max-width: 767px) {
  .sbn {
    display: flex;
    align-items: flex-end;
    gap: 10px; /* 两块之间 gap-2.5 ≈ 10px */
    position: fixed;
    left: 12px;
    right: 12px;
    bottom: calc(env(safe-area-inset-bottom) + 10px);
    z-index: 65;
  }
}
/* 液态玻璃块：radius 28、blur 24px */
.sbn-main,
.sbn-search-block {
  border-radius: 28px;
  background: var(--uhd-s-glass);
  border: 1px solid var(--uhd-s-glass-border);
  backdrop-filter: blur(24px) saturate(160%);
  -webkit-backdrop-filter: blur(24px) saturate(160%);
  box-shadow: var(--uhd-s-shadow-pill);
}
.sbn-main {
  position: relative;
  flex: 1;
  display: flex;
  padding: 4px; /* 内 p-1 */
}
/* 激活滑块：宽 25%、白 10%、滑动跟随 */
.sbn-slider {
  position: absolute;
  top: 4px;
  bottom: 4px;
  left: 4px;
  width: calc((100% - 8px) / 4);
  border-radius: 24px;
  background: var(--uhd-s-slider-bg);
  transition: transform 0.28s var(--uhd-ease-pop);
}
.sbn-tab {
  position: relative;
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px; /* gap-0.5 */
  padding: 6px 0; /* py-1.5 */
  color: var(--uhd-s-text-2);
  text-decoration: none;
  font-size: 11px;
  font-weight: var(--uhd-fw-medium);
}
.sbn-tab--on {
  color: var(--uhd-s-text);
}
.sbn-search-block {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  /* 块体 = 56×56 按钮 + 1px 边框一圈 = 58×58，不被容器拉伸 */
  width: 58px;
  height: 58px;
}
.sbn-search-btn {
  width: 56px;
  height: 56px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 28px;
  background: transparent;
  color: var(--uhd-s-text);
  cursor: pointer;
}
/* 全屏搜索层：bg-canvas/80 + backdrop-blur-xl */
.sbn-overlay {
  position: fixed;
  inset: 0;
  z-index: 70;
  padding: calc(env(safe-area-inset-top) + 18px) 16px 16px;
  background: color-mix(in srgb, var(--uhd-s-bg) 80%, transparent);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
}
.sbn-overlay__bar {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 52px;
  padding: 0 8px 0 16px;
  border-radius: 26px;
  background: var(--uhd-s-pill-bg);
  border: 1px solid var(--uhd-s-pill-border);
  color: var(--uhd-s-text);
}
.sbn-overlay__input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--uhd-s-text);
  font: inherit;
  font-size: 16px;
}
.sbn-overlay__x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--uhd-s-text);
  cursor: pointer;
}
.sbn-overlay__hint {
  margin: 16px 4px 0;
  font-size: 13px;
  color: var(--uhd-s-text-2);
}
.sbn-overlay__brand {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 28px 4px 0;
  font-size: 13px;
  color: var(--uhd-s-text-2);
}
@media (prefers-reduced-motion: reduce) {
  .sbn-slider {
    transition: none;
  }
}
</style>
