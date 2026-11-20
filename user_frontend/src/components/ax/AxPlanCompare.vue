<script setup lang="ts">
/**
 * AxPlanCompare —— 订阅单选对比表（说明书 2.11 的共用组件）
 * 官网定价（批 1）与站内订阅（批 2）共用同一套版式，全站只维护一份：
 * - 套餐横向并列，每列列头单选圆圈 20px + 套餐名 18px 粗 + 价格大字 + 计价注；
 * - 选中列 = 白卡（圆角 16px + 阴影），未选中列灰字半透明；
 * - 规格行来自套餐数据本身（时长 / 积分价 / features），不写死任何价格与规格；
 * - 推荐套餐（is_popular）带彩虹「推荐」角标（白名单位置：定价推荐角标）。
 * 官网版不带结算栏：底部按钮只 emit('select')，由页面决定跳转/下单动作。
 */
import { computed, ref, watch } from 'vue'
import AxButton from './AxButton.vue'
import type { SubscriptionPlan } from '@/api/economy'

const props = withDefaults(
  defineProps<{
    plans: SubscriptionPlan[]
    ctaText?: string
    /** 服名标注：多服时列头下显示套餐所属服 */
    showRealm?: boolean
    /** 受控选中（v-model:modelValue）：不传时组件内部维护选中态（官网版口径） */
    modelValue?: number | null
    /** 规格行扩展（如订阅页的权益行，由调用方按后端权益表生成） */
    extraSpecRows?: (plan: SubscriptionPlan) => string[]
  }>(),
  { ctaText: '立即开通', showRealm: true, modelValue: undefined, extraSpecRows: undefined },
)

const emit = defineEmits<{
  select: [plan: SubscriptionPlan]
  'update:modelValue': [id: number | null]
}>()

const innerSelectedId = ref<number | null>(null)
/** 受控时以 modelValue 为准，否则用内部选中态 */
const selectedId = computed<number | null>({
  get: () => (props.modelValue !== undefined ? props.modelValue : innerSelectedId.value),
  set: (v) => {
    innerSelectedId.value = v
    emit('update:modelValue', v)
  },
})

function pickDefault() {
  const popular = props.plans.find((p) => p.is_popular)
  selectedId.value = popular?.id ?? props.plans[0]?.id ?? null
}
watch(() => props.plans, pickDefault, { immediate: true })

function specRows(plan: SubscriptionPlan): string[] {
  const rows: string[] = [`有效期 ${plan.duration_days} 天`]
  if (props.extraSpecRows) rows.push(...props.extraSpecRows(plan))
  for (const f of plan.features ?? []) rows.push(f)
  return rows
}
</script>

<template>
  <div class="ax-plans">
    <div
      v-for="plan in plans"
      :key="plan.id"
      class="ax-plans__col"
      :class="{ 'ax-plans__col--selected': plan.id === selectedId }"
      role="radio"
      :aria-checked="plan.id === selectedId"
      tabindex="0"
      @click="selectedId = plan.id"
      @keydown.enter.prevent="selectedId = plan.id"
      @keydown.space.prevent="selectedId = plan.id"
    >
      <span v-if="plan.is_popular" class="ax-plans__badge">推荐</span>

      <div class="ax-plans__head">
        <span class="ax-plans__radio" :class="{ 'ax-plans__radio--on': plan.id === selectedId }" aria-hidden="true" />
        <span class="ax-plans__name">{{ plan.name }}</span>
      </div>
      <p v-if="showRealm && (plan.line_name || plan.realm_name)" class="ax-plans__realm">{{ plan.line_name || plan.realm_name }}</p>

      <p class="ax-plans__price">
        <span class="ax-plans__price-num">${{ plan.price }}</span>
        <span class="ax-plans__price-per">/ {{ plan.duration_days }} 天</span>
      </p>
      <p v-if="plan.description" class="ax-plans__desc">{{ plan.description }}</p>

      <ul class="ax-plans__specs">
        <li v-for="row in specRows(plan)" :key="row" class="ax-plans__spec">{{ row }}</li>
      </ul>

      <AxButton
        :variant="plan.id === selectedId ? 'primary' : 'secondary'"
        block
        size="sm"
        class="ax-plans__cta"
        @click.stop="emit('select', plan)"
      >
        {{ ctaText }}
      </AxButton>
    </div>
  </div>
</template>

<style scoped>
.ax-plans {
  display: grid;
  /* 质感第二波（2026-11-18）：列宽 287px、间距 24px（友站实测卡 287×454），居中排列 */
  grid-template-columns: repeat(auto-fit, 287px);
  justify-content: center;
  gap: 24px;
  max-width: var(--ax-container-pricing);
  margin: 0 auto;
  font-family: var(--ax-font-sans);
}

.ax-plans__col {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 454px;
  padding: 26px 24px;
  border: var(--ax-border-w) solid transparent;
  border-radius: 24px;
  color: var(--ax-l-text-2);
  opacity: 0.72;
  cursor: pointer;
  transition:
    opacity var(--ax-dur-ui) var(--ax-ease-standard),
    box-shadow var(--ax-dur-ui) var(--ax-ease-standard);
}
.ax-plans__col--selected {
  background: var(--ax-l-surface);
  border-color: var(--ax-l-border);
  box-shadow: var(--ax-shadow-card-l);
  color: var(--ax-l-text);
  opacity: 1;
}
.ax-plans__col:focus-visible {
  outline: 2px solid var(--ax-l-link);
  outline-offset: 2px;
}

/* 推荐角标：彩虹小胶囊（白名单位置）——填色走不透明 fill 版 */
.ax-plans__badge {
  position: absolute;
  top: 16px;
  right: 16px;
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 10px;
  border-radius: var(--ax-radius-pill);
  background: var(--ax-rainbow-fill);
  color: #ffffff;
  font-size: var(--ax-fs-aux-sm);
  font-weight: var(--ax-fw-bold);
  line-height: 1;
}

.ax-plans__head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.ax-plans__radio {
  width: 20px;
  height: 20px;
  flex: none;
  border-radius: 50%;
  border: 2px solid var(--ax-l-text-3);
  position: relative;
}
.ax-plans__radio--on {
  border-color: var(--ax-l-text);
}
.ax-plans__radio--on::after {
  content: '';
  position: absolute;
  inset: 3px;
  border-radius: 50%;
  background: var(--ax-l-text);
}
.ax-plans__name {
  font-size: var(--ax-fs-title);
  font-weight: var(--ax-fw-bold);
}
.ax-plans__realm {
  margin: 6px 0 0 30px;
  font-size: var(--ax-fs-aux);
  color: var(--ax-l-text-3);
}

.ax-plans__price {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin: 18px 0 4px;
}
.ax-plans__price-num {
  font-family: var(--ax-font-display);
  font-size: var(--ax-fs-price);
  font-weight: var(--ax-fw-black);
  line-height: 1;
  color: inherit;
}
.ax-plans__price-per {
  font-size: var(--ax-fs-caption);
  color: var(--ax-l-text-3);
}
.ax-plans__desc {
  margin: 4px 0 0;
  font-size: var(--ax-fs-caption);
  line-height: var(--ax-lh-body);
  color: var(--ax-l-text-2);
}

.ax-plans__specs {
  list-style: none;
  margin: 14px 0 22px;
  padding: 0;
  flex: 1;
}
.ax-plans__spec {
  padding: 12px 0;
  border-top: var(--ax-border-w) solid var(--ax-l-divider);
  font-size: var(--ax-fs-body);
  line-height: 1.45;
}

.ax-plans__cta {
  margin-top: auto;
}
</style>
