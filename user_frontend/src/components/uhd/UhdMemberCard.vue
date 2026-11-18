<script setup lang="ts">
/**
 * UhdMemberCard —— 会员卡（说明书 2.5；全站唯一实现，2026-11-18 质感第二波收敛）
 * 框约 425×265px、圆角 28px；两态各一份（友站补测裁定，见 gap 表 §5.1）：
 * - 浅色态：白底 rgb(255,255,255)＋宽阔斜向淡彩光带（核心蓝紫 rgb(160,189,242)、羽化约 60–80px）
 *   ＋全卡极细斜纹（间距 14px，落在实测 12–16px 区间）；阴影卡下 2px rgb(205,201,229)→38px 溶入页底。
 * - 深色态：紫蓝渐变底（左近黑 rgb(29,31,34)→中右靛蓝平台 rgb(110,116,153)→右近黑 rgb(22,23,27)）
 *   ＋同一条斜光带呈亮色形态＋顶部淡紫高光边 rgb(130,125,159)；阴影弱得多（2px rgb(23,23,31)→36px 至页底）。
 * 右上彩虹三角 Logo：浅色 37×40、深色 33×39；卡名约 28px 粗体。
 * 深色判定：data-theme 缺省即深（useTheme 只在浅色时写 data-theme="light"）；
 * .ov-dark 为概览页主题竞态兜底（与 PersonalCenterView 的 .ov-dark 回滚块同口径）。
 * 数据全部由调用方传入（订阅/资格接口），组件不写死任何数字。
 */
import UhdChip from './UhdChip.vue'
import BrandMark from '@/components/BrandMark.vue'

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
    /** 卡内第三行（如家庭子账号归属）；空则不显示 */
    familyText?: string
  }>(),
  { brand: 'Aetrix', statusText: '', note: '', familyText: '' },
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
        <span class="uhd-member__logo"><BrandMark /></span>
      </div>
      <div class="uhd-member__bottom">
        <div class="uhd-member__plan">{{ planName }}</div>
        <div class="uhd-member__user">{{ userLine }}</div>
        <p v-if="familyText" class="uhd-member__family">{{ familyText }}</p>
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
  background: rgb(255, 255, 255);
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-card-lg);
  /* 浅色阴影：卡下 2px 淡紫 rgb(205,201,229)→38px 溶入页底（友站补测采样） */
  box-shadow:
    0 2px 8px -2px rgb(205, 201, 229),
    0 24px 38px -20px rgba(205, 201, 229, 0.45);
}
/* 斜向淡彩光带（浅色态）：宽阔斜向、核心蓝紫 rgb(160,189,242)、羽化约 60–80px */
.uhd-member__band {
  position: absolute;
  inset: 0;
  background: linear-gradient(115deg, transparent 0%, transparent 28%, rgb(247, 245, 252) 38%, rgb(160, 189, 242) 50%, rgb(247, 245, 252) 62%, transparent 80%);
  pointer-events: none;
}
/* 细斜纹（浅色态）：间距 14px，落在友站实测 12–16px 区间 */
.uhd-member__tex {
  position: absolute;
  inset: 0;
  background: repeating-linear-gradient(115deg, rgba(17, 17, 17, 0.028) 0 1px, transparent 1px 14px);
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
/* Aetrix 品牌标（2026-11-16 定版，BrandMark 组件，A 色随当前文字色）；
   浅色 37×40、深色 33×39（友站补测实测） */
.uhd-member__logo {
  width: 37px;
  height: 40px;
  flex: none;
}
.uhd-member__bottom { position: relative; }
.uhd-member__plan {
  font-family: var(--uhd-font-display);
  font-size: 28px;
  font-weight: var(--uhd-fw-extrabold);
  color: var(--uhd-l-text);
  letter-spacing: -0.01em;
}
.uhd-member__user {
  margin-top: 6px;
  font-family: var(--uhd-font-mono);
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-l-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 卡内第三行（如家庭子账号归属），小字弱化，不与套餐名争位 */
.uhd-member__family {
  margin: 6px 0 0;
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-l-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.uhd-member__note {
  margin: 12px 2px 0;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
}

/* ---------- 深色态（data-theme 缺省即深；.ov-dark 为概览页主题竞态兜底） ---------- */
html:not([data-theme='light']) .uhd-member__card,
.ov-dark .uhd-member__card {
  background: linear-gradient(115deg, rgb(29, 31, 34) 0%, rgb(37, 38, 41) 12%, rgb(62, 62, 69) 24%, rgb(79, 74, 94) 36%, rgb(110, 116, 153) 50%, rgb(116, 117, 154) 58%, rgb(95, 120, 152) 72%, rgb(68, 90, 112) 84%, rgb(30, 35, 40) 94%, rgb(22, 23, 27) 100%);
  border-color: rgba(255, 255, 255, 0.1);
  /* 顶部淡紫高光边 rgb(130,125,159)＋弱阴影（卡下 2px rgb(23,23,31)→36px 至页底） */
  box-shadow:
    inset 0 1px 0 rgb(130, 125, 159),
    0 2px 8px -2px rgb(23, 23, 31),
    0 20px 36px -18px rgba(23, 23, 31, 0.6);
}
/* 同一条斜光带呈亮色形态 */
html:not([data-theme='light']) .uhd-member__band,
.ov-dark .uhd-member__band {
  background: linear-gradient(115deg, transparent 0%, transparent 28%, rgba(134, 134, 153, 0.5) 40%, rgba(110, 116, 153, 0.7) 52%, rgba(134, 134, 153, 0.5) 64%, transparent 80%);
}
html:not([data-theme='light']) .uhd-member__tex,
.ov-dark .uhd-member__tex {
  background: repeating-linear-gradient(115deg, rgba(255, 255, 255, 0.05) 0 1px, transparent 1px 14px);
}
html:not([data-theme='light']) .uhd-member__brand,
.ov-dark .uhd-member__brand,
html:not([data-theme='light']) .uhd-member__plan,
.ov-dark .uhd-member__plan {
  color: #ffffff;
}
html:not([data-theme='light']) .uhd-member__logo,
.ov-dark .uhd-member__logo {
  width: 33px;
  height: 39px;
  color: #ffffff;
}
html:not([data-theme='light']) .uhd-member__user,
.ov-dark .uhd-member__user,
html:not([data-theme='light']) .uhd-member__family,
.ov-dark .uhd-member__family,
html:not([data-theme='light']) .uhd-member__note,
.ov-dark .uhd-member__note {
  color: rgba(255, 255, 255, 0.55);
}
</style>
