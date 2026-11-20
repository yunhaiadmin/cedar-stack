<script setup lang="ts">
/**
 * AxModal —— 弹窗（说明书 2.8）
 * 居中卡约 400px 宽、圆角 24px、内边距 32px、标题 24px、右上 × 关闭；
 * 遮罩黑 45%（无模糊）；入场淡入 + 模糊→清晰。
 * 公告、线路、确认类弹窗同模具。
 * 2026-11-15 曾玻璃化（全站排查批 2）；2026-11-18 用户亲令弹窗
 * 改实色：面板底用实色面（styles/modal-glass.css 的
 * --modal-glass-bg 现引实色 surface 令牌）、去 backdrop 与
 * 内高光，遮罩去 blur；几何/内边距/动画不动。面板 Teleport
 * 到 body、--ax-l-* 只剩 :root 值，故面板作用域内把文字
 * 令牌随面色翻转（深色面＝浅字、浅色面＝恢复 :root 深字），
 * 插槽内容同受益。
 */
import { onBeforeUnmount, watch } from 'vue'
import '../../styles/modal-glass.css'

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
    <div v-if="modelValue" class="ax-modal__overlay" @click.self="close">
      <div class="ax-modal" :style="{ width: `min(${width}px, calc(100vw - 32px))` }" role="dialog" aria-modal="true">
        <div v-if="title" class="ax-modal__head">
          <h3 class="ax-modal__title">{{ title }}</h3>
          <button type="button" class="ax-modal__close" aria-label="关闭" @click="close">×</button>
        </div>
        <button v-else type="button" class="ax-modal__close ax-modal__close--float" aria-label="关闭" @click="close">×</button>
        <div class="ax-modal__body"><slot /></div>
        <div v-if="$slots.footer" class="ax-modal__footer"><slot name="footer" /></div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.ax-modal__overlay {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: var(--modal-scrim-dark);
  animation: ax-fade-in var(--ax-dur-fade) var(--ax-ease-standard);
  font-family: var(--ax-font-sans);
}
.ax-modal {
  position: relative;
  /* 面板作用域文字令牌随面色翻转（默认深色面＝浅字） */
  --ax-l-text: var(--modal-glass-fg);
  --ax-l-text-2: var(--modal-glass-dim);
  --ax-l-input-bg: rgba(255, 255, 255, 0.08);
  background: var(--modal-glass-bg);
  border: 1px solid var(--modal-glass-edge);
  border-radius: var(--ax-radius-modal);
  box-shadow: var(--modal-glass-shadow);
  padding: 32px;
  animation: ax-blur-in var(--ax-dur-blur-in) var(--ax-ease-pop);
}
/* 浅色面：文字令牌恢复 :root 浅面值（深字） */
html[data-theme='light'] .ax-modal {
  --ax-l-text: #111111;
  --ax-l-text-2: #6b7280;
  --ax-l-input-bg: #f5f5f5;
}
.ax-modal__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.ax-modal__title {
  margin: 0;
  font-size: var(--ax-fs-h3);
  font-weight: var(--ax-fw-bold);
  color: var(--ax-l-text);
}
.ax-modal__close {
  flex: none;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--ax-l-text-2);
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
}
.ax-modal__close:hover { background: var(--ax-l-input-bg); }
.ax-modal__close--float {
  position: absolute;
  top: 14px;
  right: 14px;
}
.ax-modal__body {
  font-size: var(--ax-fs-body);
  color: var(--ax-l-text-2);
  line-height: var(--ax-lh-body);
}
.ax-modal__footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 24px;
}
</style>
