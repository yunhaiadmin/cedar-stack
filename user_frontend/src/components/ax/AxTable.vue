<script setup lang="ts">
/**
 * AxTable —— 表格（说明书 2.9）
 * 表头 14px 灰、行高 48–64px、1px --ax-l-divider 分隔；无斑马纹、无竖线；
 * 工具条 = 胶囊下拉 + 胶囊搜索输入 + 图标刷新钮（由 toolbar 插槽放入，
 * 插槽内元素加 .ax-tool-pill 类即得胶囊模具）；空态接 AxEmptyState，
 * 加载态 = 旋转圈 + 「正在加载…」灰字。
 */
import AxEmptyState from './AxEmptyState.vue'

export interface AxTableColumn {
  key: string
  label: string
  width?: string
  align?: 'left' | 'center' | 'right'
}

withDefaults(
  defineProps<{
    columns: AxTableColumn[]
    rows: Record<string, unknown>[]
    rowKey?: string
    loading?: boolean
    emptyText?: string
  }>(),
  { rowKey: 'id', loading: false, emptyText: '暂无数据' },
)
</script>

<template>
  <div class="ax-table">
    <div v-if="$slots.toolbar" class="ax-table__toolbar"><slot name="toolbar" /></div>

    <div v-if="loading" class="ax-table__loading">
      <span class="ax-table__spinner" aria-hidden="true" />
      <span>正在加载…</span>
    </div>

    <AxEmptyState v-else-if="!rows.length" :title="emptyText" />

    <table v-else class="ax-table__el">
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
.ax-table {
  font-family: var(--ax-font-sans);
}
.ax-table__toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
/* 工具条胶囊模具（下拉/搜索/按钮） */
.ax-table__toolbar :slotted(.ax-tool-pill) {
  height: 38px;
  padding: 0 14px;
  border: var(--ax-border-w) solid var(--ax-l-border);
  border-radius: var(--ax-radius-pill);
  background: var(--ax-l-surface);
  color: var(--ax-l-text);
  font-size: var(--ax-fs-caption);
  font-family: var(--ax-font-sans);
}
.ax-table__el {
  width: 100%;
  border-collapse: collapse;
}
.ax-table__el th {
  padding: 10px 16px 10px 2px;
  font-size: var(--ax-fs-caption);
  font-weight: var(--ax-fw-medium);
  color: var(--ax-l-text-2);
  border-bottom: var(--ax-border-w) solid var(--ax-l-divider);
  white-space: nowrap;
}
.ax-table__el td {
  height: 56px;
  padding: 8px 16px 8px 2px;
  font-size: var(--ax-fs-body);
  color: var(--ax-l-text);
  border-bottom: var(--ax-border-w) solid var(--ax-l-divider);
}
.ax-table__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 56px 0;
  font-size: var(--ax-fs-caption);
  color: var(--ax-l-text-2);
}
.ax-table__spinner {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 2px solid var(--ax-l-border);
  border-top-color: var(--ax-l-text-2);
  animation: ax-spin 1s linear infinite;
}
</style>
