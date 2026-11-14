<script setup lang="ts">
/**
 * 片库分类页（/library/movies、/library/series —— UHDNow 分类页对应面）
 * 骨架（存档第 10 节＋用户真机截图）：无 Hero；H1 56–60px 超粗（手机约 40）；
 * 分类行：片库分类胶囊（激活 = 白胶囊高 40/圆角 20 + 阴影，内含「名称＋计数」；
 * 未激活 = 15px 字 + 13px 灰计数）；行右端排序单胶囊（当前排序名 + ⇅，
 * 点开下拉选排序 + 升降序切换）；计数行「{分类} · N 部」14px 灰；
 * 网格 7 列、海报 228×343、列间距 20–22；下方「加载更多 / 回到顶部」。
 * 眉题「媒体库」：桌面存档有、用户手机截图无——手机端隐藏、桌面保留。
 * 分类 = 现行 getViews 按 CollectionType 过滤（movies / tvshows），
 * 计数 = 各库 getItems 的 TotalRecordCount，全部数据驱动、不写死分类名与数字。
 * 条目数据调用仍走现行 getItems 口径（仅加既有 parentId 参数，与首页逐库行同口径）。
 * 排序（友站口径）：单胶囊下拉 5 键——最近添加 / 上映日期 / 名称 / 评分 /
 * 资源更新 + 升降序切换。五键全部后端 Items 真支持（DateCreated /
 * PremiereDate / SortName / CommunityRating / DateLastContentAdded，
 * 映射见 emby_server _query_items）。热度无全站数据源
 * （后端 PlayCount 是当前用户自己的播放次数），不冒充、不提供。
 */
import { computed, onActivated, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowDown, ArrowUp, ArrowUpDown, Check } from 'lucide-vue-next'
import StreamShell from '@/components/stream/StreamShell.vue'
import StreamPosterCard from '@/components/stream/StreamPosterCard.vue'
import {
  embyApi as embyProtocol,
  posterUrl,
  type EmbyItem,
} from '@/api/emby'

/* KeepAlive include 按组件 name 匹配：显式声明，保活命中不靠文件名推断 */
defineOptions({ name: 'LibraryBrowseView' })

const route = useRoute()
const kind = computed<'movie' | 'series'>(() =>
  route.path.endsWith('/series') ? 'series' : 'movie',
)
const heading = computed(() => (kind.value === 'series' ? '剧集' : '电影'))
const includeTypes = computed(() => (kind.value === 'series' ? ['Series'] : ['Movie']))
const collectionType = computed(() => (kind.value === 'series' ? 'tvshows' : 'movies'))

const PAGE = 60
const items = ref<EmbyItem[]>([])
const total = ref(0)
const hasMore = ref(false)
const loading = ref(false)
const loadingMore = ref(false)
const error = ref('')

/* ---------- 分类（片库视图 + 计数，数据驱动） ---------- */
interface Category {
  id: string
  name: string
  count: number | null
}
const categories = ref<Category[]>([])
/** '' = 无分类可用时的兜底（不加 parentId、拉全部，旧口径） */
const activeCat = ref('')

const activeCatName = computed(
  () => categories.value.find((c) => c.id === activeCat.value)?.name || '全部',
)

async function loadCategories() {
  try {
    const views = await embyProtocol.getViews()
    const list = (views || []).filter(
      (v) => (v.CollectionType || '') === collectionType.value,
    )
    categories.value = list.map((v): Category => ({ id: v.Id, name: v.Name, count: null }))
    if (list.length && !list.some((v) => v.Id === activeCat.value)) {
      activeCat.value = list[0].Id
    }
    // 计数逐库取 TotalRecordCount（limit 1 只读计数，不拉数据）
    await Promise.allSettled(
      list.map(async (v) => {
        try {
          const res = await embyProtocol.getItems({
            parentId: v.Id,
            includeTypes: includeTypes.value,
            limit: 1,
          })
          const cat = categories.value.find((c) => c.id === v.Id)
          if (cat) cat.count = res.TotalRecordCount ?? null
        } catch {
          /* 计数拿不到就只显示分类名，不阻塞页面 */
        }
      }),
    )
  } catch {
    categories.value = []
  }
}

function pickCategory(id: string) {
  if (activeCat.value === id) return
  activeCat.value = id
  void load(true)
}

/* ---------- 排序（单胶囊下拉 + 升降序） ---------- */
type SortOrder = 'Ascending' | 'Descending'
interface SortDef {
  key: string
  label: string
  sortBy: string
  /** 选中该排序时的自然方向；升降序切换在此基础上翻转当前方向 */
  order: SortOrder
}
const SORTS: SortDef[] = [
  { key: 'recent', label: '最近添加', sortBy: 'DateCreated', order: 'Descending' },
  { key: 'premiere', label: '上映日期', sortBy: 'PremiereDate,SortName', order: 'Descending' },
  { key: 'name', label: '名称', sortBy: 'SortName', order: 'Ascending' },
  { key: 'rating', label: '评分', sortBy: 'CommunityRating,SortName', order: 'Descending' },
  { key: 'updated', label: '资源更新', sortBy: 'DateLastContentAdded,SortName', order: 'Descending' },
]
const sort = ref('recent')
const sortOrder = ref<SortOrder>('Descending')
const sortOpen = ref(false)

function sortDef(): SortDef {
  return SORTS.find((s) => s.key === sort.value) || SORTS[0]
}
const sortLabel = computed(() => sortDef().label)

function sortParams() {
  return { sortBy: sortDef().sortBy, sortOrder: sortOrder.value }
}

function pickSort(key: string) {
  sort.value = key
  sortOrder.value = sortDef().order
  sortOpen.value = false
  void load(true)
}
function toggleSortOrder() {
  sortOrder.value = sortOrder.value === 'Descending' ? 'Ascending' : 'Descending'
  sortOpen.value = false
  void load(true)
}

/* ---------- 条目加载（现行 getItems 口径） ---------- */
let seq = 0
/** 保活切回静默刷新阈值：与首页同口径 5 分钟 */
const VIEW_REFRESH_TTL_MS = 5 * 60 * 1000
let lastLoadedAt = 0
/** silent = 保活切回的后台刷新：不亮骨架、失败保留已有数据不清空 */
async function load(reset = true, silent = false) {
  const my = ++seq
  if (reset) {
    if (!silent) {
      loading.value = true
      error.value = ''
    }
  } else {
    loadingMore.value = true
  }
  try {
    const res = await embyProtocol.getItems({
      parentId: activeCat.value || undefined,
      includeTypes: includeTypes.value,
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
    if (reset) lastLoadedAt = Date.now()
  } catch (err) {
    if (my !== seq) return
    if (reset && !silent) {
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

/* 保活切回：数据还新鲜（< 5 分钟）不重拉；过期才静默刷新，不闪骨架 */
onActivated(() => {
  if (lastLoadedAt && Date.now() - lastLoadedAt > VIEW_REFRESH_TTL_MS) {
    void (async () => {
      await loadCategories()
      await load(true, true)
    })()
  }
})

function scrollTop() {
  window.scrollTo({ top: 0, behavior: 'smooth' })
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
  activeCat.value = ''
  sortOpen.value = false
  void (async () => {
    await loadCategories()
    void load(true)
  })()
})
onMounted(() => {
  void (async () => {
    await loadCategories()
    void load(true)
  })()
})
</script>

<template>
  <StreamShell>
    <div class="lb-wrap">
      <p class="lb-eyebrow">媒体库</p>
      <div class="lb-head">
        <h1 class="lb-h1">{{ heading }}</h1>
      </div>

      <!-- 分类行：左分类胶囊（激活含计数），右排序单胶囊 -->
      <div class="lb-catrow">
        <div class="lb-pills" role="group" aria-label="分类">
          <button
            v-for="c in categories"
            :key="c.id"
            type="button"
            class="lb-pill"
            :class="{ 'lb-pill--on': activeCat === c.id }"
            @click="pickCategory(c.id)"
          >
            {{ c.name }}<span v-if="c.count !== null" class="lb-pill__count">{{ c.count }}</span>
          </button>
          <span v-if="!categories.length" class="lb-pill lb-pill--on">全部</span>
        </div>
        <div class="lb-sortwrap">
          <button
            type="button"
            class="lb-sortbtn"
            :aria-expanded="sortOpen"
            @click="sortOpen = !sortOpen"
          >
            {{ sortLabel }}
            <ArrowUpDown :size="14" aria-hidden="true" />
          </button>
          <template v-if="sortOpen">
            <div class="lb-sortveil" @click="sortOpen = false"></div>
            <div class="lb-sortmenu" role="menu" aria-label="排序方式">
              <button
                v-for="s in SORTS"
                :key="s.key"
                type="button"
                class="lb-sortmenu__item"
                :class="{ 'lb-sortmenu__item--on': sort === s.key }"
                @click="pickSort(s.key)"
              >
                <span>{{ s.label }}</span>
                <Check v-if="sort === s.key" :size="15" aria-hidden="true" />
              </button>
              <div class="lb-sortmenu__sep" aria-hidden="true"></div>
              <button type="button" class="lb-sortmenu__item" @click="toggleSortOrder">
                <span>{{ sortOrder === 'Descending' ? '降序' : '升序' }}</span>
                <ArrowDown v-if="sortOrder === 'Descending'" :size="15" aria-hidden="true" />
                <ArrowUp v-else :size="15" aria-hidden="true" />
              </button>
            </div>
          </template>
        </div>
      </div>

      <p class="lb-count">{{ activeCatName }} · {{ total }} 部</p>

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
        <div class="lb-more">
          <button
            v-if="hasMore"
            type="button"
            class="lb-more__btn"
            :disabled="loadingMore"
            @click="load(false)"
          >
            {{ loadingMore ? '正在加载…' : '加载更多' }}
          </button>
          <button type="button" class="lb-more__btn lb-more__btn--ghost" @click="scrollTop">
            回到顶部
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
/* 分类行：分类胶囊左、排序单胶囊右（存档第 10 节） */
.lb-catrow {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-top: 22px;
}
.lb-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  min-width: 0;
}
.lb-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 40px;
  padding: 0 16px;
  border-radius: 20px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--uhd-s-text-2);
  font-size: 15px;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
}
.lb-pill__count {
  font-size: 13px;
  color: var(--uhd-s-text-2);
}
.lb-pill--on {
  background: var(--uhd-s-pill-active-bg);
  color: var(--uhd-s-pill-active-text);
  font-weight: var(--uhd-fw-semibold);
  box-shadow: var(--uhd-shadow-seg);
  border-color: var(--uhd-s-pill-border);
}
.lb-pill--on .lb-pill__count {
  color: inherit;
  font-weight: var(--uhd-fw-semibold);
}
/* 排序单胶囊 + 下拉（约 135×40/圆角 20 口径，宽度随当前排序名） */
.lb-sortwrap {
  position: relative;
  flex: none;
  margin-left: auto;
}
.lb-sortbtn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 40px;
  padding: 0 16px;
  border-radius: 20px;
  border: 1px solid var(--uhd-s-pill-border);
  background: var(--uhd-s-pill-bg);
  color: var(--uhd-s-text);
  font-size: 14px;
  font-weight: var(--uhd-fw-semibold);
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
}
.lb-sortveil {
  position: fixed;
  inset: 0;
  z-index: 40;
}
.lb-sortmenu {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 41;
  min-width: 176px;
  padding: 6px;
  border-radius: 16px;
  border: 1px solid var(--uhd-s-pill-border);
  background: var(--uhd-s-pill-active-bg);
  box-shadow: var(--uhd-s-shadow-pill);
}
.lb-sortmenu__item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  height: 38px;
  padding: 0 12px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--uhd-s-text-2);
  font-size: 14px;
  font-family: inherit;
  cursor: pointer;
  text-align: left;
}
.lb-sortmenu__item--on {
  color: var(--uhd-s-text);
  font-weight: var(--uhd-fw-semibold);
}
.lb-sortmenu__sep {
  height: 1px;
  margin: 5px 8px;
  background: var(--uhd-s-border);
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
  /* 用户真机截图：手机版无「媒体库」眉题 */
  .lb-eyebrow { display: none; }
  .lb-h1 { margin-top: 0; }
  /* 手机：分类单行横滑，排序胶囊留在行右 */
  .lb-pills {
    flex-wrap: nowrap;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .lb-pills::-webkit-scrollbar { display: none; }
  .lb-pill { height: 38px; padding: 0 14px; }
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
  gap: 12px;
  margin-top: 44px;
}
.lb-more__btn {
  margin-top: 0;
}
.lb-more__btn--ghost {
  background: transparent;
  color: var(--uhd-s-text-2);
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
