<script setup lang="ts">
/**
 * AxSidebar —— 左栏框架（说明书 2.10，办事面框架；管理后台复用同模具）
 * 约 250px、白底、右 1px 分隔；分组小标题 13px 灰；菜单项高 44px、圆角 12px、
 * 图标 18px + 文字 15px，激活态 = 白底胶囊 + 边框 + 轻阴影；栏顶右可收起；
 * 底部预留「账号胶囊位」（account 插槽）。无页脚。
 */
import { computed, onMounted, onUnmounted, ref, type Component } from 'vue'
import BrandMark from '@/components/BrandMark.vue'

export interface AxSidebarItem {
  key: string
  label: string
  icon?: Component
  /** 外部链接（站内无对应路由时用，如使用说明→官网文档）：
     点击新开标签页，不走路由、不记激活 */
  href?: string
}
export interface AxSidebarGroup {
  title?: string
  items: AxSidebarItem[]
}

const props = defineProps<{
  groups: AxSidebarGroup[]
  activeKey: string
  brand?: string
  /** 窄屏覆盖层打开时，顶上的钮只负责关闭，不把栏宽收成图标条 */
  dismissible?: boolean
}>()

const emit = defineEmits<{ select: [key: string]; dismiss: [] }>()
const collapsed = ref(false)
/** ≤768 忽略桌面收起偏好：窄屏覆盖层必须带文字，回到桌面再恢复 68px。 */
const isNarrow = ref(typeof window !== 'undefined' && window.matchMedia('(max-width: 768px)').matches)
let narrowMedia: MediaQueryList | null = null
function syncNarrow() {
  isNarrow.value = !!narrowMedia?.matches
}
onMounted(() => {
  narrowMedia = window.matchMedia('(max-width: 768px)')
  syncNarrow()
  narrowMedia.addEventListener('change', syncNarrow)
})
onUnmounted(() => narrowMedia?.removeEventListener('change', syncNarrow))
const labelsVisible = computed(() => isNarrow.value || !collapsed.value)
const collapsedVisual = computed(() => collapsed.value && !isNarrow.value)

function onCollapse() {
  if (props.dismissible) emit('dismiss')
  else collapsed.value = !collapsed.value
}
</script>

<template>
  <aside class="ax-side" :class="{ 'ax-side--collapsed': collapsedVisual }">
    <div class="ax-side__head">
      <span class="ax-side__brand">
        <span class="ax-side__logo"><BrandMark /></span>
        <span v-if="labelsVisible" class="ax-side__brand-name">{{ brand ?? 'Aetrix' }}</span>
      </span>
      <button
        type="button"
        class="ax-side__collapse"
        :aria-label="dismissible ? '关闭导航' : collapsed ? '展开侧栏' : '收起侧栏'"
        @click="onCollapse"
      >
        {{ dismissible ? '×' : collapsed ? '›' : '‹' }}
      </button>
    </div>

    <nav class="ax-side__nav">
      <div v-for="(group, gi) in groups" :key="gi" class="ax-side__group">
        <div v-if="group.title && labelsVisible" class="ax-side__group-title">{{ group.title }}</div>
        <button
          v-for="item in group.items"
          :key="item.key"
          type="button"
          class="ax-side__item"
          :class="{ 'ax-side__item--active': item.key === activeKey }"
          :title="collapsedVisual ? item.label : undefined"
          @click="emit('select', item.key)"
        >
          <component :is="item.icon" v-if="item.icon" class="ax-side__item-icon" aria-hidden="true" />
          <span v-if="labelsVisible" class="ax-side__item-label">{{ item.label }}</span>
        </button>
      </div>
    </nav>

    <div v-if="$slots.account" class="ax-side__account">
      <slot name="account" />
    </div>
  </aside>
</template>

<style scoped>
.ax-side {
  display: flex;
  flex-direction: column;
  width: var(--ax-sidebar-w);
  flex: none;
  background: var(--ax-l-surface);
  border-right: var(--ax-border-w) solid var(--ax-l-divider);
  font-family: var(--ax-font-sans);
  transition: width var(--ax-dur-ui) var(--ax-ease-standard);
}
.ax-side--collapsed { width: 68px; }
.ax-side__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 14px 10px 18px;
}
.ax-side__brand {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  font-size: var(--ax-fs-lead);
  font-weight: var(--ax-fw-bold);
  color: var(--ax-l-text);
}
/* Aetrix 品牌标（2026-11-16 定版，BrandMark 组件，A 色随当前文字色） */
.ax-side__logo {
  width: 20px;
  height: 20px;
  flex: none;
}
.ax-side__collapse {
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--ax-l-text-3);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}
.ax-side__collapse:hover { background: var(--ax-l-input-bg); }
.ax-side__nav {
  flex: 1;
  overflow-y: auto;
  padding: 4px 12px 12px;
}
.ax-side__group { margin-bottom: 14px; }
.ax-side__group-title {
  padding: 8px 10px 6px;
  font-size: var(--ax-fs-aux);
  color: var(--ax-l-text-3);
}
.ax-side__item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 44px;
  padding: 0 10px;
  margin-bottom: 2px;
  border: var(--ax-border-w) solid transparent;
  border-radius: var(--ax-radius-navitem);
  background: transparent;
  color: var(--ax-l-text);
  font-size: var(--ax-fs-body);
  font-weight: var(--ax-fw-medium);
  cursor: pointer;
  text-align: left;
  transition: background var(--ax-dur-ui) var(--ax-ease-standard);
}
.ax-side__item:hover { background: var(--ax-l-input-bg); }
.ax-side__item--active {
  background: var(--ax-l-surface);
  border-color: var(--ax-l-border);
  box-shadow: var(--ax-shadow-seg);
  font-weight: var(--ax-fw-semibold);
}
.ax-side__item--active:hover { background: var(--ax-l-surface); }
.ax-side__item-icon {
  width: 18px;
  height: 18px;
  flex: none;
}
.ax-side__account {
  padding: 12px;
  border-top: var(--ax-border-w) solid var(--ax-l-divider);
}
</style>
