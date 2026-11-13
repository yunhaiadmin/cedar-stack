<script setup lang="ts">
/**
 * StreamPosterCard —— 片库海报卡（批 3）
 * 实测口径（存档第 4 节）：海报 2:3、圆角 12–14；标题 17–18px 粗；
 * 元信息 14px 灰、评分黑色粗体。首页海报无任何角标、悬停无浮层——
 * 本组件不渲染角标、不做悬停浮层（收藏星钮是收藏页的业务控件，
 * 由收藏页自己叠加，不在本组件内）。
 */
import { RouterLink } from 'vue-router'
import { Clapperboard } from 'lucide-vue-next'

withDefaults(
  defineProps<{
    title: string
    /** 元信息左段（年份 · 类型…），评分单独走 rating 以便粗体 */
    meta?: string
    rating?: number | null
    image?: string
    to: string
    /** 横滑行大卡 260 宽 / 网格卡 228 宽，两档共用同一模具 */
    size?: 'row' | 'grid'
  }>(),
  { meta: '', rating: null, image: '', size: 'row' },
)
</script>

<template>
  <RouterLink class="sp-card" :class="`sp-card--${size}`" :to="to">
    <span class="sp-poster">
      <img v-if="image" :src="image" alt="" loading="lazy" decoding="async" class="sp-img" />
      <Clapperboard v-else :size="30" :stroke-width="1.5" class="sp-fallback" aria-hidden="true" />
    </span>
    <span class="sp-title" :title="title">{{ title }}</span>
    <span class="sp-meta">
      <template v-if="meta">{{ meta }}</template>
      <b v-if="rating" class="sp-rating">{{ meta ? ' · ' : '' }}★ {{ rating.toFixed(1) }}</b>
    </span>
  </RouterLink>
</template>

<style scoped>
.sp-card {
  display: block;
  text-decoration: none;
  color: var(--uhd-s-text);
  min-width: 0;
}
.sp-card--row {
  flex: 0 0 260px;
  width: 260px;
}
.sp-card--grid {
  width: 100%;
}
.sp-poster {
  display: flex;
  align-items: center;
  justify-content: center;
  aspect-ratio: 2 / 3;
  border-radius: 13px;
  overflow: hidden;
  background: var(--uhd-s-card-soft);
}
.sp-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.sp-fallback {
  color: var(--uhd-s-text-2);
}
.sp-title {
  display: block;
  margin-top: 14px;
  font-size: 17px;
  font-weight: var(--uhd-fw-bold);
  line-height: 1.3;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sp-meta {
  display: block;
  margin-top: 4px;
  font-size: 14px;
  color: var(--uhd-s-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sp-rating {
  color: var(--uhd-s-text);
  font-weight: var(--uhd-fw-bold);
}
@media (max-width: 767px) {
  .sp-card--row {
    /* 录屏实证：手机横滑约 2.2 张露头（390 宽、左右 20、间距 14 → 卡宽约 152） */
    flex-basis: 152px;
    width: 152px;
  }
  .sp-title {
    font-size: 14px;
    margin-top: 10px;
  }
  .sp-meta {
    font-size: 12px;
  }
}
</style>
