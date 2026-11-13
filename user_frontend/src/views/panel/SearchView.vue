<script setup lang="ts">
/**
 * 搜索结果页（/search?q=，批 3 —— UHDNow 搜索结果页对应面，存档第 9 节）
 * H1「『xxx』的搜索结果」36–40px；筛选 pill（全部/电影/剧集，
 * 激活白胶囊约 65×40/圆角 20）；网格与分类页同款（7 列 228×343）。
 * 数据走现行 Emby 协议搜索口 getItems(searchTerm)，与顶栏下拉同一口径。
 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import StreamShell from '@/components/stream/StreamShell.vue'
import StreamPosterCard from '@/components/stream/StreamPosterCard.vue'
import {
  embyApi as embyProtocol,
  posterUrl,
  type EmbyItem,
} from '@/api/emby'

const route = useRoute()
const router = useRouter()

const term = computed(() => String(route.query.q || '').trim())
const filter = ref<'all' | 'movie' | 'series'>('all')
const items = ref<EmbyItem[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')

const FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'movie', label: '电影' },
  { key: 'series', label: '剧集' },
] as const

let seq = 0
async function load() {
  const q = term.value
  if (!q) {
    items.value = []
    total.value = 0
    return
  }
  const my = ++seq
  loading.value = true
  error.value = ''
  try {
    const res = await embyProtocol.getItems({
      searchTerm: q,
      includeTypes:
        filter.value === 'movie'
          ? ['Movie']
          : filter.value === 'series'
            ? ['Series']
            : ['Movie', 'Series'],
      limit: 70,
      recursive: true,
    })
    if (my !== seq) return
    items.value = res.Items || []
    total.value = res.TotalRecordCount || items.value.length
  } catch (err) {
    if (my !== seq) return
    items.value = []
    error.value = err instanceof Error ? err.message : '搜索失败'
  } finally {
    if (my === seq) loading.value = false
  }
}

function metaOf(item: EmbyItem): string {
  const parts: string[] = []
  parts.push(item.Type === 'Series' ? '剧集' : '电影')
  if (item.ProductionYear) parts.push(String(item.ProductionYear))
  if (item.Genres?.length) parts.push(item.Genres.slice(0, 2).join(' / '))
  return parts.join(' · ')
}

watch([term, filter], () => void load(), { immediate: true })
</script>

<template>
  <StreamShell>
    <div class="sv-wrap">
      <template v-if="term">
        <h1 class="sv-h1">『{{ term }}』的搜索结果</h1>
        <div class="sv-pills" role="group" aria-label="类型筛选">
          <button
            v-for="f in FILTERS"
            :key="f.key"
            type="button"
            class="sv-pill"
            :class="{ 'sv-pill--on': filter === f.key }"
            @click="filter = f.key"
          >{{ f.label }}</button>
        </div>
        <p class="sv-count">共找到 {{ total }} 条结果</p>

        <!-- 骨架屏（录屏形态）：海报灰块 + 居中站标 -->
        <div v-if="loading" class="sv-skel" role="status" aria-label="正在搜索">
          <div v-for="i in 14" :key="i" class="sv-skel__poster"><span class="sv-skel__logo">A</span></div>
        </div>
        <div v-else-if="error" class="sv-note">搜索没完成：{{ error }}</div>
        <div v-else-if="items.length === 0" class="sv-note">没有找到和『{{ term }}』相关的片子</div>
        <div v-else class="sv-grid">
          <StreamPosterCard
            v-for="item in items"
            :key="item.Id"
            size="grid"
            :title="item.Name"
            :meta="metaOf(item)"
            :rating="item.CommunityRating ?? null"
            :image="posterUrl(item, 456)"
            :to="`/media/${item.Id}`"
          />
        </div>
      </template>
      <template v-else>
        <h1 class="sv-h1">搜索</h1>
        <p class="sv-note">用顶栏的放大镜，或手机底部的搜索按钮，输入片名开始找片。</p>
        <button type="button" class="sv-back" @click="router.push('/library')">回片库首页</button>
      </template>
    </div>
  </StreamShell>
</template>

<style scoped>
.sv-wrap {
  padding: 30px clamp(16px, 5vw, 96px) 56px;
}
.sv-h1 {
  margin: 0;
  font-size: 38px;
  font-weight: var(--uhd-fw-extrabold);
  color: var(--uhd-s-text);
  letter-spacing: -0.01em;
}
.sv-pills {
  display: flex;
  gap: 8px;
  margin-top: 20px;
}
.sv-pill {
  min-width: 65px;
  height: 40px;
  padding: 0 18px;
  border-radius: 20px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--uhd-s-text-2);
  font-size: 15px;
  font-family: inherit;
  cursor: pointer;
}
.sv-pill--on {
  background: var(--uhd-s-pill-active-bg);
  color: var(--uhd-s-pill-active-text);
  font-weight: var(--uhd-fw-semibold);
  box-shadow: var(--uhd-shadow-seg);
  border-color: var(--uhd-s-pill-border);
}
.sv-count {
  margin: 14px 0 0;
  font-size: 14px;
  color: var(--uhd-s-text-2);
}
.sv-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 34px 21px;
  margin-top: 22px;
}
@media (max-width: 1599px) {
  .sv-grid { grid-template-columns: repeat(6, 1fr); }
}
@media (max-width: 1279px) {
  .sv-grid { grid-template-columns: repeat(5, 1fr); }
}
@media (max-width: 1023px) {
  .sv-grid { grid-template-columns: repeat(4, 1fr); }
}
@media (max-width: 767px) {
  .sv-grid { grid-template-columns: repeat(3, 1fr); gap: 22px 12px; }
  .sv-h1 { font-size: 30px; }
}
.sv-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 40px;
  font-size: 14px;
  color: var(--uhd-s-text-2);
}
.sv-spinner {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 2px solid var(--uhd-s-border);
  border-top-color: var(--uhd-s-text);
  animation: uhd-spin 0.8s linear infinite;
}
.sv-note {
  margin-top: 28px;
  font-size: 15px;
  color: var(--uhd-s-text-2);
}
.sv-back {
  margin-top: 18px;
  height: 44px;
  padding: 0 24px;
  border-radius: 22px;
  border: 1px solid var(--uhd-s-border);
  background: var(--uhd-s-pill-active-bg);
  color: var(--uhd-s-text);
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  font-family: inherit;
  cursor: pointer;
}

/* 骨架屏网格（与 .sv-grid 同步列数） */
.sv-skel {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 34px 21px;
  margin-top: 26px;
}
@media (max-width: 1599px) { .sv-skel { grid-template-columns: repeat(6, 1fr); } }
@media (max-width: 1279px) { .sv-skel { grid-template-columns: repeat(5, 1fr); } }
@media (max-width: 1023px) { .sv-skel { grid-template-columns: repeat(4, 1fr); } }
@media (max-width: 767px) { .sv-skel { grid-template-columns: repeat(3, 1fr); gap: 22px 12px; } }
.sv-skel__poster {
  aspect-ratio: 2 / 3;
  border-radius: 13px;
  background: var(--uhd-s-card-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: sv-pulse 1.4s ease-in-out infinite;
}
.sv-skel__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: var(--uhd-s-border);
  color: var(--uhd-s-text-2);
  font-size: 18px;
  font-weight: var(--uhd-fw-black);
}
@keyframes sv-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}
</style>
