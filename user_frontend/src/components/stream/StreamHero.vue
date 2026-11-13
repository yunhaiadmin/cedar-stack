<script setup lang="ts">
/**
 * StreamHero —— 片库首页 Hero（批 3，存档第 2 节）
 * 全幅剧照、高约 880（窄屏按视口收）、底部约 65% 起向页面底渐隐；
 * 眉题「首页推荐 · 0X/05」13px 大字距；元信息 15px、评分粗体；
 * 「▶ 立即播放」白胶囊 145×44–45/圆角 22 + 约 2px 彩虹边框（白名单位置），
 * 深色版自动转黑底毛玻璃 + 白字（令牌 --uhd-s-hero-play-* 不另设，
 * 由 [data-stream-theme] 下的覆盖规则处理）；右下双圆箭头 48 + 5 段式指示 28×4。
 * Aetrix 无片名 Logo 字标图数据，标题用文字 H1（数据驱动，不搬对方素材）。
 * 「立即播放」沿发现页现行口径：打开 PlayerSchemeSheet 选播放器，
 * 不做网页播放。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ChevronLeft, ChevronRight } from 'lucide-vue-next'

export interface StreamHeroItem {
  id: string
  title: string
  meta: string
  rating?: number | null
  backdrop: string
  /** 海报地址：背景图请求失败时的二段兜底（展示层容错，不改数据口径） */
  poster?: string
  overview?: string
}

const props = defineProps<{ items: StreamHeroItem[] }>()
const emit = defineEmits<{ play: [item: StreamHeroItem] }>()

const index = ref(0)
const current = computed(() => props.items[index.value] || null)
let timer = 0

function go(i: number) {
  const n = props.items.length
  if (!n) return
  index.value = ((i % n) + n) % n
  restart()
}
function restart() {
  window.clearInterval(timer)
  if (props.items.length > 1) {
    timer = window.setInterval(() => {
      index.value = (index.value + 1) % props.items.length
    }, 6000)
  }
}
watch(() => props.items.length, restart)
onMounted(restart)
onBeforeUnmount(() => window.clearInterval(timer))

/* ---------- 触摸左右滑切换（友站口径） ----------
 * 只认横滑：横向位移 ≥48px 且 ≥纵向位移 1.6 倍（角度判定），
 * 竖滑翻页不误切；监听 passive、不拦截纵向滚动。
 * 切换走 go()：序号/标题/信息行/分段进度随 index 同步，自动轮播计时重置。
 * （友站滑动后是否续播未实测到，按重置计时处理，已在 PR 回报标注。） */
let touchX = 0
let touchY = 0
let touching = false
function onTouchStart(e: TouchEvent) {
  const t = e.changedTouches[0]
  if (!t) return
  touchX = t.clientX
  touchY = t.clientY
  touching = true
}
function onTouchEnd(e: TouchEvent) {
  if (!touching) return
  touching = false
  const t = e.changedTouches[0]
  if (!t) return
  const dx = t.clientX - touchX
  const dy = t.clientY - touchY
  if (Math.abs(dx) >= 48 && Math.abs(dx) >= Math.abs(dy) * 1.6) {
    go(dx < 0 ? index.value + 1 : index.value - 1)
  }
}

/* ---------- 背景图二段兜底 ----------
 * 背景图请求失败（个别条目图源 404 等）先换海报，海报再失败才落空态，
 * 不留「大片空白」无声失败；切张自动复位。 */
const imgStage = ref(0)
watch(index, () => {
  imgStage.value = 0
})
const bgSrc = computed(() => {
  const c = current.value
  if (!c) return ''
  if (imgStage.value === 0) return c.backdrop
  if (imgStage.value === 1) return c.poster && c.poster !== c.backdrop ? c.poster : ''
  return ''
})
function onBgError() {
  imgStage.value += 1
}
</script>

<template>
  <section
    v-if="current"
    class="sh-hero"
    aria-label="首页推荐"
    @touchstart.passive="onTouchStart"
    @touchend.passive="onTouchEnd"
  >
    <Transition name="sh-fade" mode="out-in">
      <img
        v-if="bgSrc"
        :key="current.id"
        :src="bgSrc"
        alt=""
        class="sh-bg"
        @error="onBgError"
      />
      <div v-else :key="`${current.id}-empty`" class="sh-bg sh-bg--empty"></div>
    </Transition>
    <div class="sh-fade-bottom" aria-hidden="true"></div>
    <div class="sh-content">
      <p class="sh-eyebrow">首页推荐 · {{ String(index + 1).padStart(2, '0') }}/{{ String(items.length).padStart(2, '0') }}</p>
      <h1 class="sh-title">{{ current.title }}</h1>
      <p class="sh-meta">
        {{ current.meta }}<b v-if="current.rating" class="sh-rating"> · {{ current.rating.toFixed(1) }}</b>
      </p>
      <p v-if="current.overview" class="sh-overview">{{ current.overview }}</p>
      <button type="button" class="sh-play" @click="emit('play', current)">
        <!-- 纯色实心三角 SVG（currentColor 随按钮字色：浅色黑/深色白）。
             原先用 ▶ 字符，移动端被系统当 emoji 渲染成蓝色圆角方块，已换掉 -->
        <svg class="sh-play__tri" width="11" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M2 1.3v9.4c0 .8.9 1.3 1.6.9l7.2-4.7c.6-.4.6-1.4 0-1.8L3.6.4C2.9-.1 2 .5 2 1.3Z" fill="currentColor" />
        </svg>
        立即播放
      </button>
    </div>
    <div v-if="items.length > 1" class="sh-controls">
      <div class="sh-arrows">
        <button type="button" class="sh-arrow" aria-label="上一张" @click="go(index - 1)">
          <ChevronLeft :size="16" />
        </button>
        <button type="button" class="sh-arrow" aria-label="下一张" @click="go(index + 1)">
          <ChevronRight :size="16" />
        </button>
      </div>
      <div class="sh-dots" aria-hidden="true">
        <span
          v-for="(item, i) in items"
          :key="item.id"
          class="sh-dot"
          :class="{ 'sh-dot--on': i === index }"
        >
          <!-- 激活段带进度填充（用户真机截图实证），6s 与轮播间隔同步，切张重播 -->
          <span v-if="i === index" :key="index" class="sh-dot__fill"></span>
        </span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.sh-hero {
  position: relative;
  height: min(880px, 92vh);
  min-height: 540px;
  margin-top: -60px; /* 顶栏悬浮在 Hero 之上（顶栏高 46 + 上距 14） */
  overflow: hidden;
}
.sh-bg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.sh-bg--empty {
  background: var(--uhd-s-card-soft);
}
/* 底部渐隐：约 65% 高度起向页面底色渐隐（深色终点随令牌转近黑） */
.sh-fade-bottom {
  position: absolute;
  inset: 0;
  background: linear-gradient(to bottom, transparent 55%, var(--uhd-s-hero-fade) 96%);
}
.sh-content {
  position: absolute;
  left: clamp(16px, 5vw, 96px);
  bottom: 120px;
  max-width: 640px;
  z-index: 2;
}
.sh-eyebrow {
  margin: 0 0 14px;
  font-size: 13px;
  letter-spacing: 4px;
  color: var(--uhd-s-text-2);
  font-weight: var(--uhd-fw-semibold);
}
.sh-title {
  margin: 0;
  font-size: clamp(40px, 4.6vw, 72px);
  font-weight: var(--uhd-fw-black);
  line-height: 1.08;
  letter-spacing: -0.01em;
  color: var(--uhd-s-text);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.sh-meta {
  margin: 16px 0 0;
  font-size: 15px;
  color: var(--uhd-s-text-2);
}
.sh-rating {
  color: var(--uhd-s-text);
}
.sh-overview {
  margin: 10px 0 0;
  font-size: 15px;
  line-height: 1.65;
  color: var(--uhd-s-text-2);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
/* 「▶ 立即播放」：145×44–45、圆角 22、白底 + 约 2px 彩虹边框、黑 15px 粗字 */
.sh-play {
  margin-top: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 145px;
  height: 45px;
  border-radius: 22px;
  border: 2px solid transparent;
  /* 浅色版（2026-11-13 补齐七彩＋玻璃）：淡彩粉彩底 + 玻璃栈（内高光 + 柔影 + backdrop，压在 Hero 图上验透感） */
  background:
    var(--uhd-rainbow-soft) padding-box,
    var(--uhd-rainbow) border-box;
  color: var(--uhd-s-pill-active-text);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.65),
    var(--uhd-s-shadow-pill);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
  font-size: 15px;
  font-weight: var(--uhd-fw-bold);
  font-family: inherit;
  cursor: pointer;
}
.sh-play__tri {
  display: block;
  flex: none;
}
/* 深色版：黑底毛玻璃 + 同一彩虹边框 + 白字 + 玻璃栈（内高光 + 柔影，尺寸不变） */
.stream-scope[data-stream-theme="dark"] .sh-play {
  background:
    linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box,
    var(--uhd-rainbow) border-box;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.22),
    var(--uhd-s-shadow-pill);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
}
.sh-controls {
  position: absolute;
  right: clamp(16px, 5vw, 96px);
  bottom: 120px;
  z-index: 2;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 16px;
}
.sh-arrows {
  display: flex;
  gap: 8px;
}
.sh-arrow {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  border: 1px solid var(--uhd-s-pill-border);
  background: var(--uhd-s-pill-bg);
  color: var(--uhd-s-text);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  cursor: pointer;
}
.sh-dots {
  display: flex;
  gap: 8px;
}
.sh-dot {
  position: relative;
  overflow: hidden;
  width: 28px;
  height: 4px;
  border-radius: 2px;
  background: var(--uhd-s-text-2);
  opacity: 0.35;
}
/* 激活段：底色作轨道，填充自左向右 6s（与自动轮播间隔一致） */
.sh-dot--on {
  opacity: 1;
}
.sh-dot__fill {
  position: absolute;
  inset: 0;
  border-radius: 2px;
  background: var(--uhd-s-text);
  transform-origin: left center;
  animation: sh-fill 6s linear forwards;
}
@keyframes sh-fill {
  from { transform: scaleX(0); }
  to { transform: scaleX(1); }
}
@media (prefers-reduced-motion: reduce) {
  .sh-dot__fill {
    animation: none;
  }
}
.sh-fade-enter-active,
.sh-fade-leave-active {
  transition: opacity 0.5s var(--uhd-ease-standard);
}
.sh-fade-enter-from,
.sh-fade-leave-to {
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
  .sh-fade-enter-active,
  .sh-fade-leave-active {
    transition: none;
  }
}
@media (max-width: 767px) {
  /* D2-04：原站手机 Hero 约占上半屏、无简介（录屏 8c） */
  .sh-hero {
    height: 54vh;
    min-height: 400px;
    margin-top: 0;
  }
  .sh-content {
    bottom: 84px;
    right: 16px;
  }
  .sh-overview {
    display: none;
  }
  /* D2-03：手机播放钮约 110×38（QA 估约 115×36、录屏帧定位约 98 宽，取中值）；
     分段指示保留并与播放钮同行靠右（录屏帧实证）、双圆箭头隐藏 */
  .sh-play {
    width: 110px;
    height: 38px;
    border-radius: 19px;
    font-size: 13.5px;
    margin-top: 16px;
  }
  .sh-arrow {
    display: none;
  }
  .sh-controls {
    /* 与播放钮（内容底 84 + 高 38）垂直居中对齐 */
    bottom: 100px;
    right: 16px;
  }
  .sh-dots {
    flex-direction: row;
  }
}
</style>
