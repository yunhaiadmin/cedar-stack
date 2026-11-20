<script setup lang="ts">
/**
 * AxNavPill —— 悬浮胶囊导航（说明书 2.1，深色营销面/片库面顶栏）
 * 居中悬浮、吸顶；高 46px、圆角 23px；半透明深灰毛玻璃 + 1px 浅边框；
 * Logo 在胶囊外左侧、CTA 在胶囊外右侧；带 children 的项展开毛玻璃下拉卡
 * （约 280px 宽、圆角 16–20px，行内左 icon + 右外链箭头）。
 * 片库面变体：brand + 分类项 + 右侧可放搜索胶囊（search 插槽，替代 CTA）。
 */
import { onMounted, onUnmounted, ref } from 'vue'
import AxButton from './AxButton.vue'
import BrandMark from '@/components/BrandMark.vue'

export interface AxNavChild {
  label: string
  desc?: string
}
export interface AxNavItem {
  key: string
  label: string
  children?: AxNavChild[]
}

const props = withDefaults(
  defineProps<{
    items: AxNavItem[]
    activeKey?: string
    brand?: string
    brandSub?: string
    ctaText?: string
    /** 跟随用户主题：浅色主题下整条转浅色玻璃（定价页用）；缺省锁深色玻璃（落地页/片库面） */
    themeFollow?: boolean
    /** 滚动收窄（D1-11，原站实测）：开启后整条容器 max-width 1360，滚动 >40px 收窄到 760（.5s ease-sheet）；缺省关，现有消费者零影响 */
    shrinkOnScroll?: boolean
    /** 品牌标彩虹旋转（友站 prism-icon 6s 口径）：缺省关，仅落地页首屏开启，透传给 BrandMark 的 spin */
    brandSpin?: boolean
  }>(),
  { activeKey: '', brand: 'Aetrix', brandSub: 'SERVICE', ctaText: '', themeFollow: false, shrinkOnScroll: false, brandSpin: false },
)

const emit = defineEmits<{ select: [key: string]; cta: [] }>()

const scrolled = ref(false)
function onScrollShrink() {
  scrolled.value = window.scrollY > 40
}
onMounted(() => {
  window.addEventListener('keydown', onNavKey)
  if (!props.shrinkOnScroll) return
  window.addEventListener('scroll', onScrollShrink, { passive: true })
  onScrollShrink()
})
onUnmounted(() => {
  window.removeEventListener('keydown', onNavKey)
  window.removeEventListener('scroll', onScrollShrink)
})

const openKey = ref('')
function onItem(item: AxNavItem) {
  if (item.children?.length) {
    openKey.value = openKey.value === item.key ? '' : item.key
  } else {
    openKey.value = ''
    emit('select', item.key)
  }
}
function onNavKey(event: KeyboardEvent) {
  if (event.key === 'Escape' && openKey.value) {
    event.preventDefault()
    openKey.value = ''
  }
}
</script>

<template>
  <div
    class="ax-nav"
    :class="{
      'ax-nav--follow': props.themeFollow,
      'ax-nav--shrink': props.shrinkOnScroll,
      'is-shrunk': props.shrinkOnScroll && scrolled,
    }"
  >
    <span class="ax-nav__brand">
      <span class="ax-nav__logo"><BrandMark :spin="props.brandSpin" /></span>
      <span class="ax-nav__brand-text">
        <span class="ax-nav__brand-name">{{ props.brand }}</span>
        <span class="ax-nav__brand-sub">{{ props.brandSub }}</span>
      </span>
    </span>

    <nav class="ax-nav__pill" aria-label="主导航">
      <div v-for="item in items" :key="item.key" class="ax-nav__cell">
        <button
          type="button"
          class="ax-nav__item"
          :class="{ 'ax-nav__item--active': item.key === activeKey }"
          :aria-expanded="item.children?.length ? openKey === item.key : undefined"
          @click="onItem(item)"
        >
          {{ item.label }}
          <span v-if="item.children?.length" class="ax-nav__caret" aria-hidden="true">▾</span>
        </button>
        <div v-if="item.children?.length && openKey === item.key" class="ax-nav__dropdown">
          <button
            v-for="child in item.children"
            :key="child.label"
            type="button"
            class="ax-nav__drop-row"
            @click="openKey = ''"
          >
            <span class="ax-nav__drop-icon" aria-hidden="true" />
            <span class="ax-nav__drop-main">
              <span class="ax-nav__drop-label">{{ child.label }}</span>
              <span v-if="child.desc" class="ax-nav__drop-desc">{{ child.desc }}</span>
            </span>
            <span class="ax-nav__drop-arrow" aria-hidden="true">↗</span>
          </button>
        </div>
      </div>
    </nav>

    <span class="ax-nav__right">
      <slot name="right">
        <AxButton v-if="ctaText" variant="primary" dark size="sm" @click="emit('cta')">{{ ctaText }}</AxButton>
      </slot>
    </span>
  </div>
</template>

<style scoped>
.ax-nav {
  position: relative;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 18px 28px 0;
  font-family: var(--ax-font-sans);
}
.ax-nav__brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: var(--ax-d-text);
  z-index: 2;
}
/* Aetrix 品牌标（2026-11-16 定版，BrandMark 组件，A 色随当前文字色） */
.ax-nav__logo {
  width: 26px;
  height: 25px;
  flex: none;
}
.ax-nav__brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
}
.ax-nav__brand-name {
  font-family: var(--ax-font-display);
  font-size: var(--ax-fs-lead);
  font-weight: var(--ax-fw-extrabold);
  letter-spacing: 0.06em;
}
.ax-nav__brand-sub {
  font-size: var(--ax-fs-micro);
  letter-spacing: var(--ax-ls-eyebrow);
  color: var(--ax-d-text-2);
}
/* 悬浮胶囊本体 */
.ax-nav__pill {
  position: absolute;
  left: 50%;
  top: 14px;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  height: var(--ax-nav-h);
  padding: 0 8px;
  border: var(--ax-border-w) solid var(--ax-glass-border);
  border-radius: 23px;
  background: var(--ax-glass-d);
  backdrop-filter: var(--ax-glass-filter);
  -webkit-backdrop-filter: var(--ax-glass-filter);
  z-index: 3;
}
.ax-nav__cell { position: relative; }
.ax-nav__item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 38px;
  padding: 0 16px;
  border: none;
  border-radius: var(--ax-radius-pill);
  background: transparent;
  color: var(--ax-d-text-2);
  font-size: var(--ax-fs-body);
  font-weight: var(--ax-fw-medium);
  cursor: pointer;
  transition:
    background var(--ax-dur-ui) var(--ax-ease-standard),
    color var(--ax-dur-ui) var(--ax-ease-standard);
}
.ax-nav__item:hover {
  background: rgba(255, 255, 255, 0.08);
  color: var(--ax-d-text);
}
.ax-nav__item--active {
  color: var(--ax-d-text);
  font-weight: var(--ax-fw-semibold);
}
.ax-nav__item:focus-visible,
.ax-nav__drop-row:focus-visible {
  outline: 2px solid var(--ax-d-text);
  outline-offset: 2px;
}
.ax-nav__caret { font-size: 10px; }
/* 下拉：深色毛玻璃卡 */
.ax-nav__dropdown {
  position: absolute;
  top: calc(100% + 12px);
  left: 50%;
  transform: translateX(-50%);
  width: 280px;
  padding: 8px;
  border: var(--ax-border-w) solid var(--ax-glass-border);
  /* 质感第二波（2026-11-18）：圆角收 20（友站实测下拉圆角约 20） */
  border-radius: 20px;
  background: var(--ax-glass-d);
  backdrop-filter: var(--ax-glass-filter);
  -webkit-backdrop-filter: var(--ax-glass-filter);
  animation: ax-blur-in var(--ax-dur-blur-in) var(--ax-ease-pop);
}
.ax-nav__drop-row {
  display: flex;
  align-items: center;
  gap: 11px;
  width: 100%;
  padding: 10px;
  border: none;
  border-radius: var(--ax-radius-navitem);
  background: transparent;
  color: var(--ax-d-text);
  cursor: pointer;
  text-align: left;
}
.ax-nav__drop-row:hover { background: rgba(255, 255, 255, 0.08); }
.ax-nav__drop-icon {
  /* 质感第二波（2026-11-18）：行图标块收 36（友站实测约 36，行高随之约 56） */
  width: 36px;
  height: 36px;
  flex: none;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.09);
}
.ax-nav__drop-main {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
}
.ax-nav__drop-label { font-size: var(--ax-fs-caption); font-weight: var(--ax-fw-semibold); }
.ax-nav__drop-desc { font-size: var(--ax-fs-aux-sm); color: var(--ax-d-text-2); }
.ax-nav__drop-arrow { color: var(--ax-d-text-2); font-size: var(--ax-fs-aux); }
.ax-nav__right {
  display: inline-flex;
  align-items: center;
  z-index: 2;
}
/* 滚动收窄（D1-11，仅 shrinkOnScroll 启用时）：容器居中限宽，滚动后 1360→760 */
.ax-nav--shrink {
  max-width: 1360px;
  margin-left: auto;
  margin-right: auto;
  transition: max-width 0.5s cubic-bezier(0.32, 0.72, 0, 1);
}
/* 滚过首屏后收成一条胶囊：字标、导航、主钮同处一条玻璃条里（顶区仍是字标在外、胶囊居中）。 */
.ax-nav--shrink.is-shrunk {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 4px;
  width: fit-content;
  max-width: calc(100vw - 32px);
  margin-top: 14px;
  padding: 6px 8px 6px 16px;
  border: var(--ax-border-w) solid var(--ax-glass-border);
  border-radius: 999px;
  background: rgba(10, 10, 14, 0.72);
  backdrop-filter: var(--ax-glass-filter);
  -webkit-backdrop-filter: var(--ax-glass-filter);
}
.ax-nav--shrink.is-shrunk .ax-nav__pill {
  position: static;
  transform: none;
  height: auto;
  flex: 1 1 auto;
  min-width: 0;
  padding: 0;
  border: none;
  background: transparent;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
.ax-nav--shrink.is-shrunk .ax-nav__item {
  height: 36px;
  padding: 0 12px;
}
.ax-nav--shrink.is-shrunk .ax-nav__brand {
  flex: none;
  margin-right: 6px;
}
@media (max-width: 720px) {
  .ax-nav__brand-sub { display: none; }
  .ax-nav__item { padding: 0 10px; font-size: var(--ax-fs-caption); }
}

/* 跟随主题的浅色形态（themeFollow 时，浅色主题下生效；CTA 主按钮保持深底，
   与原站浅色主题下主按钮口径一致） */
:root[data-theme='light'] .ax-nav--follow .ax-nav__brand { color: var(--ax-l-text); }
:root[data-theme='light'] .ax-nav--follow .ax-nav__brand-sub { color: var(--ax-l-text-2); }
:root[data-theme='light'] .ax-nav--follow .ax-nav__pill {
  background: var(--ax-glass-l);
  border-color: var(--ax-glass-border-l);
}
:root[data-theme='light'] .ax-nav--follow .ax-nav__item { color: var(--ax-l-text-2); }
:root[data-theme='light'] .ax-nav--follow .ax-nav__item:hover {
  background: rgba(17, 17, 17, 0.06);
  color: var(--ax-l-text);
}
:root[data-theme='light'] .ax-nav--follow .ax-nav__item--active { color: var(--ax-l-text); }
:root[data-theme='light'] .ax-nav--follow .ax-nav__dropdown {
  background: var(--ax-glass-l);
  border-color: var(--ax-glass-border-l);
}
:root[data-theme='light'] .ax-nav--follow .ax-nav__drop-row { color: var(--ax-l-text); }
:root[data-theme='light'] .ax-nav--follow .ax-nav__drop-row:hover { background: rgba(17, 17, 17, 0.06); }
:root[data-theme='light'] .ax-nav--follow .ax-nav__drop-icon { background: rgba(17, 17, 17, 0.07); }
:root[data-theme='light'] .ax-nav--follow .ax-nav__drop-desc,
:root[data-theme='light'] .ax-nav--follow .ax-nav__drop-arrow { color: var(--ax-l-text-2); }
</style>
