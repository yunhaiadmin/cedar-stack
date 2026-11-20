<script setup lang="ts">
/**
 * AxButton —— 胶囊按钮（说明书 2.2）
 * 主按钮：彩虹渐变边框（白名单位置）+ 淡彩渐变底（浅色面）/ 深色毛玻璃底（深色面）；
 * 次按钮：白底 + 1px 灰边框（深色面为透明底 + 深色边框 + 白字）；
 * 危险：红色文字按钮，不做填充；播放：白胶囊 + 实心三角 SVG 图标。
 * 只依赖 --ax-* 令牌。
 */
withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'danger' | 'play'
    /** 深色面语境（营销面/片库面） */
    dark?: boolean
    size?: 'md' | 'mid' | 'sm'
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
    class="ax-btn"
    :class="[`ax-btn--${variant}`, `ax-btn--${size}`, { 'ax-btn--dark': dark, 'ax-btn--block': block }]"
    :disabled="disabled"
  >
    <!-- 实心三角 SVG（currentColor）：▶ 字符在移动端会被渲染成蓝色 emoji 方块，已换掉 -->
    <svg v-if="variant === 'play'" class="ax-btn__play-icon" width="11" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M2 1.3v9.4c0 .8.9 1.3 1.6.9l7.2-4.7c.6-.4.6-1.4 0-1.8L3.6.4C2.9-.1 2 .5 2 1.3Z" fill="currentColor" />
    </svg>
    <slot />
  </button>
</template>

<style scoped>
.ax-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border-radius: var(--ax-radius-pill);
  font-family: var(--ax-font-sans);
  font-weight: var(--ax-fw-semibold);
  white-space: nowrap;
  cursor: pointer;
  transition:
    opacity var(--ax-dur-ui) var(--ax-ease-standard),
    transform var(--ax-press-dur) var(--ax-ease-spring),
    background-color var(--ax-dur-base) var(--ax-ease-standard),
    box-shadow var(--ax-dur-base) var(--ax-ease-standard);
}
/* 按压回弹（批 B 微交互，报告 §2.4）：缩放＋亮度＋时长走 --ax-press 三值；
   禁用态不触发，松手靠 spring 曲线回弹 */
.ax-btn:not(:disabled):active {
  transform: scale(var(--ax-press-scale));
  filter: brightness(var(--ax-press-brightness));
}
.ax-btn--md {
  height: 48px;
  padding: 0 26px;
  font-size: var(--ax-fs-body);
}
/* 中档 42 级（质感第二波，2026-11-18）：友站片库立即播放 146×42、
   一起看开启 265×44 均在 42–54 区间，供片库/弹层主钮用 */
.ax-btn--mid {
  height: 42px;
  padding: 0 22px;
  font-size: var(--ax-fs-body);
}
.ax-btn--sm {
  height: 38px;
  padding: 0 18px;
  font-size: var(--ax-fs-caption);
}
.ax-btn--block {
  width: 100%;
}
.ax-btn:focus-visible {
  outline: 2px solid var(--ax-l-link);
  outline-offset: 2px;
}

/* 主按钮（浅色面）：彩虹边框 + 淡彩底 + 玻璃栈（内高光 + 柔影 + backdrop，2026-11-13 补齐） */
.ax-btn--primary {
  color: var(--ax-l-text);
  border: var(--ax-rainbow-border-w) solid transparent;
  background:
    var(--ax-rainbow-soft) padding-box,
    var(--ax-rainbow) border-box;
  background-color: var(--ax-l-surface);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.65),
    var(--ax-shadow-card-l);
  backdrop-filter: var(--ax-glass-filter);
  -webkit-backdrop-filter: var(--ax-glass-filter);
}
/* 主按钮（深色面）：纯透光玻璃底 + 白字 + 玻璃栈（内高光 + 柔影）；
   彩虹只在 1.5px 细环上（::before 环切，见下）——钮面内一律是玻璃，
   背景光束全幅透过。旧写法彩虹渐变铺满 border-box，透过半透明玻璃在
   钮面内晕染成色块（2026-11-18 返工，hero/buttons/scrim 三图亲验打回）。 */
.ax-btn--primary.ax-btn--dark {
  position: relative;
  color: var(--ax-d-text);
  border: var(--ax-rainbow-border-w) solid transparent;
  background: linear-gradient(var(--ax-glass-btn-d), var(--ax-glass-btn-d));
  box-shadow: var(--ax-shadow-sheet);
  backdrop-filter: var(--ax-glass-filter);
  -webkit-backdrop-filter: var(--ax-glass-filter);
}
/* 彩虹环（环形-only）：渐变层 padding 取边框宽 1.5px，mask 剔掉 content-box
   只剩环形；停点走 --ax-rainbow 不对称分布（左鲜明、向右淡出），与钮面
   玻璃层分离，钮面内不再有彩虹晕染。 */
.ax-btn--primary.ax-btn--dark::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: var(--ax-rainbow-border-w);
  background: var(--ax-rainbow);
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  mask-composite: exclude;
  pointer-events: none;
}
/* 禁用态彩虹退场：环随之一并隐藏 */
.ax-btn--primary.ax-btn--dark:disabled::before {
  display: none;
}
/* 次按钮（浅色面） */
.ax-btn--secondary {
  color: var(--ax-l-text);
  border: var(--ax-border-w) solid var(--ax-l-border);
  background: var(--ax-l-surface);
}
/* 次按钮（深色面） */
.ax-btn--secondary.ax-btn--dark {
  color: var(--ax-d-text);
  border-color: var(--ax-d-border-2);
  background: transparent;
}
/* 危险：红色文字，不填充 */
.ax-btn--danger {
  color: var(--ax-danger);
  border: var(--ax-border-w) solid transparent;
  background: transparent;
  font-weight: var(--ax-fw-medium);
}
/* 播放按钮：白胶囊（片库面） */
.ax-btn--play {
  color: #000000;
  border: var(--ax-border-w) solid transparent;
  background: var(--ax-d-text);
}
.ax-btn__play-icon {
  display: block;
  flex: none;
}
/* 禁用：灰字 + 低透明度、无悬停反馈（彩虹退场） */
.ax-btn:disabled {
  color: var(--ax-l-text-3);
  border-color: var(--ax-l-border);
  background: var(--ax-l-input-bg);
  box-shadow: none;
  opacity: 0.55;
  cursor: not-allowed;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
.ax-btn--danger:disabled {
  background: transparent;
  border-color: transparent;
}
</style>
