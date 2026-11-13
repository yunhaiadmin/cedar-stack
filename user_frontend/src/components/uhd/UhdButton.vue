<script setup lang="ts">
/**
 * UhdButton —— 胶囊按钮（说明书 2.2）
 * 主按钮：彩虹渐变边框（白名单位置）+ 淡彩渐变底（浅色面）/ 深色毛玻璃底（深色面）；
 * 次按钮：白底 + 1px 灰边框（深色面为透明底 + 深色边框 + 白字）；
 * 危险：红色文字按钮，不做填充；播放：白胶囊 + ▶。
 * 只依赖 --uhd-* 令牌。
 */
withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'danger' | 'play'
    /** 深色面语境（营销面/片库面） */
    dark?: boolean
    size?: 'md' | 'sm'
    block?: boolean
    disabled?: boolean
    type?: 'button' | 'submit'
  }>(),
  { variant: 'primary', dark: false, size: 'md', block: false, disabled: false, type: 'button' },
)
</script>

<template>
  <button
    :type="type"
    class="uhd-btn"
    :class="[`uhd-btn--${variant}`, `uhd-btn--${size}`, { 'uhd-btn--dark': dark, 'uhd-btn--block': block }]"
    :disabled="disabled"
  >
    <span v-if="variant === 'play'" class="uhd-btn__play-icon" aria-hidden="true">▶</span>
    <slot />
  </button>
</template>

<style scoped>
.uhd-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border-radius: var(--uhd-radius-pill);
  font-family: var(--uhd-font-sans);
  font-weight: var(--uhd-fw-semibold);
  white-space: nowrap;
  cursor: pointer;
  transition: opacity var(--uhd-dur-ui) var(--uhd-ease-standard);
}
.uhd-btn--md {
  height: 48px;
  padding: 0 26px;
  font-size: var(--uhd-fs-body);
}
.uhd-btn--sm {
  height: 38px;
  padding: 0 18px;
  font-size: var(--uhd-fs-caption);
}
.uhd-btn--block {
  width: 100%;
}
.uhd-btn:focus-visible {
  outline: 2px solid var(--uhd-l-link);
  outline-offset: 2px;
}

/* 主按钮（浅色面）：彩虹边框 + 淡彩底 + 玻璃栈（内高光 + 柔影 + backdrop，2026-11-13 补齐） */
.uhd-btn--primary {
  color: var(--uhd-l-text);
  border: var(--uhd-rainbow-border-w) solid transparent;
  background:
    var(--uhd-rainbow-soft) padding-box,
    var(--uhd-rainbow) border-box;
  background-color: var(--uhd-l-surface);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.65),
    var(--uhd-shadow-card-l);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
}
/* 主按钮（深色面）：彩虹边框 + 深色毛玻璃底 + 白字 + 玻璃栈（内高光 + 柔影） */
.uhd-btn--primary.uhd-btn--dark {
  color: var(--uhd-d-text);
  background:
    linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box,
    var(--uhd-rainbow) border-box;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.22),
    0 12px 28px -12px rgba(0, 0, 0, 0.55);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
}
/* 次按钮（浅色面） */
.uhd-btn--secondary {
  color: var(--uhd-l-text);
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  background: var(--uhd-l-surface);
}
/* 次按钮（深色面） */
.uhd-btn--secondary.uhd-btn--dark {
  color: var(--uhd-d-text);
  border-color: var(--uhd-d-border-2);
  background: transparent;
}
/* 危险：红色文字，不填充 */
.uhd-btn--danger {
  color: var(--uhd-danger);
  border: var(--uhd-border-w) solid transparent;
  background: transparent;
  font-weight: var(--uhd-fw-medium);
}
/* 播放按钮：白胶囊（片库面） */
.uhd-btn--play {
  color: #000000;
  border: var(--uhd-border-w) solid transparent;
  background: var(--uhd-d-text);
}
.uhd-btn__play-icon {
  font-size: 11px;
  line-height: 1;
}
/* 禁用：灰字 + 低透明度、无悬停反馈（彩虹退场） */
.uhd-btn:disabled {
  color: var(--uhd-l-text-3);
  border-color: var(--uhd-l-border);
  background: var(--uhd-l-input-bg);
  box-shadow: none;
  opacity: 0.55;
  cursor: not-allowed;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
.uhd-btn--danger:disabled {
  background: transparent;
  border-color: transparent;
}
</style>
