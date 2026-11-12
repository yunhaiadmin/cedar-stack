<script setup lang="ts">
/**
 * UhdNavPill —— 悬浮胶囊导航（说明书 2.1，深色营销面/片库面顶栏）
 * 居中悬浮、吸顶；高 46px、圆角 23px；半透明深灰毛玻璃 + 1px 浅边框；
 * Logo 在胶囊外左侧、CTA 在胶囊外右侧；带 children 的项展开毛玻璃下拉卡
 * （约 280px 宽、圆角 16–20px，行内左 icon + 右外链箭头）。
 * 片库面变体：brand + 分类项 + 右侧可放搜索胶囊（search 插槽，替代 CTA）。
 */
import { ref } from 'vue'
import UhdButton from './UhdButton.vue'

export interface UhdNavChild {
  label: string
  desc?: string
}
export interface UhdNavItem {
  key: string
  label: string
  children?: UhdNavChild[]
}

const props = withDefaults(
  defineProps<{
    items: UhdNavItem[]
    activeKey?: string
    brand?: string
    brandSub?: string
    ctaText?: string
  }>(),
  { activeKey: '', brand: 'Aetrix', brandSub: 'SERVICE', ctaText: '' },
)

const emit = defineEmits<{ select: [key: string]; cta: [] }>()

const openKey = ref('')
function onItem(item: UhdNavItem) {
  if (item.children?.length) {
    openKey.value = openKey.value === item.key ? '' : item.key
  } else {
    openKey.value = ''
    emit('select', item.key)
  }
}
</script>

<template>
  <div class="uhd-nav">
    <span class="uhd-nav__brand">
      <span class="uhd-nav__logo" aria-hidden="true" />
      <span class="uhd-nav__brand-text">
        <span class="uhd-nav__brand-name">{{ props.brand }}</span>
        <span class="uhd-nav__brand-sub">{{ props.brandSub }}</span>
      </span>
    </span>

    <nav class="uhd-nav__pill" aria-label="主导航">
      <div v-for="item in items" :key="item.key" class="uhd-nav__cell">
        <button
          type="button"
          class="uhd-nav__item"
          :class="{ 'uhd-nav__item--active': item.key === activeKey }"
          @click="onItem(item)"
        >
          {{ item.label }}
          <span v-if="item.children?.length" class="uhd-nav__caret" aria-hidden="true">▾</span>
        </button>
        <div v-if="item.children?.length && openKey === item.key" class="uhd-nav__dropdown">
          <button
            v-for="child in item.children"
            :key="child.label"
            type="button"
            class="uhd-nav__drop-row"
            @click="openKey = ''"
          >
            <span class="uhd-nav__drop-icon" aria-hidden="true" />
            <span class="uhd-nav__drop-main">
              <span class="uhd-nav__drop-label">{{ child.label }}</span>
              <span v-if="child.desc" class="uhd-nav__drop-desc">{{ child.desc }}</span>
            </span>
            <span class="uhd-nav__drop-arrow" aria-hidden="true">↗</span>
          </button>
        </div>
      </div>
    </nav>

    <span class="uhd-nav__right">
      <slot name="right">
        <UhdButton v-if="ctaText" variant="primary" dark size="sm" @click="emit('cta')">{{ ctaText }}</UhdButton>
      </slot>
    </span>
  </div>
</template>

<style scoped>
.uhd-nav {
  position: relative;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 18px 28px 0;
  font-family: var(--uhd-font-sans);
}
.uhd-nav__brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: var(--uhd-d-text);
  z-index: 2;
}
/* 彩虹三角 Logo（白名单） */
.uhd-nav__logo {
  width: 26px;
  height: 24px;
  background: var(--uhd-rainbow-conic);
  clip-path: polygon(50% 0, 100% 100%, 0 100%);
}
.uhd-nav__brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
}
.uhd-nav__brand-name {
  font-family: var(--uhd-font-display);
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: 0.06em;
}
.uhd-nav__brand-sub {
  font-size: var(--uhd-fs-micro);
  letter-spacing: var(--uhd-ls-eyebrow);
  color: var(--uhd-d-text-2);
}
/* 悬浮胶囊本体 */
.uhd-nav__pill {
  position: absolute;
  left: 50%;
  top: 14px;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  height: var(--uhd-nav-h);
  padding: 0 8px;
  border: var(--uhd-border-w) solid var(--uhd-glass-border);
  border-radius: 23px;
  background: var(--uhd-glass-d);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
  z-index: 3;
}
.uhd-nav__cell { position: relative; }
.uhd-nav__item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 38px;
  padding: 0 16px;
  border: none;
  border-radius: var(--uhd-radius-pill);
  background: transparent;
  color: var(--uhd-d-text-2);
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-medium);
  cursor: pointer;
  transition:
    background var(--uhd-dur-ui) var(--uhd-ease-standard),
    color var(--uhd-dur-ui) var(--uhd-ease-standard);
}
.uhd-nav__item:hover {
  background: rgba(255, 255, 255, 0.08);
  color: var(--uhd-d-text);
}
.uhd-nav__item--active {
  color: var(--uhd-d-text);
  font-weight: var(--uhd-fw-semibold);
}
.uhd-nav__caret { font-size: 10px; }
/* 下拉：深色毛玻璃卡 */
.uhd-nav__dropdown {
  position: absolute;
  top: calc(100% + 12px);
  left: 50%;
  transform: translateX(-50%);
  width: 280px;
  padding: 8px;
  border: var(--uhd-border-w) solid var(--uhd-glass-border);
  border-radius: var(--uhd-radius-card-sm);
  background: var(--uhd-glass-d);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
  animation: uhd-blur-in var(--uhd-dur-blur-in) var(--uhd-ease-pop);
}
.uhd-nav__drop-row {
  display: flex;
  align-items: center;
  gap: 11px;
  width: 100%;
  padding: 10px;
  border: none;
  border-radius: var(--uhd-radius-navitem);
  background: transparent;
  color: var(--uhd-d-text);
  cursor: pointer;
  text-align: left;
}
.uhd-nav__drop-row:hover { background: rgba(255, 255, 255, 0.08); }
.uhd-nav__drop-icon {
  width: 30px;
  height: 30px;
  flex: none;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.09);
}
.uhd-nav__drop-main {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
}
.uhd-nav__drop-label { font-size: var(--uhd-fs-caption); font-weight: var(--uhd-fw-semibold); }
.uhd-nav__drop-desc { font-size: var(--uhd-fs-aux-sm); color: var(--uhd-d-text-2); }
.uhd-nav__drop-arrow { color: var(--uhd-d-text-2); font-size: var(--uhd-fs-aux); }
.uhd-nav__right {
  display: inline-flex;
  align-items: center;
  z-index: 2;
}
@media (max-width: 720px) {
  .uhd-nav__brand-sub { display: none; }
  .uhd-nav__item { padding: 0 10px; font-size: var(--uhd-fs-caption); }
}
</style>
