<script setup lang="ts">
/**
 * UhdChip —— 标签胶囊（说明书 2.4）
 * spec：Hero 规格 chip（图标 + 主词粗体 + 副词灰，无边框横排，深色面）；
 * mini：深色卡内小 chip（高 28px、圆角 14px、1px --uhd-d-chip-border、12px 灰字）；
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
  <span class="uhd-chip" :class="`uhd-chip--${variant}`">
    <span v-if="variant === 'status'" class="uhd-chip__dot" :class="`uhd-chip__dot--${dot}`" aria-hidden="true" />
    <span v-if="$slots.icon" class="uhd-chip__icon"><slot name="icon" /></span>
    <span class="uhd-chip__label"><slot /></span>
    <span v-if="variant === 'spec' && sub" class="uhd-chip__sub">{{ sub }}</span>
  </span>
</template>

<style scoped>
.uhd-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-family: var(--uhd-font-sans);
  white-space: nowrap;
}
/* 规格 chip：无边框横排 */
.uhd-chip--spec {
  color: var(--uhd-d-text);
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-bold);
}
.uhd-chip--spec .uhd-chip__icon {
  display: inline-flex;
  font-size: 20px;
  line-height: 1;
}
.uhd-chip--spec .uhd-chip__sub {
  font-size: var(--uhd-fs-aux);
  font-weight: var(--uhd-fw-normal);
  color: var(--uhd-d-text-2);
}
/* 深色卡内小 chip */
.uhd-chip--mini {
  height: 28px;
  padding: 0 11px;
  border: var(--uhd-border-w) solid var(--uhd-d-chip-border);
  border-radius: var(--uhd-radius-base);
  color: var(--uhd-d-text-2);
  font-size: var(--uhd-fs-aux-sm);
}
/* 状态筛选 chip（浅色面） */
.uhd-chip--status {
  height: 32px;
  padding: 0 13px;
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-pill);
  background: var(--uhd-l-surface);
  color: var(--uhd-l-text);
  font-size: var(--uhd-fs-aux);
}
.uhd-chip__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}
.uhd-chip__dot--idle { background: var(--uhd-dot-idle); }
.uhd-chip__dot--doing { background: var(--uhd-dot-doing); }
.uhd-chip__dot--ok { background: var(--uhd-dot-ok); }
.uhd-chip__dot--bad { background: var(--uhd-dot-bad); }
/* 「推荐」角标：彩虹底（白名单） */
.uhd-chip--badge {
  height: 22px;
  padding: 0 9px;
  border-radius: var(--uhd-radius-pill);
  background: var(--uhd-rainbow);
  color: var(--uhd-d-text);
  font-size: var(--uhd-fs-micro);
  font-weight: var(--uhd-fw-bold);
}
/* 会员状态小胶囊：琥珀 */
.uhd-chip--member {
  height: 22px;
  padding: 0 9px;
  border-radius: var(--uhd-radius-pill);
  background: var(--uhd-warn-bg);
  color: var(--uhd-warn-text);
  font-size: var(--uhd-fs-aux-sm);
  font-weight: var(--uhd-fw-semibold);
}
</style>
