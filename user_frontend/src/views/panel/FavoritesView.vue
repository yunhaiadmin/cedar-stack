<template>
  <StreamShell>
    <div class="fv-wrap">
      <p class="fv-eyebrow">♥ 我的收藏</p>
      <h1 class="fv-h1">收藏</h1>
      <!-- 页签：剧集 / 电影 / 演职人员（存档第 11 节；激活白胶囊；深色转深灰 + 白字，随令牌走） -->
      <div class="fv-tabs" role="group" aria-label="收藏类型">
        <button type="button" class="fv-tab" :class="{ 'fv-tab--on': tab === 'series' }" @click="pickTab('series')">剧集 {{ seriesCount }}</button>
        <button type="button" class="fv-tab" :class="{ 'fv-tab--on': tab === 'movie' }" @click="pickTab('movie')">电影 {{ movieCount }}</button>
        <button type="button" class="fv-tab" :class="{ 'fv-tab--on': tab === 'people' }" @click="pickTab('people')">演职人员 {{ peopleCount }}</button>
      </div>

      <!-- 演职人员页签：数据走人员收藏接口（快照），与媒体收藏各走各的链 -->
      <template v-if="tab === 'people'">
        <div v-if="peopleError" class="fv-error" role="alert">
          演职人员收藏没加载出来：{{ peopleError }}<br />
          <button type="button" class="fv-retry" :disabled="peopleLoading" @click="() => loadPeople()">{{ peopleLoading ? '正在重试…' : '重试' }}</button>
        </div>
        <div v-else-if="peopleLoading" class="fv-skel" role="status" aria-label="正在加载">
          <div v-for="i in 7" :key="i" class="fv-skel__poster"><span class="fv-skel__logo">A</span></div>
        </div>
        <div v-else-if="peopleItems.length === 0" class="fv-empty">
          还没有收藏的演职人员 —— 在片子详情页点任意一位演职人员，进人员页就能收藏
        </div>
        <div v-else class="fv-grid">
          <article v-for="p in peopleItems" :key="p.key" class="fv-card">
            <div class="fv-poster fv-poster--person">
              <span class="fv-avatar">
                <img
                  v-if="p.image && !brokenAvatars.has(p.key)"
                  :src="p.image"
                  alt=""
                  loading="lazy"
                  decoding="async"
                  @error="onAvatarError(p.key)"
                />
                <template v-else>{{ p.name.charAt(0) }}</template>
              </span>
              <RouterLink
                class="fv-link"
                :to="{ name: 'person-detail', params: { key: p.key } }"
                :aria-label="`查看演职人员 ${p.name}`"
              />
              <button
                type="button"
                class="fv-star"
                :disabled="pendingPersonKey === p.key"
                :aria-label="`取消收藏 ${p.name}`"
                title="取消收藏"
                @click="togglePerson(p)"
              >
                <Star :size="16" :stroke-width="2" fill="currentColor" />
              </button>
            </div>
            <div class="fv-body">
              <RouterLink class="fv-name" :title="p.name" :to="{ name: 'person-detail', params: { key: p.key } }">{{ p.name }}</RouterLink>
              <div class="fv-meta">演职人员</div>
            </div>
          </article>
        </div>
      </template>

      <template v-else>
      <div v-if="error" class="fv-error" role="alert">
        收藏没加载出来：{{ error }}<br />
        <button type="button" class="fv-retry" :disabled="loading" @click="() => load()">{{ loading ? '正在重试…' : '重试' }}</button>
      </div>
      <!-- 骨架屏（录屏形态）：海报灰块 + 居中站标 -->
      <div v-else-if="loading" class="fv-skel" role="status" aria-label="正在加载">
        <div v-for="i in 7" :key="i" class="fv-skel__poster"><span class="fv-skel__logo">A</span></div>
      </div>
      <!-- 空态虚线框：高约 230（存档第 11 节）；演职人员页签走上方独立模板块，本分支不会命中 -->
      <div v-else-if="items.length === 0" class="fv-empty">
        还没有收藏的{{ tab === 'movie' ? '电影' : '剧集' }} —— 在播放器里给喜欢的片子标个星，就会到这里来
      </div>
      <div v-else-if="filteredItems.length === 0" class="fv-empty">
        这个分类下还没有收藏
      </div>
      <div v-else class="fv-grid">
        <article v-for="m in filteredItems" :key="m.id" class="fv-card">
          <div class="fv-poster">
            <img
              v-if="posterSrc(m)"
              class="fv-img"
              :src="posterSrc(m)"
              alt=""
              loading="lazy"
              decoding="async"
              @error="onPosterError(m.id)"
            />
            <Clapperboard v-else :size="28" :stroke-width="1.5" class="fv-fallback" aria-hidden="true" />
            <RouterLink
              class="fv-link"
              :to="{ name: 'media-detail', params: { id: m.id } }"
              :aria-label="`查看《${m.name}》详情`"
            />
            <button
              type="button"
              class="fv-star"
              :disabled="pendingId === m.id"
              :aria-label="`取消收藏《${m.name}》`"
              title="取消收藏"
              @click="toggle(m)"
            >
              <Star :size="16" :stroke-width="2" fill="currentColor" />
            </button>
          </div>
          <div class="fv-body">
            <RouterLink class="fv-name" :title="m.name" :to="{ name: 'media-detail', params: { id: m.id } }">{{ m.name }}</RouterLink>
            <div class="fv-meta">{{ metaLine(m) }}</div>
          </div>
        </article>
      </div>
      </template>
    </div>
  </StreamShell>
</template>

<script setup lang="ts">
import { computed, onActivated, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { Clapperboard, Star } from 'lucide-vue-next'
import { embyApi, type PortalMediaItem, type PortalPerson } from '@/api'
import { imageAuthUrl } from '@/api/emby'
import { embyApiKeyRef } from '@/api/imageKey'
import { useToast } from '@/composables/useToast'
import StreamShell from '@/components/stream/StreamShell.vue'

/* KeepAlive include 按组件 name 匹配：显式声明，保活命中不靠文件名推断 */
defineOptions({ name: 'FavoritesView' })

const toast = useToast()

const loading = ref(false)
const error = ref('')
const items = ref<PortalMediaItem[]>([])
const pendingId = ref<string | null>(null)
const brokenPosters = ref<Set<string>>(new Set())

function errText(err: unknown): string {
  const msg = (err as { userMessage?: string })?.userMessage
  if (msg) return msg
  if (err instanceof Error) return err.message
  return '加载失败'
}

function posterSrc(m: PortalMediaItem): string {
  if (brokenPosters.value.has(m.id)) return ''
  const url = m.poster_url
  // PA-036：后端给的 poster_url 不带凭据，渲染前补 api_key（图片端点已鉴权）；
  // 同时按卡片展示尺寸补 maxWidth=320，别拉原图（poster_url 是裸地址）
  return typeof url === 'string' && url.trim() !== '' ? imageAuthUrl(url, 456) : ''
}

function onPosterError(id: string) {
  brokenPosters.value = new Set(brokenPosters.value).add(id)
}

// PA-054：图片 key 一变（别页轮换后经 storage 同步过来、或本页读卡拿到
// 新 key），裂图标记立刻清掉——posterSrc 重新拼上新 key 的地址自动重试，
// 不用整页重载才恢复。key 本身没变时不触发，不会把正常裂图反复重刷。
watch(embyApiKeyRef, () => {
  if (brokenPosters.value.size > 0) brokenPosters.value = new Set()
})

function metaLine(m: PortalMediaItem): string {
  const parts: string[] = []
  parts.push(m.type === 'movie' ? '电影' : m.type === 'series' ? '剧集' : m.type === 'season' ? '季' : m.type === 'episode' ? '集' : m.type)
  if (m.year) parts.push(String(m.year))
  if (m.rating) parts.push(m.rating.toFixed(1))
  return parts.join(' · ')
}

/** 保活切回静默刷新阈值：与首页同口径 5 分钟 */
const VIEW_REFRESH_TTL_MS = 5 * 60 * 1000
let lastLoadedAt = 0
/** silent = 保活切回的后台刷新：不亮骨架、失败保留已有数据、不动用户页签 */
async function load(silent = false) {
  if (!silent) {
    loading.value = true
    error.value = ''
  }
  try {
    const data = await embyApi.getFavorites()
    items.value = data.items || []
    lastLoadedAt = Date.now()
    // 默认落在剧集页签；用户未手动切过且只有电影收藏时落到电影，避免先见空态
    // （静默刷新不走这条：不许在用户背后换页签）
    if (!silent && !tabPicked.value && seriesCount.value === 0 && movieCount.value > 0) {
      tab.value = 'movie'
    }
  } catch (err) {
    if (!silent) error.value = errText(err)
  } finally {
    if (!silent) loading.value = false
  }
}

/* 演职人员收藏：独立一条链，加载失败只影响演职页签、不挡媒体收藏 */
const peopleItems = ref<PortalPerson[]>([])
const peopleLoading = ref(false)
const peopleError = ref('')
const pendingPersonKey = ref<string | null>(null)
const brokenAvatars = ref<Set<string>>(new Set())

async function loadPeople(silent = false) {
  if (!silent) {
    peopleLoading.value = true
    peopleError.value = ''
  }
  try {
    const data = await embyApi.getPersonFavorites()
    peopleItems.value = data.items || []
  } catch (err) {
    if (!silent) peopleError.value = errText(err)
  } finally {
    if (!silent) peopleLoading.value = false
  }
}

/* 保活切回：数据还新鲜（< 5 分钟）不重拉；过期才静默刷新，不闪骨架 */
onActivated(() => {
  if (lastLoadedAt && Date.now() - lastLoadedAt > VIEW_REFRESH_TTL_MS) {
    void load(true)
    void loadPeople(true)
  }
})

function onAvatarError(key: string) {
  brokenAvatars.value = new Set(brokenAvatars.value).add(key)
}

async function togglePerson(p: PortalPerson) {
  if (pendingPersonKey.value) return
  pendingPersonKey.value = p.key
  try {
    const res = await embyApi.togglePersonFavorite(p.key)
    if (res.is_favorite) {
      toast.info(`${p.name} 已在收藏里`)
    } else {
      peopleItems.value = peopleItems.value.filter((x) => x.key !== p.key)
      toast.success(`已取消收藏 ${p.name}`)
    }
  } catch (err) {
    toast.error(`取消收藏失败：${errText(err)}`)
  } finally {
    pendingPersonKey.value = null
  }
}

async function toggle(m: PortalMediaItem) {
  if (pendingId.value) return
  pendingId.value = m.id
  try {
    const res = await embyApi.toggleFavorite(m.id)
    if (res.is_favorite) {
      // 理论上不会发生（列表里都是已收藏），但后端说了算：状态对齐即可
      toast.info(`《${m.name}》已在收藏里`)
    } else {
      items.value = items.value.filter((x) => x.id !== m.id)
      toast.success(`已取消收藏《${m.name}》`)
    }
  } catch (err) {
    // 失败明示、不动列表：卡片还留在原地，用户可以再点
    toast.error(`取消收藏失败：${errText(err)}`)
  } finally {
    pendingId.value = null
  }
}

/* 片库面页签：剧集 / 电影 / 演职人员（存档第 11 节，计数嵌页签、激活白胶囊，无「全部」）。
   剧集/电影计数随收藏数据走，客户端过滤，不动接口；
   演职人员页签结构由 #132 先立，本单（favorites-people-tab）把数据通路接上：
   人员收藏走独立接口（上方 loadPeople 一链），计数＝人员收藏条数，不与媒体收藏混过滤。 */
type FavTab = 'series' | 'movie' | 'people'
const tab = ref<FavTab>('series')
const tabPicked = ref(false)
function pickTab(t: FavTab) {
  tabPicked.value = true
  tab.value = t
}
const seriesCount = computed(() => items.value.filter((x) => x.type === 'series').length)
const movieCount = computed(() => items.value.filter((x) => x.type === 'movie').length)
const peopleCount = computed(() => peopleItems.value.length)
const filteredItems = computed(() =>
  tab.value === 'people' ? [] : items.value.filter((x) => x.type === tab.value),
)

onMounted(() => {
  void load()
  void loadPeople()
})
</script>
<style scoped>
.fv-wrap {
  padding: 26px clamp(16px, 5vw, 96px) 56px;
}
.fv-eyebrow {
  margin: 0;
  font-size: 14px;
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-s-text-2);
}
.fv-h1 {
  margin: 8px 0 0;
  font-size: 56px;
  font-weight: var(--uhd-fw-black);
  line-height: 1.05;
  letter-spacing: -0.01em;
  color: var(--uhd-s-text);
}
.fv-tabs {
  display: flex;
  gap: 8px;
  margin-top: 22px;
}
.fv-tab {
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
.fv-tab--on {
  background: var(--uhd-s-pill-active-bg);
  color: var(--uhd-s-pill-active-text);
  font-weight: var(--uhd-fw-semibold);
  box-shadow: var(--uhd-shadow-seg);
  border-color: var(--uhd-s-pill-border);
}
.fv-error,
.fv-loading {
  margin-top: 36px;
  font-size: 15px;
  color: var(--uhd-s-text-2);
}
.fv-spinner {
  display: inline-block;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 2px solid var(--uhd-s-border);
  border-top-color: var(--uhd-s-text);
  animation: uhd-spin 0.8s linear infinite;
  vertical-align: -3px;
  margin-right: 8px;
}
.fv-retry {
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
/* 空态虚线框：高约 230、圆角约 12、居中 15px 灰字 */
.fv-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 230px;
  margin-top: 26px;
  padding: 24px;
  border: 1.5px dashed var(--uhd-s-border);
  border-radius: 12px;
  font-size: 15px;
  color: var(--uhd-s-text-2);
  text-align: center;
}
/* 网格与分类页同款：7 列 228×343 口径，窄屏递减 */
.fv-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 34px 21px;
  margin-top: 26px;
}
@media (max-width: 1599px) {
  .fv-grid { grid-template-columns: repeat(6, 1fr); }
}
@media (max-width: 1279px) {
  .fv-grid { grid-template-columns: repeat(5, 1fr); }
}
@media (max-width: 1023px) {
  .fv-grid { grid-template-columns: repeat(4, 1fr); }
}
@media (max-width: 767px) {
  .fv-grid { grid-template-columns: repeat(3, 1fr); gap: 22px 12px; }
  .fv-h1 { font-size: 40px; }
}
.fv-card {
  min-width: 0;
}
.fv-poster {
  position: relative;
  aspect-ratio: 2 / 3;
  border-radius: 13px;
  background: var(--uhd-s-card-soft);
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}
.fv-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
/* 演职人员卡：格子同海报节奏，头像居中大圆（无海报底色块） */
.fv-poster--person {
  background: transparent;
}
.fv-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 72%;
  aspect-ratio: 1;
  border-radius: 50%;
  overflow: hidden;
  background: var(--uhd-s-card-soft);
  color: var(--uhd-s-text-2);
  font-size: 30px;
  font-weight: var(--uhd-fw-bold);
}
.fv-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.fv-fallback {
  color: var(--uhd-s-text-2);
}
.fv-link {
  position: absolute;
  inset: 0;
  z-index: 1;
}
/* 收藏星钮是业务控件（非角标）：保留现行口径，位置与行为不变 */
.fv-star {
  position: absolute;
  z-index: 2;
  top: 8px;
  right: 8px;
  width: 32px;
  height: 32px;
  border: 1px solid var(--uhd-s-pill-border);
  border-radius: 999px;
  background: var(--uhd-s-pill-bg);
  color: var(--uhd-s-text);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  padding: 0;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}
.fv-star:disabled {
  opacity: 0.55;
  cursor: wait;
}
.fv-body {
  padding-top: 12px;
}
.fv-name {
  display: block;
  font-size: 17px;
  font-weight: var(--uhd-fw-bold);
  line-height: 1.3;
  color: var(--uhd-s-text);
  text-decoration: none;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.fv-meta {
  margin-top: 4px;
  font-size: 14px;
  color: var(--uhd-s-text-2);
}

/* 骨架屏网格（与 .fv-grid 同步列数） */
.fv-skel {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 34px 21px;
  margin-top: 26px;
}
@media (max-width: 1599px) { .fv-skel { grid-template-columns: repeat(6, 1fr); } }
@media (max-width: 1279px) { .fv-skel { grid-template-columns: repeat(5, 1fr); } }
@media (max-width: 1023px) { .fv-skel { grid-template-columns: repeat(4, 1fr); } }
@media (max-width: 767px) { .fv-skel { grid-template-columns: repeat(3, 1fr); gap: 22px 12px; } }
.fv-skel__poster {
  aspect-ratio: 2 / 3;
  border-radius: 13px;
  background: var(--uhd-s-card-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: fv-pulse 1.4s ease-in-out infinite;
}
.fv-skel__logo {
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
@keyframes fv-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}
</style>
