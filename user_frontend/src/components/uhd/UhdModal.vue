<script setup lang="ts">
/**
 * UhdModal —— 弹窗（说明书 2.8）
 * 居中卡约 400px 宽、圆角 24px、内边距 32px、标题 24px、右上 × 关闭；
 * 遮罩黑 40% + 背景模糊；入场淡入 + 模糊→清晰。
 * 公告、线路、确认类弹窗同模具。
 * 2026-11-15 弹窗玻璃化（全站排查批 2）：面板由实心 --uhd-l-surface
 * 换玻璃五件（styles/modal-glass.css，#233 购流量弹窗标尺，深浅
 * 双主题）；几何/内边距/动画/遮罩不动。面板 Teleport 到 body、
 * --uhd-l-* 只剩 :root 值，故面板作用域内把文字令牌随材质翻转
 * （深色玻璃＝浅字、浅色玻璃＝恢复 :root 深字），插槽内容同受益。
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
  background: var(--modal-scrim-dark);
  backdrop-filter: var(--modal-scrim-dark-filter);
  -webkit-backdrop-filter: var(--modal-scrim-dark-filter);
  animation: uhd-fade-in var(--uhd-dur-fade) var(--uhd-ease-standard);
  font-family: var(--uhd-font-sans);
}
.uhd-modal {
  position: relative;
  /* 面板作用域文字令牌随材质翻转（默认深色玻璃＝浅字） */
  --uhd-l-text: var(--modal-glass-fg);
  --uhd-l-text-2: var(--modal-glass-dim);
  --uhd-l-input-bg: rgba(255, 255, 255, 0.08);
  background: var(--modal-glass-bg);
  border: 1px solid var(--modal-glass-edge);
  border-radius: var(--uhd-radius-modal);
  box-shadow:
    inset 0 1px 0 var(--modal-glass-hi),
    inset 0 0 0 1px var(--modal-glass-ring),
    var(--modal-glass-shadow);
  backdrop-filter: var(--modal-glass-filter);
  -webkit-backdrop-filter: var(--modal-glass-filter);
  padding: 32px;
  animation: uhd-blur-in var(--uhd-dur-blur-in) var(--uhd-ease-pop);
}
/* 浅色玻璃：文字令牌恢复 :root 浅面值（深字） */
html[data-theme='light'] .uhd-modal {
  --uhd-l-text: #111111;
  --uhd-l-text-2: #6b7280;
  --uhd-l-input-bg: #f5f5f5;
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
