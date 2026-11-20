<script setup lang="ts">
/**
 * AxEmptyState —— 空态虚线卡（说明书 2.7，全站列表空态只用这一种）
 * card：虚线边框卡，高约 210px、圆角 16px，居中 15px 灰字一句话（可带一个主按钮）；
 * icon：图标空态变体（48px 圆 icon + 16px 粗标题 + 说明 + 按钮，如 Passkey 空态）。
 */
withDefaults(
  defineProps<{
    variant?: 'card' | 'icon'
    /** 主文案（card 变体即居中那句话；icon 变体为标题） */
    title: string
    desc?: string
  }>(),
  { variant: 'card', desc: '' },
)
</script>

<template>
  <div class="ax-empty" :class="`ax-empty--${variant}`">
    <span v-if="variant === 'icon'" class="ax-empty__icon"><slot name="icon" /></span>
    <p class="ax-empty__title">{{ title }}</p>
    <p v-if="desc" class="ax-empty__desc">{{ desc }}</p>
    <div v-if="$slots.action" class="ax-empty__action"><slot name="action" /></div>
  </div>
</template>

<style scoped>
.ax-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  text-align: center;
  font-family: var(--ax-font-sans);
}
.ax-empty--card {
  min-height: 210px;
  padding: 32px 24px;
  border: 1.5px dashed var(--ax-l-border);
  border-radius: var(--ax-radius-card-sm);
}
.ax-empty--card .ax-empty__title {
  font-size: var(--ax-fs-body);
  color: var(--ax-l-text-2);
}
.ax-empty--icon { padding: 28px 24px; }
.ax-empty__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: var(--ax-l-input-bg);
  color: var(--ax-l-text-2);
  font-size: 22px;
  margin-bottom: 6px;
}
.ax-empty--icon .ax-empty__title {
  font-size: var(--ax-fs-lead);
  font-weight: var(--ax-fw-semibold);
  color: var(--ax-l-text);
}
.ax-empty__desc {
  font-size: var(--ax-fs-caption);
  color: var(--ax-l-text-2);
}
.ax-empty__action { margin-top: 10px; }
.ax-empty p { margin: 0; }
</style>
