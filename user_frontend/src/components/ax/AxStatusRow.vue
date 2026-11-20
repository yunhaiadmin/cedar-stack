<script setup lang="ts">
/**
 * AxStatusRow —— 状态行（说明书 2.6，办事面基础行组件）
 * 左图标 22px + 标题 16px 粗 + 副文案 14px 灰；右为状态（圆点+文字）/ 操作插槽 / 「>」；
 * 行高 64–72px（会话卡变体约 90px、步骤行约 56px），行间 1px --ax-l-divider。
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
    class="ax-row"
    :class="[`ax-row--${size}`, { 'ax-row--divider': divider, 'ax-row--clickable': clickable }]"
    :role="clickable ? 'button' : undefined"
    :tabindex="clickable ? 0 : undefined"
    @click="clickable && emit('click')"
  >
    <span v-if="$slots.icon" class="ax-row__icon"><slot name="icon" /></span>
    <span class="ax-row__main">
      <span class="ax-row__title">{{ title }}</span>
      <span v-if="desc" class="ax-row__desc">{{ desc }}</span>
    </span>
    <span v-if="$slots.action" class="ax-row__action"><slot name="action" /></span>
    <span v-else-if="statusText" class="ax-row__status">
      <span v-if="tone !== 'plain'" class="ax-row__dot" :class="`ax-row__dot--${tone}`" aria-hidden="true" />
      <span :class="`ax-row__status-text--${tone}`">{{ statusText }}</span>
    </span>
    <span v-if="chevron" class="ax-row__chevron" aria-hidden="true">›</span>
  </div>
</template>

<style scoped>
.ax-row {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: var(--ax-row-h);
  padding: 10px 2px;
  font-family: var(--ax-font-sans);
}
.ax-row--session { min-height: 90px; }
.ax-row--step { min-height: 56px; }
.ax-row--divider { border-bottom: var(--ax-border-w) solid var(--ax-l-divider); }
.ax-row--clickable { cursor: pointer; }
.ax-row__icon {
  display: inline-flex;
  flex: none;
  font-size: 22px;
  line-height: 1;
  color: var(--ax-l-text);
}
.ax-row__main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}
.ax-row__title {
  font-size: var(--ax-fs-lead);
  font-weight: var(--ax-fw-semibold);
  color: var(--ax-l-text);
}
.ax-row__desc {
  font-size: var(--ax-fs-caption);
  color: var(--ax-l-text-2);
}
.ax-row__action { display: inline-flex; flex: none; }
.ax-row__status {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  flex: none;
  font-size: var(--ax-fs-caption);
}
.ax-row__dot { width: 8px; height: 8px; border-radius: 50%; }
.ax-row__dot--ok { background: var(--ax-dot-ok); }
.ax-row__dot--idle { background: var(--ax-dot-idle); }
.ax-row__dot--warn { background: var(--ax-dot-doing); }
.ax-row__dot--bad { background: var(--ax-dot-bad); }
.ax-row__status-text--ok { color: var(--ax-success); }
.ax-row__status-text--warn { color: var(--ax-warn-text-2); }
.ax-row__status-text--bad { color: var(--ax-danger); }
.ax-row__status-text--idle,
.ax-row__status-text--plain { color: var(--ax-l-text-2); }
.ax-row__chevron {
  flex: none;
  font-size: 20px;
  line-height: 1;
  color: var(--ax-l-text-3);
}
</style>
