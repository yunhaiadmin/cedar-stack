<script setup lang="ts">
/**
 * 区块卡片（ax 口径 · 玻璃栈）：半透底 + blur/saturate + 玻璃边 + 内高光 + 柔影
 * （同用户端玻璃卡配方，见 AxMemberCard / AxNavPill 下拉），可选无衬线标题行。
 *
 * - 标题行：icon（lucide 组件）+ 标题 + 灰色 meta（「近 7 天」之类）+ 右侧 actions 插槽
 * - tone="accent"：主色描边（同用户端 .au-card-accent），给「需要注意」的块用
 * - flush：内容区不留内边距（里面直接放 el-table / 列表时用，表格自己有单元格内距）
 * 用法见 ./README.md。
 */
withDefaults(
  defineProps<{
    title?: string
    /** 标题后的灰色小字：时间范围、计数、口径 */
    meta?: string
    /** 标题前的图标（传 lucide-vue-next 组件本身） */
    icon?: unknown
    /** 标题下的一行说明 */
    description?: string
    tone?: 'default' | 'accent'
    /** 内容区去掉内边距（放表格 / 贴边列表） */
    flush?: boolean
    /** 根元素标签：独立区块用 section，列表里的卡片可用 div / article */
    as?: string
    /** 根元素 id：页内锚点导航用 */
    id?: string
  }>(),
  { title: '', meta: '', icon: undefined, description: '', tone: 'default', flush: false, as: 'section', id: undefined },
)
</script>

<template>
  <component :is="as" :id="id || undefined" class="au-section" :class="[`is-${tone}`, { 'is-flush': flush }]">
    <header v-if="title || $slots.title || $slots.actions" class="au-section__head">
      <div class="au-section__heading">
        <h3 class="au-section__title">
          <component :is="icon" v-if="icon" :size="16" class="au-section__icon" />
          <slot name="title">{{ title }}</slot>
          <span v-if="meta" class="au-section__meta">{{ meta }}</span>
        </h3>
        <p v-if="description" class="au-section__desc">{{ description }}</p>
      </div>
      <div v-if="$slots.actions" class="au-section__actions">
        <slot name="actions" />
      </div>
    </header>
    <div class="au-section__body">
      <slot />
    </div>
    <footer v-if="$slots.footer" class="au-section__foot">
      <slot name="footer" />
    </footer>
  </component>
</template>

<style scoped>
.au-section {
  min-width: 0;
  background: var(--ax-glass-d);
  border: 1px solid var(--ax-glass-border);
  border-radius: var(--au-r-lg);
  /* 玻璃六件：半透底 + blur/saturate + 内高光 + 柔影（同用户端玻璃卡） */
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08), var(--ax-shadow-card-d);
  backdrop-filter: var(--ax-glass-filter);
  -webkit-backdrop-filter: var(--ax-glass-filter);
  transition: transform var(--au-fast) var(--au-ease), border-color var(--au-fast) var(--au-ease),
    box-shadow var(--au-fast) var(--au-ease);
}
html[data-theme='light'] .au-section {
  background: var(--ax-glass-l);
  border-color: var(--ax-glass-border-l);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--ax-shadow-card-l);
}
/* P3：悬停提升只在桌面（hover 可用 + 精确指针）生效，避免移动端触摸 :hover 粘滞 */
@media (hover: hover) and (pointer: fine) {
  .au-section:hover {
    border-color: var(--au-border-strong);
    transform: translateY(-1px);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08), var(--au-lift-shadow);
  }
  html[data-theme='light'] .au-section:hover {
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--au-lift-shadow);
  }
}

.au-section.is-accent { border-color: var(--au-primary-border); }

.au-section__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 16px 20px 0;
}

.au-section__heading { min-width: 0; }

.au-section__title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-family: var(--au-font-serif);
  font-size: var(--ax-fs-body);
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--au-text);
}

.au-section__icon { color: var(--au-primary); flex-shrink: 0; }

.au-section__meta {
  font-family: var(--au-font-sans);
  font-size: var(--ax-fs-aux-sm);
  font-weight: 400;
  letter-spacing: 0;
  color: var(--au-text-4);
}

.au-section__desc {
  margin: 4px 0 0;
  font-size: var(--ax-fs-aux-sm);
  line-height: 1.55;
  color: var(--au-text-3);
}

.au-section__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.au-section__body { padding: 14px 20px 18px; }
.au-section.is-flush .au-section__body { padding: 10px 0 4px; }

.au-section__foot {
  padding: 12px 20px;
  border-top: 1px solid var(--au-border);
  font-size: var(--ax-fs-aux-sm);
  color: var(--au-text-3);
}

@media (max-width: 640px) {
  .au-section__head { padding: 14px 16px 0; }
  .au-section__body { padding: 12px 16px 16px; }
  .au-section__foot { padding: 10px 16px; }
}
</style>
