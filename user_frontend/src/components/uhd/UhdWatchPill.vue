<script setup lang="ts">
/**
 * UhdWatchPill —— 「前往流媒体」常驻入口胶囊（全站唯一一份实现）
 *
 * 质感真源：友站 .pill-primary／.btn-glass 逐字值（2026-11-18 抓取，
 * 见 reports/member-card-friend-css-20261118.md 附节）：玻璃 lens 底
 * （blur 2px saturate 160%＋sheen/edge/shadow 三件套）；彩虹环是
 * conic 弧段（prism 环，透明缺口＋1.75px mask 环切、inset -1px），
 * 环外同 conic 的 blur 5px 光晕；hover 环转 32deg、active scale .97。
 * 尺寸不走友站大钮：#321 曾照 .btn-glass 放大到高 44／字 15，用户
 * 2026-11-18 亲点「变大了，收回去」——收回改前配方：高 33 由
 * --utb-pill-h 钉死、字 13、padding-inline 13（--utb-pill-px）。
 * 落点 /library：网页播放器没有独立入口页（/play/:id 须带具体条目），
 * 片库首页是网页点播的起点，且与此前概览/我的/邀请/记录四处既有
 * 入口的落点口径一致。
 * 尺寸/填充/玻璃令牌见 styles/uhd-topbar.css（--utb-*），不许在别处
 * 再写第二份观影钮样式（此前全站曾有四份不同写法，本组件即收口）。
 */
import { RouterLink } from 'vue-router'
</script>

<template>
  <RouterLink to="/library" class="utb-pill" aria-label="前往流媒体：前往片库">
    <svg class="utb-pill__icon" width="12" height="12" viewBox="0 0 24 24" aria-hidden="true">
      <defs>
        <linearGradient id="utb-pill-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#2f8fe0" />
          <stop offset="0.14" stop-color="#6f7ad8" />
          <stop offset="0.22" stop-color="#c46c90" />
          <stop offset="0.28" stop-color="#e8766f" />
          <stop offset="0.36" stop-color="#f2a45a" />
          <stop offset="0.46" stop-color="#ecd04f" />
          <stop offset="0.57" stop-color="#95c77d" />
          <stop offset="0.75" stop-color="#39b3ec" />
          <stop offset="1" stop-color="#2f8fe0" />
        </linearGradient>
      </defs>
      <path d="M7 4.2v15.6c0 .8.9 1.3 1.6.9l11.6-7.8c.6-.4.6-1.3 0-1.7L8.6 3.3C7.9 2.9 7 3.4 7 4.2Z" fill="url(#utb-pill-g)" />
    </svg>
    <span>前往流媒体</span>
  </RouterLink>
</template>

<style scoped>
/* 友站 .pill-primary 配方：lens 玻璃底（--utb-pill-fill＋blur 2px
   saturate 160%）＋box-shadow 三件套（sheen 内高光／edge 描边环／
   shadow 柔影）；彩虹环走 conic 弧段（prism 色环带透明缺口），
   ::before 1.75px mask 环切、::after 同渐变 blur 5px 作环外光晕。
   图标三角的色标按友站 --brand-play-gradient 的 conic 色序校准
   （与 --uhd-rainbow-fill 同停点），图标本体不换。 */
.utb-pill {
  position: relative;
  z-index: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  /* 中和全局 mobile.css 的 a{min-height:44px}：胶囊高由 --utb-pill-h
     令牌钉死（33），不许外力改高 */
  min-height: 0;
  height: var(--utb-pill-h);
  padding: 0 var(--utb-pill-px);
  border: 1px solid transparent;
  border-radius: 999px;
  background: var(--utb-pill-fill);
  backdrop-filter: var(--utb-pill-filter);
  -webkit-backdrop-filter: var(--utb-pill-filter);
  box-shadow:
    var(--utb-pill-sheen),
    var(--utb-pill-edge),
    var(--utb-pill-shadow);
  color: var(--utb-pill-fg);
  font-size: 13px;
  font-weight: var(--uhd-fw-medium);
  letter-spacing: -0.025em;
  line-height: 19px;
  font-family: var(--uhd-font-sans);
  text-decoration: none;
  white-space: nowrap;
  flex: none;
  --utb-prism-angle: 0deg;
  transition:
    transform var(--uhd-press-dur) var(--uhd-ease-standard, ease),
    background-color .3s ease,
    --utb-prism-angle .5s ease;
}
/* 彩虹环（prism conic 弧段）：1.75px mask 环切、inset -1px 压住边线。
   渐变全文在 ::before／::after 各写一份（友站同款两处引用同一渐变）。 */
.utb-pill::before {
  content: "";
  position: absolute;
  inset: -1px;
  border-radius: inherit;
  padding: 1.75px;
  background: conic-gradient(from calc(var(--utb-prism-angle) + 225deg),
    transparent 0deg,
    oklch(var(--utb-prism-l) var(--utb-prism-c) 25) 22deg,
    oklch(var(--utb-prism-l) var(--utb-prism-c) 70) 40deg,
    oklch(calc(var(--utb-prism-l) + .06) var(--utb-prism-c) 105) 58deg,
    oklch(var(--utb-prism-l) var(--utb-prism-c) 155) 76deg,
    oklch(var(--utb-prism-l) var(--utb-prism-c) 205) 94deg,
    oklch(calc(var(--utb-prism-l) - .04) var(--utb-prism-c) 265) 112deg,
    oklch(calc(var(--utb-prism-l) - .04) var(--utb-prism-c) 305) 128deg,
    transparent 152deg,
    transparent 196deg,
    oklch(var(--utb-prism-l) var(--utb-prism-c) 205 / .4) 226deg,
    oklch(calc(var(--utb-prism-l) - .04) var(--utb-prism-c) 305 / .4) 254deg,
    transparent 290deg);
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  mask-composite: exclude;
  pointer-events: none;
}
/* 环外光晕：同一 conic 渐变 blur 5px，藏在胶囊身后（z-index -1） */
.utb-pill::after {
  content: "";
  position: absolute;
  inset: 0;
  z-index: -1;
  border-radius: inherit;
  background: conic-gradient(from calc(var(--utb-prism-angle) + 225deg),
    transparent 0deg,
    oklch(var(--utb-prism-l) var(--utb-prism-c) 25) 22deg,
    oklch(var(--utb-prism-l) var(--utb-prism-c) 70) 40deg,
    oklch(calc(var(--utb-prism-l) + .06) var(--utb-prism-c) 105) 58deg,
    oklch(var(--utb-prism-l) var(--utb-prism-c) 155) 76deg,
    oklch(var(--utb-prism-l) var(--utb-prism-c) 205) 94deg,
    oklch(calc(var(--utb-prism-l) - .04) var(--utb-prism-c) 265) 112deg,
    oklch(calc(var(--utb-prism-l) - .04) var(--utb-prism-c) 305) 128deg,
    transparent 152deg,
    transparent 196deg,
    oklch(var(--utb-prism-l) var(--utb-prism-c) 205 / .4) 226deg,
    oklch(calc(var(--utb-prism-l) - .04) var(--utb-prism-c) 305 / .4) 254deg,
    transparent 290deg);
  filter: blur(5px);
  opacity: var(--utb-prism-glow);
  transition: opacity .3s ease;
  pointer-events: none;
}
.utb-pill:hover {
  background: var(--utb-pill-fill-hover);
  --utb-prism-angle: 32deg;
}
.utb-pill:hover::after {
  opacity: calc(var(--utb-prism-glow) * 1.8);
}
.utb-pill:active {
  transform: scale(var(--uhd-press-scale));
}
.utb-pill__icon {
  flex: none;
}
</style>
