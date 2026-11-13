<script setup lang="ts">
/**
 * StreamRow —— 海报横滑行（批 3，存档第 4 节）
 * 行标题 28–30px 粗 + 计数 14px 灰；右侧圆形「下一组」箭头直径 48、
 * 白色半透明毛玻璃（深色转深色毛玻璃 + 白箭头，随令牌走）。
 * 行内横滑：海报列间距 24–26。
 */
import { ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ChevronRight } from 'lucide-vue-next'

defineProps<{
  title: string
  count?: number | null
  countLabel?: string
  /** 「查看全部」目标路由（手机端文字链；没有真实目标页就不传、不渲染） */
  moreTo?: string
}>()

const trackEl = ref<HTMLElement | null>(null)

function nextGroup() {
  const el = trackEl.value
  if (!el) return
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  el.scrollBy({ left: Math.max(320, el.clientWidth * 0.85), behavior: reduce ? 'auto' : 'smooth' })
}
</script>

<template>
  <section class="sr-row">
    <div class="sr-head">
      <h2 class="sr-title">
        {{ title }}
        <span v-if="count !== null && count !== undefined" class="sr-count">{{ count }}{{ countLabel || '部' }}</span>
      </h2>
      <!-- 桌面：圆形「下一组」48（存档数值）；手机：录屏形态「查看全部 →」
           文字链（有真实目标页才渲染，moreTo 由父级按行数据给） -->
      <RouterLink v-if="moreTo" :to="moreTo" class="sr-more">查看全部 →</RouterLink>
      <button type="button" class="sr-next" aria-label="下一组" @click="nextGroup">
        <ChevronRight :size="18" />
      </button>
    </div>
    <div ref="trackEl" class="sr-track">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.sr-row {
  margin-top: 44px;
}
.sr-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 clamp(16px, 5vw, 96px);
}
.sr-title {
  margin: 0;
  font-size: 29px;
  font-weight: var(--uhd-fw-extrabold);
  color: var(--uhd-s-text);
  letter-spacing: -0.01em;
}
.sr-count {
  margin-left: 10px;
  font-size: 14px;
  font-weight: var(--uhd-fw-normal);
  color: var(--uhd-s-text-2);
}
/* 「查看全部 →」：仅手机端显示（录屏形态）；桌面用圆形下一组钮 */
.sr-more {
  display: none;
  flex: none;
  font-size: 14px;
  color: var(--uhd-s-text-2);
  text-decoration: none;
}
.sr-next {
  flex: none;
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
.sr-track {
  display: flex;
  gap: 25px;
  margin-top: 18px;
  padding: 0 clamp(16px, 5vw, 96px) 8px;
  overflow-x: auto;
  overscroll-behavior-x: contain;
  scroll-snap-type: x proximity;
  scrollbar-width: none;
}
.sr-track::-webkit-scrollbar {
  display: none;
}
@media (max-width: 767px) {
  .sr-row {
    margin-top: 32px;
  }
  .sr-title {
    font-size: 22px;
  }
  .sr-track {
    gap: 14px;
  }
  .sr-next {
    display: none;
  }
  .sr-more {
    display: inline-block;
  }
}
</style>
