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
</script>

<template>
  <section v-if="current" class="sh-hero" aria-label="首页推荐">
    <Transition name="sh-fade" mode="out-in">
      <img
        v-if="current.backdrop"
        :key="current.id"
        :src="current.backdrop"
        alt=""
        class="sh-bg"
      />
      <div v-else :key="current.id" class="sh-bg sh-bg--empty"></div>
    </Transition>
    <div class="sh-fade-bottom" aria-hidden="true"></div>
    <div class="sh-content">
      <p class="sh-eyebrow">首页推荐 · {{ String(index + 1).padStart(2, '0') }}/{{ String(items.length).padStart(2, '0') }}</p>
      <h1 class="sh-title">{{ current.title }}</h1>
      <p class="sh-meta">
        {{ current.meta }}<b v-if="current.rating" class="sh-rating"> · ★ {{ current.rating.toFixed(1) }}</b>
      </p>
      <p v-if="current.overview" class="sh-overview">{{ current.overview }}</p>
      <button type="button" class="sh-play" @click="emit('play', current)">
        <span class="sh-play__tri" aria-hidden="true">▶</span> 立即播放
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
        ></span>
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
  background:
    linear-gradient(var(--uhd-s-pill-active-bg), var(--uhd-s-pill-active-bg)) padding-box,
    var(--uhd-rainbow) border-box;
  color: var(--uhd-s-pill-active-text);
  font-size: 15px;
  font-weight: var(--uhd-fw-bold);
  font-family: inherit;
  cursor: pointer;
}
.sh-play__tri {
  font-size: 11px;
}
/* 深色版：黑底毛玻璃 + 同一彩虹边框 + 白字（尺寸不变） */
.stream-scope[data-stream-theme="dark"] .sh-play {
  background:
    linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box,
    var(--uhd-rainbow) border-box;
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
  width: 28px;
  height: 4px;
  border-radius: 2px;
  background: var(--uhd-s-text-2);
  opacity: 0.35;
}
.sh-dot--on {
  background: var(--uhd-s-text);
  opacity: 1;
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
