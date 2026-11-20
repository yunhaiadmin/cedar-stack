<script setup lang="ts">
/**
 * AxSegmented —— 分段切换（说明书 2.3）
 * 浅灰底容器（--ax-l-seg-bg）高 44px、圆角 22px；激活项白色胶囊 + 轻阴影 + 黑字。
 * 用例：线路切换、钱包页签、邀请类型、状态筛选。
 */
export interface AxSegmentOption {
  label: string
  value: string
}

defineProps<{
  options: AxSegmentOption[]
  modelValue: string
}>()

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
</script>

<template>
  <div class="ax-seg" role="tablist">
    <button
      v-for="opt in options"
      :key="opt.value"
      type="button"
      role="tab"
      class="ax-seg__item"
      :class="{ 'ax-seg__item--active': opt.value === modelValue }"
      :aria-selected="opt.value === modelValue"
      @click="emit('update:modelValue', opt.value)"
    >
      {{ opt.label }}
    </button>
  </div>
</template>

<style scoped>
.ax-seg {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 44px;
  padding: 4px;
  border-radius: 22px;
  background: var(--ax-l-seg-bg);
  font-family: var(--ax-font-sans);
}
.ax-seg__item {
  height: 36px;
  padding: 0 18px;
  border: none;
  border-radius: var(--ax-radius-pill);
  background: transparent;
  color: var(--ax-l-text-2);
  font-size: var(--ax-fs-caption);
  font-weight: var(--ax-fw-medium);
  white-space: nowrap;
  cursor: pointer;
  transition:
    background var(--ax-dur-ui) var(--ax-ease-standard),
    color var(--ax-dur-ui) var(--ax-ease-standard);
}
.ax-seg__item--active {
  background: var(--ax-l-surface);
  color: var(--ax-l-text);
  font-weight: var(--ax-fw-semibold);
  box-shadow: var(--ax-shadow-seg);
}
</style>
