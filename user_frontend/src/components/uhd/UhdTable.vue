<script setup lang="ts">
/**
 * UhdTable —— 表格（说明书 2.9）
 * 表头 14px 灰、行高 48–64px、1px --uhd-l-divider 分隔；无斑马纹、无竖线；
 * 工具条 = 胶囊下拉 + 胶囊搜索输入 + 图标刷新钮（由 toolbar 插槽放入，
 * 插槽内元素加 .uhd-tool-pill 类即得胶囊模具）；空态接 UhdEmptyState，
 * 加载态 = 旋转圈 + 「正在加载…」灰字。
 */
import UhdEmptyState from './UhdEmptyState.vue'

export interface UhdTableColumn {
  key: string
  label: string
  width?: string
  align?: 'left' | 'center' | 'right'
}

withDefaults(
  defineProps<{
    columns: UhdTableColumn[]
    rows: Record<string, unknown>[]
    rowKey?: string
    loading?: boolean
    emptyText?: string
  }>(),
  { rowKey: 'id', loading: false, emptyText: '暂无数据' },
)
</script>

<template>
  <div class="uhd-table">
    <div v-if="$slots.toolbar" class="uhd-table__toolbar"><slot name="toolbar" /></div>

    <div v-if="loading" class="uhd-table__loading">
      <span class="uhd-table__spinner" aria-hidden="true" />
      <span>正在加载…</span>
    </div>

    <UhdEmptyState v-else-if="!rows.length" :title="emptyText" />

    <table v-else class="uhd-table__el">
      <thead>
        <tr>
          <th
            v-for="col in columns"
            :key="col.key"
            :style="{ width: col.width, textAlign: col.align ?? 'left' }"
          >
            {{ col.label }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, ri) in rows" :key="String(row[rowKey] ?? ri)">
          <td
            v-for="col in columns"
            :key="col.key"
            :style="{ textAlign: col.align ?? 'left' }"
          >
            <slot :name="`cell-${col.key}`" :row="row" :value="row[col.key]">
              {{ row[col.key] ?? '' }}
            </slot>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.uhd-table {
  font-family: var(--uhd-font-sans);
}
.uhd-table__toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
/* 工具条胶囊模具（下拉/搜索/按钮） */
.uhd-table__toolbar :slotted(.uhd-tool-pill) {
  height: 38px;
  padding: 0 14px;
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-pill);
  background: var(--uhd-l-surface);
  color: var(--uhd-l-text);
  font-size: var(--uhd-fs-caption);
  font-family: var(--uhd-font-sans);
}
.uhd-table__el {
  width: 100%;
  border-collapse: collapse;
}
.uhd-table__el th {
  padding: 10px 16px 10px 2px;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  color: var(--uhd-l-text-2);
  border-bottom: var(--uhd-border-w) solid var(--uhd-l-divider);
  white-space: nowrap;
}
.uhd-table__el td {
  height: 56px;
  padding: 8px 16px 8px 2px;
  font-size: var(--uhd-fs-body);
  color: var(--uhd-l-text);
  border-bottom: var(--uhd-border-w) solid var(--uhd-l-divider);
}
.uhd-table__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 56px 0;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
}
.uhd-table__spinner {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 2px solid var(--uhd-l-border);
  border-top-color: var(--uhd-l-text-2);
  animation: uhd-spin 1s linear infinite;
}
</style>
