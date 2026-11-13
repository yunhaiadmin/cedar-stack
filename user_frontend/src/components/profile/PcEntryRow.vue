<template>
  <RouterLink v-if="to" :to="to" class="pc-row">
    <span class="pc-row-ic" aria-hidden="true"><component :is="icon" :size="22" /></span>
    <span class="pc-row-name">{{ name }}</span>
    <span v-if="meta" class="pc-row-meta">{{ meta }}</span>
    <ChevronRight :size="18" class="pc-chev" aria-hidden="true" />
  </RouterLink>
  <button v-else type="button" class="pc-row" :class="{ danger }" @click="emit('click')">
    <span class="pc-row-ic" aria-hidden="true"><component :is="icon" :size="22" /></span>
    <span class="pc-row-name">{{ name }}</span>
    <span v-if="meta" class="pc-row-meta">{{ meta }}</span>
    <ChevronDown
      v-if="expandable"
      :size="18"
      class="pc-chev pc-chev-down"
      :class="{ open: expanded }"
      aria-hidden="true"
    />
    <ChevronRight v-else :size="18" class="pc-chev" aria-hidden="true" />
  </button>
</template>

<script setup lang="ts">
/**
 * 个人中心 / 账号设置共用的入口行（说明书 2.6 状态行模具）：
 * 左图标 22px + 名称 16px/600 + 右侧状态小字（14px 灰）+ 箭头，
 * 行高 68px，行间分隔由父级分组的 1px --uhd-l-divider 承担。
 * to 有值渲染 RouterLink（整行跳转），否则渲染 button（点击/展开由父级处理）。
 * meta 只传现有接口拿得到的真实摘要，拿不到就别传（不编造）。
 */
import type { Component } from 'vue'
import { RouterLink } from 'vue-router'
import { ChevronRight, ChevronDown } from 'lucide-vue-next'

defineProps<{
  icon: Component
  name: string
  meta?: string
  to?: string | { path: string; hash?: string }
  danger?: boolean
  expandable?: boolean
  expanded?: boolean
}>()

const emit = defineEmits<{ (e: 'click'): void }>()
</script>

<style scoped>
.pc-row {
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
  min-height: 68px;
  padding: 10px 2px;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--uhd-l-text);
  font: inherit;
  font-family: var(--uhd-font-sans);
  text-align: left;
  text-decoration: none;
  box-sizing: border-box;
}
.pc-row:active { background: var(--uhd-l-input-bg); }
.pc-row-ic {
  width: 22px;
  height: 22px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--uhd-l-text);
}
.pc-row-name { font-size: var(--uhd-fs-lead); font-weight: var(--uhd-fw-semibold); white-space: nowrap; }
.pc-row-meta {
  margin-left: auto;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-variant-numeric: tabular-nums;
}
.pc-chev { margin-left: auto; color: var(--uhd-l-text-3); flex: none; }
.pc-row:has(.pc-row-meta) .pc-chev { margin-left: 0; }
.pc-chev-down { transition: transform var(--uhd-dur-ui) var(--uhd-ease-standard); }
.pc-chev-down.open { transform: rotate(180deg); }
.pc-row.danger .pc-row-name { color: var(--uhd-danger); }
.pc-row.danger .pc-row-ic { color: var(--uhd-danger); }
</style>
