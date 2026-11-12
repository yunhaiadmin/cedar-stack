<script setup lang="ts">
/**
 * UhdStatusRow —— 状态行（说明书 2.6，办事面基础行组件）
 * 左图标 22px + 标题 16px 粗 + 副文案 14px 灰；右为状态（圆点+文字）/ 操作插槽 / 「>」；
 * 行高 64–72px（会话卡变体约 90px、步骤行约 56px），行间 1px --uhd-l-divider。
 */
withDefaults(
  defineProps<{
    title: string
    desc?: string
    /** 右侧状态文字（如「已设置」「在线」「6 分钟前」） */
    statusText?: string
    /** 状态点色调；plain=无点纯灰字（相对时间等） */
    tone?: 'ok' | 'idle' | 'warn' | 'bad' | 'plain'
    chevron?: boolean
    size?: 'md' | 'session' | 'step'
    divider?: boolean
    clickable?: boolean
  }>(),
  { desc: '', statusText: '', tone: 'plain', chevron: false, size: 'md', divider: true, clickable: false },
)

const emit = defineEmits<{ click: [] }>()
</script>

<template>
  <div
    class="uhd-row"
    :class="[`uhd-row--${size}`, { 'uhd-row--divider': divider, 'uhd-row--clickable': clickable }]"
    :role="clickable ? 'button' : undefined"
    :tabindex="clickable ? 0 : undefined"
    @click="clickable && emit('click')"
  >
    <span v-if="$slots.icon" class="uhd-row__icon"><slot name="icon" /></span>
    <span class="uhd-row__main">
      <span class="uhd-row__title">{{ title }}</span>
      <span v-if="desc" class="uhd-row__desc">{{ desc }}</span>
    </span>
    <span v-if="$slots.action" class="uhd-row__action"><slot name="action" /></span>
    <span v-else-if="statusText" class="uhd-row__status">
      <span v-if="tone !== 'plain'" class="uhd-row__dot" :class="`uhd-row__dot--${tone}`" aria-hidden="true" />
      <span :class="`uhd-row__status-text--${tone}`">{{ statusText }}</span>
    </span>
    <span v-if="chevron" class="uhd-row__chevron" aria-hidden="true">›</span>
  </div>
</template>

<style scoped>
.uhd-row {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 68px;
  padding: 10px 2px;
  font-family: var(--uhd-font-sans);
}
.uhd-row--session { min-height: 90px; }
.uhd-row--step { min-height: 56px; }
.uhd-row--divider { border-bottom: var(--uhd-border-w) solid var(--uhd-l-divider); }
.uhd-row--clickable { cursor: pointer; }
.uhd-row__icon {
  display: inline-flex;
  flex: none;
  font-size: 22px;
  line-height: 1;
  color: var(--uhd-l-text);
}
.uhd-row__main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}
.uhd-row__title {
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-l-text);
}
.uhd-row__desc {
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
}
.uhd-row__action { display: inline-flex; flex: none; }
.uhd-row__status {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  flex: none;
  font-size: var(--uhd-fs-caption);
}
.uhd-row__dot { width: 8px; height: 8px; border-radius: 50%; }
.uhd-row__dot--ok { background: var(--uhd-dot-ok); }
.uhd-row__dot--idle { background: var(--uhd-dot-idle); }
.uhd-row__dot--warn { background: var(--uhd-dot-doing); }
.uhd-row__dot--bad { background: var(--uhd-dot-bad); }
.uhd-row__status-text--ok { color: var(--uhd-success); }
.uhd-row__status-text--warn { color: var(--uhd-warn-text-2); }
.uhd-row__status-text--bad { color: var(--uhd-danger); }
.uhd-row__status-text--idle,
.uhd-row__status-text--plain { color: var(--uhd-l-text-2); }
.uhd-row__chevron {
  flex: none;
  font-size: 20px;
  line-height: 1;
  color: var(--uhd-l-text-3);
}
</style>
