<script setup lang="ts">
/**
 * UhdMemberCard —— 会员卡（说明书 2.5）
 * 约 425×265px、圆角 28px；白底 + 紫蓝斜向彩虹光带（白名单位置）+ 细斜纹；
 * 左上品牌名 16px 粗 + 状态小胶囊、右上彩虹三角 Logo；
 * 底部套餐名约 32px 超粗、用户名·ID 约 13px 灰；卡下另起一行状态说明。
 * 数据全部由调用方传入（订阅/资格接口），组件不写死任何数字。
 */
import UhdChip from './UhdChip.vue'

withDefaults(
  defineProps<{
    brand?: string
    planName: string
    /** 用户名 · ID 一行 */
    userLine: string
    /** 状态小胶囊文案（如「已到期」「有效」）；空则不显示 */
    statusText?: string
    /** 卡下说明行（如到期日期） */
    note?: string
  }>(),
  { brand: 'Aetrix', statusText: '', note: '' },
)
</script>

<template>
  <div class="uhd-member">
    <div class="uhd-member__card">
      <div class="uhd-member__band" aria-hidden="true" />
      <div class="uhd-member__tex" aria-hidden="true" />
      <div class="uhd-member__top">
        <span class="uhd-member__brand">
          {{ brand }}
          <UhdChip v-if="statusText" variant="member">{{ statusText }}</UhdChip>
        </span>
        <span class="uhd-member__logo" aria-hidden="true" />
      </div>
      <div class="uhd-member__bottom">
        <div class="uhd-member__plan">{{ planName }}</div>
        <div class="uhd-member__user">{{ userLine }}</div>
      </div>
    </div>
    <p v-if="note" class="uhd-member__note">{{ note }}</p>
  </div>
</template>

<style scoped>
.uhd-member {
  font-family: var(--uhd-font-sans);
  max-width: 425px;
}
.uhd-member__card {
  position: relative;
  overflow: hidden;
  min-height: 265px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 26px 28px;
  background: var(--uhd-l-surface);
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-card-lg);
  box-shadow: var(--uhd-shadow-card-l);
}
/* 斜向彩虹光带（白名单） */
.uhd-member__band {
  position: absolute;
  inset: 0;
  background: var(--uhd-rainbow-band);
  pointer-events: none;
}
/* 细斜纹 */
.uhd-member__tex {
  position: absolute;
  inset: 0;
  background: repeating-linear-gradient(115deg, rgba(17, 17, 17, 0.028) 0 1px, transparent 1px 7px);
  pointer-events: none;
}
.uhd-member__top {
  position: relative;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}
.uhd-member__brand {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-l-text);
}
/* 彩虹三角 Logo（白名单） */
.uhd-member__logo {
  width: 24px;
  height: 22px;
  background: var(--uhd-rainbow-conic);
  clip-path: polygon(50% 0, 100% 100%, 0 100%);
}
.uhd-member__bottom { position: relative; }
.uhd-member__plan {
  font-family: var(--uhd-font-display);
  font-size: var(--uhd-fs-h3-lg);
  font-weight: var(--uhd-fw-extrabold);
  color: var(--uhd-l-text);
  letter-spacing: -0.01em;
}
.uhd-member__user {
  margin-top: 6px;
  font-family: var(--uhd-font-mono);
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-l-text-2);
}
.uhd-member__note {
  margin: 12px 2px 0;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
}
</style>
