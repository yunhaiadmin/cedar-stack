<script setup lang="ts">
/**
 * StreamGuessRow —— 「猜你想看」推荐区（搜索浮层空输入态）
 * 标题 + 海报卡横排（卡宽 84、海报 2:3），点卡进详情页。
 * 数据由 useGuessRecommendations 提供（现行三路只读信号合的 5 条），
 * 本组件只负责渲染；select 事件让父级（顶栏面板/手机全屏层）收起浮层。
 */
import { RouterLink } from 'vue-router'
import { Clapperboard } from 'lucide-vue-next'
import type { GuessCard } from '@/composables/useGuessRecommendations'

defineProps<{ items: GuessCard[] }>()
const emit = defineEmits<{ select: [] }>()
</script>

<template>
  <section class="sg" aria-label="猜你想看">
    <p class="sg__title">猜你想看</p>
    <div class="sg__row">
      <RouterLink
        v-for="g in items"
        :key="g.id"
        class="sg__card"
        :to="{ name: 'media-detail', params: { id: g.id } }"
        @click="emit('select')"
      >
        <span class="sg__poster">
          <img v-if="g.image" :src="g.image" alt="" loading="lazy" />
          <Clapperboard v-else :size="20" aria-hidden="true" />
        </span>
        <span class="sg__name">{{ g.title }}</span>
        <span v-if="g.meta" class="sg__meta">{{ g.meta }}</span>
      </RouterLink>
    </div>
  </section>
</template>

<style scoped>
.sg__title {
  margin: 0;
  padding: 0 2px 10px;
  font-size: 14px;
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-s-text);
}
/* 横排：桌面面板内宽 460 = 5×84 + 4×10 正好放满；窄屏横滑 */
.sg__row {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  scrollbar-width: none;
}
.sg__row::-webkit-scrollbar {
  display: none;
}
.sg__card {
  flex: none;
  width: 84px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-decoration: none;
  color: var(--uhd-s-text);
}
.sg__poster {
  width: 84px;
  height: 126px;
  border-radius: 8px;
  overflow: hidden;
  background: var(--uhd-s-card-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--uhd-s-text-2);
}
.sg__poster img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.sg__name {
  font-size: 13px;
  font-weight: var(--uhd-fw-medium);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sg__meta {
  font-size: 12px;
  color: var(--uhd-s-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
