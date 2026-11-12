<script setup lang="ts">
/**
 * UhdSidebar —— 左栏框架（说明书 2.10，办事面框架；管理后台复用同模具）
 * 约 250px、白底、右 1px 分隔；分组小标题 13px 灰；菜单项高 44px、圆角 12px、
 * 图标 18px + 文字 15px，激活态 = 白底胶囊 + 边框 + 轻阴影；栏顶右可收起；
 * 底部预留「账号胶囊位」（account 插槽）。无页脚。
 */
import { ref, type Component } from 'vue'

export interface UhdSidebarItem {
  key: string
  label: string
  icon?: Component
}
export interface UhdSidebarGroup {
  title?: string
  items: UhdSidebarItem[]
}

defineProps<{
  groups: UhdSidebarGroup[]
  activeKey: string
  brand?: string
}>()

const emit = defineEmits<{ select: [key: string] }>()
const collapsed = ref(false)
</script>

<template>
  <aside class="uhd-side" :class="{ 'uhd-side--collapsed': collapsed }">
    <div class="uhd-side__head">
      <span class="uhd-side__brand">
        <span class="uhd-side__logo" aria-hidden="true" />
        <span v-if="!collapsed" class="uhd-side__brand-name">{{ brand ?? 'Aetrix' }}</span>
      </span>
      <button type="button" class="uhd-side__collapse" :aria-label="collapsed ? '展开侧栏' : '收起侧栏'" @click="collapsed = !collapsed">
        {{ collapsed ? '›' : '‹' }}
      </button>
    </div>

    <nav class="uhd-side__nav">
      <div v-for="(group, gi) in groups" :key="gi" class="uhd-side__group">
        <div v-if="group.title && !collapsed" class="uhd-side__group-title">{{ group.title }}</div>
        <button
          v-for="item in group.items"
          :key="item.key"
          type="button"
          class="uhd-side__item"
          :class="{ 'uhd-side__item--active': item.key === activeKey }"
          :title="collapsed ? item.label : undefined"
          @click="emit('select', item.key)"
        >
          <component :is="item.icon" v-if="item.icon" class="uhd-side__item-icon" aria-hidden="true" />
          <span v-if="!collapsed" class="uhd-side__item-label">{{ item.label }}</span>
        </button>
      </div>
    </nav>

    <div v-if="$slots.account" class="uhd-side__account">
      <slot name="account" />
    </div>
  </aside>
</template>

<style scoped>
.uhd-side {
  display: flex;
  flex-direction: column;
  width: var(--uhd-sidebar-w);
  flex: none;
  background: var(--uhd-l-surface);
  border-right: var(--uhd-border-w) solid var(--uhd-l-divider);
  font-family: var(--uhd-font-sans);
  transition: width var(--uhd-dur-ui) var(--uhd-ease-standard);
}
.uhd-side--collapsed { width: 68px; }
.uhd-side__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 14px 10px 18px;
}
.uhd-side__brand {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-l-text);
}
/* 彩虹三角 Logo（白名单） */
.uhd-side__logo {
  width: 20px;
  height: 18px;
  flex: none;
  background: var(--uhd-rainbow-conic);
  clip-path: polygon(50% 0, 100% 100%, 0 100%);
}
.uhd-side__collapse {
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--uhd-l-text-3);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}
.uhd-side__collapse:hover { background: var(--uhd-l-input-bg); }
.uhd-side__nav {
  flex: 1;
  overflow-y: auto;
  padding: 4px 12px 12px;
}
.uhd-side__group { margin-bottom: 14px; }
.uhd-side__group-title {
  padding: 8px 10px 6px;
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-l-text-3);
}
.uhd-side__item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 44px;
  padding: 0 10px;
  margin-bottom: 2px;
  border: var(--uhd-border-w) solid transparent;
  border-radius: var(--uhd-radius-navitem);
  background: transparent;
  color: var(--uhd-l-text);
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-medium);
  cursor: pointer;
  text-align: left;
  transition: background var(--uhd-dur-ui) var(--uhd-ease-standard);
}
.uhd-side__item:hover { background: var(--uhd-l-input-bg); }
.uhd-side__item--active {
  background: var(--uhd-l-surface);
  border-color: var(--uhd-l-border);
  box-shadow: var(--uhd-shadow-seg);
  font-weight: var(--uhd-fw-semibold);
}
.uhd-side__item--active:hover { background: var(--uhd-l-surface); }
.uhd-side__item-icon {
  width: 18px;
  height: 18px;
  flex: none;
}
.uhd-side__account {
  padding: 12px;
  border-top: var(--uhd-border-w) solid var(--uhd-l-divider);
}
</style>
