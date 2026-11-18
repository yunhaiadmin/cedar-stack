<script setup lang="ts">
/**
 * UhdMemberCard —— 会员卡（说明书 2.5；全站唯一实现）
 * 2026-11-18 按友站公开样式表真值重做静态外观（真源 reports/member-card-friend-css-20261118.md；
 * 用户双标签页对照截图亲点：大小/颜色/边框三项）：
 * - 尺寸：aspect-ratio 1.586（宽由容器定、框 max-width 425 不动）、圆角 40px（专用令牌
 *   --uhd-radius-member，不动多卡共用的 --uhd-radius-card-lg）、padding 32px（友站像素
 *   实测内容偏移左 31/上 34/下 32 的约值）。
 * - 层（自下而上）：卡面（155deg 半透底＋backdrop blur/saturate＋1px 边＋slab/face 合并
 *   阴影走 --uhd-shadow-member-l/d 令牌）→ reflection（卡面 ::before）→ sheen → foil
 *   （4px 周期白条纹＋prism 彩虹、115deg 斜带遮罩、彩虹层静态居中）→ bevel（1.5px
 *   mask 环切、145deg 渐变）。指针倾斜/glare 跟随/halo 光晕不在本单范围。
 * - prism 基参 --prism-l/--prism-c 定义在 uhd-tokens.css（深 .82/.13、浅 .66/.15）。
 * 右上彩虹三角 Logo：浅色 37×40、深色 33×39；卡名约 28px 粗体（沿用此前实测）。
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
      <div class="uhd-member__sheen" aria-hidden="true" />
      <div class="uhd-member__foil" aria-hidden="true" />
      <div class="uhd-member__bevel" aria-hidden="true" />
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
  isolation: isolate;
  overflow: hidden;
  /* 宽高比 1.586（友站 .member-card 真值）；旧固定高 228 已废 */
  aspect-ratio: 1.586;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 32px;
  background: linear-gradient(155deg, #ffffffbd 0%, #ffffff94 40%, #ffffff85 100%);
  backdrop-filter: blur(24px) saturate(160%);
  -webkit-backdrop-filter: blur(24px) saturate(160%);
  border: var(--uhd-border-w) solid #ffffff4d;
  border-radius: var(--uhd-radius-member);
  box-shadow: var(--uhd-shadow-member-l);
}
/* reflection（卡面 ::before）：105deg 微光带 */
.uhd-member__card::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: linear-gradient(105deg, transparent 0%, #ffffff24 36%, #ffffff4d 44%, #ffffff12 51%, transparent 62%);
  pointer-events: none;
}
.uhd-member__sheen {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: linear-gradient(160deg, #fffffff2 0%, #ffffff4d 22%, #fff0 42%), radial-gradient(120% 70% at 100% 100%, #ffffff73, #fff0 60%);
  pointer-events: none;
}
/* foil：4px 周期白条纹＋prism 彩虹，115deg 斜带遮罩（38%→46%–58%→66%）；
   彩虹层 background-size 200%、友站位置随指针动，静态取 50% 居中等效位 */
.uhd-member__foil {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  opacity: 0.6;
  background:
    repeating-linear-gradient(115deg, transparent 0 3px, #ffffff80 3px 4px),
    linear-gradient(115deg, oklch(calc(var(--prism-l) + .04) var(--prism-c) 62), oklch(var(--prism-l) var(--prism-c) 24), oklch(var(--prism-l) var(--prism-c) 302), oklch(var(--prism-l) var(--prism-c) 258), oklch(var(--prism-l) var(--prism-c) 232), oklch(var(--prism-l) var(--prism-c) 175));
  background-size: auto, 200% 200%;
  background-position: 0 0, 50% 50%;
  -webkit-mask-image: linear-gradient(115deg, #0000 38%, #000 46% 58%, #0000 66%);
  mask-image: linear-gradient(115deg, #0000 38%, #000 46% 58%, #0000 66%);
  pointer-events: none;
}
/* bevel：1.5px mask 环切斜光环（145deg） */
.uhd-member__bevel {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1.5px;
  background: linear-gradient(145deg, #fff 0%, #ffffff80 30%, #14161c1f 68%, #fffc 100%);
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  mask-composite: exclude;
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
  /* .ov-dark 兜底时 :root 可能是浅色档，卡内强制深色 prism 基参 */
  --prism-l: .82;
  --prism-c: .13;
  background: linear-gradient(155deg, #ffffff14 0%, #ffffff0a 30%, #ffffff08 75%, #ffffff0a 100%);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border-color: #ffffff0a;
  box-shadow: var(--uhd-shadow-member-d);
}
html:not([data-theme='light']) .uhd-member__card::before,
.ov-dark .uhd-member__card::before {
  background: linear-gradient(105deg, transparent 0%, #ffffff02 36%, #ffffff03 44%, #ffffff01 51%, transparent 62%);
}
html:not([data-theme='light']) .uhd-member__sheen,
.ov-dark .uhd-member__sheen {
  background: linear-gradient(160deg, #ffffff3d 0%, #ffffff12 24%, #fff0 44%);
}
html:not([data-theme='light']) .uhd-member__foil,
.ov-dark .uhd-member__foil {
  opacity: 0.55;
  mix-blend-mode: screen;
}
html:not([data-theme='light']) .uhd-member__bevel,
.ov-dark .uhd-member__bevel {
  background: linear-gradient(145deg, #ffffff80 0%, #ffffff1f 30%, #ffffff08 68%, #ffffff40 100%);
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
