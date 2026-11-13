<script setup lang="ts">
/**
 * 片库分类页（/library/movies、/library/series，批 3 —— UHDNow 分类页对应面）
 * 实测骨架（存档第 10 节）：无 Hero；眉题「媒体库」14px 粗；H1 56–60px 超粗；
 * 子分类 pill 行（激活 = 白胶囊高 40/圆角 20 + 阴影）；计数行 14px 灰；
 * 网格 7 列、海报 228×343、列间距 20–22；下方「加载更多」。
 * 子分类 = 现行筛选口 getFilters 的类型（Genres），计数随筛选结果走，
 * 全部数据驱动、不写死分类名与数字。排序胶囊：最近添加 / 名称 / 年份。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import StreamShell from '@/components/stream/StreamShell.vue'
import StreamPosterCard from '@/components/stream/StreamPosterCard.vue'
import {
  embyApi as embyProtocol,
  posterUrl,
  type EmbyItem,
} from '@/api/emby'

const route = useRoute()
const kind = computed<'movie' | 'series'>(() =>
  route.path.endsWith('/series') ? 'series' : 'movie',
)
const heading = computed(() => (kind.value === 'series' ? '剧集' : '电影'))
const includeTypes = computed(() => (kind.value === 'series' ? ['Series'] : ['Movie']))

const PAGE = 49
const items = ref<EmbyItem[]>([])
const total = ref(0)
const hasMore = ref(false)
const loading = ref(false)
const loadingMore = ref(false)
const error = ref('')
const genres = ref<string[]>([])
const activeGenre = ref('')
const sort = ref<'recent' | 'name' | 'year'>('recent')

const SORTS = [
  { key: 'recent', label: '最近添加' },
  { key: 'name', label: '名称' },
  { key: 'year', label: '年份' },
] as const

function sortParams() {
  if (sort.value === 'name') return { sortBy: 'SortName', sortOrder: 'Ascending' as const }
  if (sort.value === 'year') return { sortBy: 'ProductionYear,SortName', sortOrder: 'Descending' as const }
  return { sortBy: 'DateCreated', sortOrder: 'Descending' as const }
}

let seq = 0
async function load(reset = true) {
  const my = ++seq
  if (reset) {
    loading.value = true
    error.value = ''
  } else {
    loadingMore.value = true
  }
  try {
    const res = await embyProtocol.getItems({
      includeTypes: includeTypes.value,
      genres: activeGenre.value ? [activeGenre.value] : undefined,
      startIndex: reset ? 0 : items.value.length,
      limit: PAGE,
      ...sortParams(),
    })
    if (my !== seq) return
    const page = res.Items || []
    items.value = reset ? page : [...items.value, ...page]
    total.value = res.TotalRecordCount || items.value.length
    hasMore.value =
      res.HasMore !== undefined ? !!res.HasMore : items.value.length < total.value
  } catch (err) {
    if (my !== seq) return
    if (reset) {
      items.value = []
      error.value = err instanceof Error ? err.message : '加载失败'
    }
  } finally {
    if (my === seq) {
      loading.value = false
      loadingMore.value = false
    }
  }
}

async function loadGenres() {
  try {
    const f = await embyProtocol.getFilters()
    genres.value = f.Genres || []
  } catch {
    genres.value = []
  }
}

function pickGenre(g: string) {
  activeGenre.value = g
  void load(true)
}
function pickSort(s: typeof sort.value) {
  sort.value = s
  void load(true)
}

function metaOf(item: EmbyItem): string {
  const parts: string[] = []
  if (item.ProductionYear) parts.push(String(item.ProductionYear))
  if (item.Genres?.length) parts.push(item.Genres.slice(0, 2).join(' / '))
  if (item.RunTimeTicks) {
    const mins = Math.round(item.RunTimeTicks / 600_000_000)
    if (mins > 0) parts.push(mins >= 60 ? `${Math.floor(mins / 60)}小时${mins % 60}分` : `${mins}分钟`)
  }
  return parts.join(' · ')
}

watch(kind, () => {
  activeGenre.value = ''
  void load(true)
})
onMounted(() => {
  void loadGenres()
  void load(true)
})
</script>

<template>
  <StreamShell>
    <div class="lb-wrap">
      <p class="lb-eyebrow">媒体库</p>
      <div class="lb-head">
        <h1 class="lb-h1">{{ heading }}</h1>
        <div class="lb-sort" role="group" aria-label="排序">
          <button
            v-for="s in SORTS"
            :key="s.key"
            type="button"
            class="lb-sort__btn"
            :class="{ 'lb-sort__btn--on': sort === s.key }"
            @click="pickSort(s.key)"
          >{{ s.label }}</button>
        </div>
      </div>

      <!-- 子分类 pill 行：激活白胶囊 + 阴影（深色转深灰填充 + 白字，随令牌走） -->
      <div class="lb-pills" role="group" aria-label="子分类">
        <button
          type="button"
          class="lb-pill"
          :class="{ 'lb-pill--on': activeGenre === '' }"
          @click="pickGenre('')"
        >全部</button>
        <button
          v-for="g in genres"
          :key="g"
          type="button"
          class="lb-pill"
          :class="{ 'lb-pill--on': activeGenre === g }"
          @click="pickGenre(g)"
        >{{ g }}</button>
      </div>

      <p class="lb-count">共 {{ total }} 部{{ heading }}</p>

      <!-- 骨架屏（录屏形态）：海报灰块 + 居中站标，网格与真实网格同列数 -->
      <div v-if="loading" class="lb-skel" role="status" aria-label="正在加载">
        <div v-for="i in 14" :key="i" class="lb-skel__poster"><span class="lb-skel__logo">A</span></div>
      </div>
      <div v-else-if="error" class="lb-error">
        {{ heading }}没加载出来：{{ error }}<br />
        <button type="button" class="lb-retry" @click="load(true)">重试</button>
      </div>
      <div v-else-if="items.length === 0" class="lb-empty">这个分类下暂时没有{{ heading }}</div>
      <template v-else>
        <div class="lb-grid">
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
        <div v-if="hasMore" class="lb-more">
          <button type="button" class="lb-more__btn" :disabled="loadingMore" @click="load(false)">
            {{ loadingMore ? '正在加载…' : '加载更多' }}
          </button>
        </div>
      </template>
    </div>
  </StreamShell>
</template>

<style scoped>
.lb-wrap {
  padding: 26px clamp(16px, 5vw, 96px) 56px;
}
.lb-eyebrow {
  margin: 0;
  font-size: 14px;
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-s-text-2);
}
.lb-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.lb-h1 {
  margin: 8px 0 0;
  font-size: 58px;
  font-weight: var(--uhd-fw-black);
  line-height: 1.05;
  letter-spacing: -0.01em;
  color: var(--uhd-s-text);
}
.lb-sort {
  display: flex;
  gap: 6px;
}
.lb-sort__btn {
  height: 40px;
  padding: 0 16px;
  border-radius: 20px;
  border: 1px solid var(--uhd-s-border);
  background: transparent;
  color: var(--uhd-s-text-2);
  font-size: 14px;
  font-family: inherit;
  cursor: pointer;
}
.lb-sort__btn--on {
  background: var(--uhd-s-pill-active-bg);
  color: var(--uhd-s-pill-active-text);
  font-weight: var(--uhd-fw-semibold);
  box-shadow: var(--uhd-shadow-seg);
}
.lb-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  margin-top: 22px;
}
.lb-pill {
  height: 36px;
  padding: 0 16px;
  border-radius: 18px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--uhd-s-text-2);
  font-size: 15px;
  font-family: inherit;
  cursor: pointer;
}
.lb-pill--on {
  background: var(--uhd-s-pill-active-bg);
  color: var(--uhd-s-pill-active-text);
  font-weight: var(--uhd-fw-semibold);
  box-shadow: var(--uhd-shadow-seg);
  border-color: var(--uhd-s-pill-border);
}
.lb-count {
  margin: 18px 0 0;
  font-size: 14px;
  color: var(--uhd-s-text-2);
}
/* 网格：7 列（≥1600px）、海报 228×343 口径；窄屏按列数递减（断点为推断，见说明书未覆盖项） */
.lb-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 34px 21px;
  margin-top: 22px;
}
@media (max-width: 1599px) {
  .lb-grid { grid-template-columns: repeat(6, 1fr); }
}
@media (max-width: 1279px) {
  .lb-grid { grid-template-columns: repeat(5, 1fr); }
}
@media (max-width: 1023px) {
  .lb-grid { grid-template-columns: repeat(4, 1fr); }
}
@media (max-width: 767px) {
  .lb-grid { grid-template-columns: repeat(3, 1fr); gap: 22px 12px; }
  .lb-h1 { font-size: 40px; }
}
.lb-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 40px;
  font-size: 14px;
  color: var(--uhd-s-text-2);
}
.lb-spinner {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 2px solid var(--uhd-s-border);
  border-top-color: var(--uhd-s-text);
  animation: uhd-spin 0.8s linear infinite;
}
.lb-error,
.lb-empty {
  margin-top: 40px;
  font-size: 15px;
  color: var(--uhd-s-text-2);
}
.lb-retry,
.lb-more__btn {
  margin-top: 12px;
  height: 44px;
  padding: 0 26px;
  border-radius: 22px;
  border: 1px solid var(--uhd-s-border);
  background: var(--uhd-s-pill-active-bg);
  color: var(--uhd-s-text);
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  font-family: inherit;
  cursor: pointer;
}
.lb-more {
  display: flex;
  justify-content: center;
  margin-top: 44px;
}
.lb-more__btn {
  margin-top: 0;
}
.lb-more__btn:disabled {
  opacity: 0.55;
  cursor: wait;
}

/* 骨架屏网格（与 .lb-grid 同步列数） */
.lb-skel {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 34px 21px;
  margin-top: 26px;
}
@media (max-width: 1599px) { .lb-skel { grid-template-columns: repeat(6, 1fr); } }
@media (max-width: 1279px) { .lb-skel { grid-template-columns: repeat(5, 1fr); } }
@media (max-width: 1023px) { .lb-skel { grid-template-columns: repeat(4, 1fr); } }
@media (max-width: 767px) { .lb-skel { grid-template-columns: repeat(3, 1fr); gap: 22px 12px; } }
.lb-skel__poster {
  aspect-ratio: 2 / 3;
  border-radius: 13px;
  background: var(--uhd-s-card-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: lb-pulse 1.4s ease-in-out infinite;
}
.lb-skel__logo {
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
@keyframes lb-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}
</style>
