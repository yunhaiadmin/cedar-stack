<script setup lang="ts">
/**
 * UhdSegmented —— 分段切换（说明书 2.3）
 * 浅灰底容器（--uhd-l-seg-bg）高 44px、圆角 22px；激活项白色胶囊 + 轻阴影 + 黑字。
 * 用例：线路切换、钱包页签、邀请类型、状态筛选。
 */
export interface UhdSegmentOption {
  label: string
  value: string
}

defineProps<{
  options: UhdSegmentOption[]
  modelValue: string
}>()

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
</script>

<template>
  <div class="uhd-seg" role="tablist">
    <button
      v-for="opt in options"
      :key="opt.value"
      type="button"
      role="tab"
      class="uhd-seg__item"
      :class="{ 'uhd-seg__item--active': opt.value === modelValue }"
      :aria-selected="opt.value === modelValue"
      @click="emit('update:modelValue', opt.value)"
    >
      {{ opt.label }}
    </button>
  </div>
</template>

<style scoped>
.uhd-seg {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 44px;
  padding: 4px;
  border-radius: 22px;
  background: var(--uhd-l-seg-bg);
  font-family: var(--uhd-font-sans);
}
.uhd-seg__item {
  height: 36px;
  padding: 0 18px;
  border: none;
  border-radius: var(--uhd-radius-pill);
  background: transparent;
  color: var(--uhd-l-text-2);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  white-space: nowrap;
  cursor: pointer;
  transition:
    background var(--uhd-dur-ui) var(--uhd-ease-standard),
    color var(--uhd-dur-ui) var(--uhd-ease-standard);
}
.uhd-seg__item--active {
  background: var(--uhd-l-surface);
  color: var(--uhd-l-text);
  font-weight: var(--uhd-fw-semibold);
  box-shadow: var(--uhd-shadow-seg);
}
</style>
