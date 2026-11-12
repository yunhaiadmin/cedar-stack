<script setup lang="ts">
/**
 * UhdModal —— 弹窗（说明书 2.8）
 * 居中白卡约 400px 宽、圆角 24px、内边距 32px、标题 24px、右上 × 关闭；
 * 遮罩黑 40% + 背景模糊；入场淡入 + 模糊→清晰。
 * 公告、线路、确认类弹窗同模具。
 */
import { onBeforeUnmount, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    title?: string
    width?: number
  }>(),
  { title: '', width: 400 },
)

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

function close() {
  emit('update:modelValue', false)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') close()
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) document.addEventListener('keydown', onKeydown)
    else document.removeEventListener('keydown', onKeydown)
  },
)
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="uhd-modal__overlay" @click.self="close">
      <div class="uhd-modal" :style="{ width: `min(${width}px, calc(100vw - 32px))` }" role="dialog" aria-modal="true">
        <div v-if="title" class="uhd-modal__head">
          <h3 class="uhd-modal__title">{{ title }}</h3>
          <button type="button" class="uhd-modal__close" aria-label="关闭" @click="close">×</button>
        </div>
        <button v-else type="button" class="uhd-modal__close uhd-modal__close--float" aria-label="关闭" @click="close">×</button>
        <div class="uhd-modal__body"><slot /></div>
        <div v-if="$slots.footer" class="uhd-modal__footer"><slot name="footer" /></div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.uhd-modal__overlay {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  animation: uhd-fade-in var(--uhd-dur-fade) var(--uhd-ease-standard);
  font-family: var(--uhd-font-sans);
}
.uhd-modal {
  position: relative;
  background: var(--uhd-l-surface);
  border-radius: var(--uhd-radius-modal);
  box-shadow: var(--uhd-shadow-modal);
  padding: 32px;
  animation: uhd-blur-in var(--uhd-dur-blur-in) var(--uhd-ease-pop);
}
.uhd-modal__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.uhd-modal__title {
  margin: 0;
  font-size: var(--uhd-fs-h3);
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-l-text);
}
.uhd-modal__close {
  flex: none;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--uhd-l-text-2);
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
}
.uhd-modal__close:hover { background: var(--uhd-l-input-bg); }
.uhd-modal__close--float {
  position: absolute;
  top: 14px;
  right: 14px;
}
.uhd-modal__body {
  font-size: var(--uhd-fs-body);
  color: var(--uhd-l-text-2);
  line-height: var(--uhd-lh-body);
}
.uhd-modal__footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 24px;
}
</style>
