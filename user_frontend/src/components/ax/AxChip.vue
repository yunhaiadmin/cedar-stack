<script setup lang="ts">
/**
 * AxChip —— 标签胶囊（说明书 2.4）
 * spec：Hero 规格 chip（图标 + 主词粗体 + 副词灰，无边框横排，深色面）；
 * mini：深色卡内小 chip（高 28px、圆角 14px、1px --ax-d-chip-border、12px 灰字）；
 * status：状态筛选 chip（胶囊 + 状态点四色）；
 * badge：「推荐」角标（约 45×22px，彩虹底——白名单位置）；
 * member：会员状态小胶囊（琥珀底）。
 */
withDefaults(
  defineProps<{
    variant?: 'spec' | 'mini' | 'status' | 'badge' | 'member'
    /** status 变体的状态点：待处理/处理中/完成·在线/拒绝·异常 */
    dot?: 'idle' | 'doing' | 'ok' | 'bad'
    /** spec 变体的副词 */
    sub?: string
  }>(),
  { variant: 'mini', dot: 'idle', sub: '' },
)
</script>

<template>
  <span class="ax-chip" :class="`ax-chip--${variant}`">
    <span v-if="variant === 'status'" class="ax-chip__dot" :class="`ax-chip__dot--${dot}`" aria-hidden="true" />
    <span v-if="$slots.icon" class="ax-chip__icon"><slot name="icon" /></span>
    <span class="ax-chip__label"><slot /></span>
    <span v-if="variant === 'spec' && sub" class="ax-chip__sub">{{ sub }}</span>
  </span>
</template>

<style scoped>
.ax-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-family: var(--ax-font-sans);
  white-space: nowrap;
  /* 基本交互过渡（批 B 微交互）：状态/主题切换与按压不再硬跳，
     时长走手册 base 档、transform 走 micro 档＋spring */
  transition:
    background-color var(--ax-dur-base) var(--ax-ease-standard),
    border-color var(--ax-dur-base) var(--ax-ease-standard),
    color var(--ax-dur-base) var(--ax-ease-standard),
    transform var(--ax-dur-micro) var(--ax-ease-spring);
}
/* 规格 chip：无边框横排 */
.ax-chip--spec {
  color: var(--ax-d-text);
  font-size: var(--ax-fs-lead);
  font-weight: var(--ax-fw-bold);
}
.ax-chip--spec .ax-chip__icon {
  display: inline-flex;
  font-size: 20px;
  line-height: 1;
}
.ax-chip--spec .ax-chip__sub {
  font-size: var(--ax-fs-aux);
  font-weight: var(--ax-fw-normal);
  color: var(--ax-d-text-2);
}
/* 深色卡内小 chip */
.ax-chip--mini {
  height: 28px;
  padding: 0 11px;
  border: var(--ax-border-w) solid var(--ax-d-chip-border);
  border-radius: var(--ax-radius-base);
  color: var(--ax-d-text-2);
  font-size: var(--ax-fs-aux-sm);
}
/* 状态筛选 chip（浅色面） */
.ax-chip--status {
  height: 32px;
  padding: 0 13px;
  border: var(--ax-border-w) solid var(--ax-l-border);
  border-radius: var(--ax-radius-pill);
  background: var(--ax-l-surface);
  color: var(--ax-l-text);
  font-size: var(--ax-fs-aux);
}
.ax-chip__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}
.ax-chip__dot--idle { background: var(--ax-dot-idle); }
.ax-chip__dot--doing { background: var(--ax-dot-doing); }
.ax-chip__dot--ok { background: var(--ax-dot-ok); }
.ax-chip__dot--bad { background: var(--ax-dot-bad); }
/* 「推荐」角标：彩虹底（白名单）——填色走不透明 fill 版，边框版右半段透明会拖垮填色 */
.ax-chip--badge {
  height: 22px;
  padding: 0 9px;
  border-radius: var(--ax-radius-pill);
  background: var(--ax-rainbow-fill);
  color: var(--ax-d-text);
  font-size: var(--ax-fs-micro);
  font-weight: var(--ax-fw-bold);
}
/* 会员状态小胶囊：琥珀 */
.ax-chip--member {
  height: 22px;
  padding: 0 9px;
  border-radius: var(--ax-radius-pill);
  background: var(--ax-warn-bg);
  color: var(--ax-warn-text);
  font-size: var(--ax-fs-aux-sm);
  font-weight: var(--ax-fw-semibold);
}
</style>
