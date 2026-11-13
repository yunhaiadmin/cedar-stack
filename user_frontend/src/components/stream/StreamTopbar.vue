<script setup lang="ts">
/**
 * StreamTopbar —— 片库面双胶囊顶栏（批 3，说明书 2.1 片库面变体）
 * 实测数值（uhdnow-stream-reverse 第 1/9 节）：
 * 左胶囊高 46/圆角 23 毛玻璃，内含标志 + 首页/电影/剧集/收藏，
 * 激活项为内嵌胶囊高 34/圆角 17；右胶囊高 46，含搜索/外观/头像 28。
 * 搜索：点放大镜向左展开宽约 560、高 46 的搜索条；下拉面板 480×490、
 * 圆角 20、结果行高 72（缩略图 40×60）。数据走现行 Emby 协议搜索口，
 * 与搜索结果页同一口径，不新造接口。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { Clapperboard, MonitorSmartphone, Moon, Search, Sun, X } from 'lucide-vue-next'
import { embyApi as embyProtocol, imageUrl, type EmbyItem } from '@/api/emby'
import { useUserStore } from '@/stores/user'
import { branding } from '@/composables/useBranding'

const props = defineProps<{
  pref: 'system' | 'light' | 'dark'
  resolved: 'light' | 'dark'
}>()
const emit = defineEmits<{ 'cycle-theme': [] }>()

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const NAV = [
  { label: '首页', to: '/library' },
  { label: '电影', to: '/library/movies' },
  { label: '剧集', to: '/library/series' },
  { label: '收藏', to: '/favorites' },
] as const

function isActive(to: string): boolean {
  if (to === '/library') return route.path === '/library'
  return route.path === to || route.path.startsWith(`${to}/`)
}

const themeIcon = computed(() =>
  props.pref === 'light' ? Sun : props.pref === 'dark' ? Moon : MonitorSmartphone,
)
const themeLabel = computed(
  () => `外观：${props.pref === 'system' ? '跟随系统' : props.pref === 'light' ? '浅色' : '深色'}（点击切换）`,
)

const avatarText = computed(() => {
  const raw = userStore.user?.username || '用'
  return raw.charAt(0).toUpperCase()
})

/* ---------- 展开式搜索 + 下拉面板 ---------- */
const searchOpen = ref(false)
const query = ref('')
const results = ref<EmbyItem[]>([])
const searching = ref(false)
let debounce = 0
let seq = 0

function openSearch() {
  searchOpen.value = true
}
function closeSearch() {
  searchOpen.value = false
  query.value = ''
  results.value = []
}

watch(query, (q) => {
  window.clearTimeout(debounce)
  const term = q.trim()
  if (!term) {
    results.value = []
    searching.value = false
    return
  }
  searching.value = true
  debounce = window.setTimeout(() => {
    void runSearch(term)
  }, 280)
})

async function runSearch(term: string) {
  const my = ++seq
  try {
    const res = await embyProtocol.getItems({
      searchTerm: term,
      includeTypes: ['Movie', 'Series'],
      limit: 12,
      recursive: true,
    })
    if (my !== seq) return
    results.value = res.Items || []
  } catch {
    if (my === seq) results.value = []
  } finally {
    if (my === seq) searching.value = false
  }
}

function goResults() {
  const term = query.value.trim()
  if (!term) return
  closeSearch()
  void router.push({ path: '/search', query: { q: term } })
}

function thumb(item: EmbyItem): string {
  return item.ImageTags?.Primary ? imageUrl(item.Id, 'Primary', 80) : ''
}
function metaLine(item: EmbyItem): string {
  const parts: string[] = []
  if (item.ProductionYear) parts.push(String(item.ProductionYear))
  if (item.Genres?.length) parts.push(item.Genres.slice(0, 2).join(' / '))
  if (item.CommunityRating) parts.push(`★ ${item.CommunityRating.toFixed(1)}`)
  return parts.join(' · ')
}
/** 命中词高亮：把标题按关键词切段（渲染用，非 HTML 注入） */
function segments(name: string): Array<{ text: string; hit: boolean }> {
  const term = query.value.trim()
  if (!term) return [{ text: name, hit: false }]
  const idx = name.toLowerCase().indexOf(term.toLowerCase())
  if (idx < 0) return [{ text: name, hit: false }]
  return [
    { text: name.slice(0, idx), hit: false },
    { text: name.slice(idx, idx + term.length), hit: true },
    { text: name.slice(idx + term.length), hit: false },
  ].filter((s) => s.text)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && searchOpen.value) closeSearch()
}
document.addEventListener('keydown', onKeydown)
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <header class="stream-topbar">
    <nav class="st-capsule st-capsule--nav" aria-label="片库导航">
      <RouterLink to="/library" class="st-logo" :aria-label="branding.site_name">
        <Clapperboard :size="20" :stroke-width="2" />
      </RouterLink>
      <RouterLink
        v-for="item in NAV"
        :key="item.to"
        :to="item.to"
        class="st-nav-item"
        :class="{ 'st-nav-item--on': isActive(item.to) }"
        :aria-current="isActive(item.to) ? 'page' : undefined"
      >{{ item.label }}</RouterLink>
    </nav>

    <div class="st-right">
      <!-- 展开搜索条（桌面）：宽约 560、高 46 -->
      <div v-if="searchOpen" class="st-searchbar">
        <Search :size="18" class="st-searchbar__icon" aria-hidden="true" />
        <input
          v-model="query"
          class="st-searchbar__input"
          type="search"
          placeholder="搜索电影、剧集…"
          aria-label="搜索电影、剧集"
          autofocus
          @keydown.enter.prevent="goResults"
        />
        <button type="button" class="st-searchbar__x" aria-label="关闭搜索" @click="closeSearch">
          <X :size="16" />
        </button>
        <!-- 下拉面板：480×490、圆角 20、结果行 72 -->
        <div v-if="query.trim()" class="st-panel" role="listbox" aria-label="搜索建议">
          <button type="button" class="st-panel__more" @click="goResults">查看更多相关结果 →</button>
          <p class="st-panel__count">{{ searching ? '正在搜索…' : `前 ${results.length} 条相关结果` }}</p>
          <div class="st-panel__list">
            <RouterLink
              v-for="item in results"
              :key="item.Id"
              class="st-panel__row"
              :to="{ name: 'media-detail', params: { id: item.Id } }"
              @click="closeSearch"
            >
              <span class="st-panel__thumb">
                <img v-if="thumb(item)" :src="thumb(item)" alt="" loading="lazy" />
                <Clapperboard v-else :size="16" aria-hidden="true" />
              </span>
              <span class="st-panel__body">
                <span class="st-panel__title">
                  <template v-for="(seg, i) in segments(item.Name)" :key="i"><mark v-if="seg.hit" class="st-hit">{{ seg.text }}</mark><template v-else>{{ seg.text }}</template></template>
                </span>
                <span class="st-panel__meta">{{ metaLine(item) }}</span>
              </span>
              <span class="st-panel__type">{{ item.Type === 'Series' ? '剧集' : '电影' }}</span>
            </RouterLink>
            <p v-if="!searching && results.length === 0" class="st-panel__empty">没有找到相关结果</p>
          </div>
        </div>
      </div>

      <div class="st-capsule st-capsule--tools">
        <button
          v-if="!searchOpen"
          type="button"
          class="st-tool"
          aria-label="搜索"
          @click="openSearch"
        >
          <Search :size="18" />
        </button>
        <button
          type="button"
          class="st-tool"
          :title="themeLabel"
          :aria-label="themeLabel"
          @click="emit('cycle-theme')"
        >
          <component :is="themeIcon" :size="18" />
        </button>
        <RouterLink to="/game/account" class="st-avatar" :aria-label="'我的'">{{ avatarText }}</RouterLink>
      </div>
    </div>
  </header>
</template>

<style scoped>
.stream-topbar {
  position: sticky;
  top: 0;
  z-index: 60;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 14px clamp(16px, 5vw, 96px) 0;
  pointer-events: none;
}
.stream-topbar > * {
  pointer-events: auto;
}
/* 双胶囊：高 46、圆角 23、毛玻璃 + 柔光阴影 + 细边框 */
.st-capsule {
  display: flex;
  align-items: center;
  height: 46px;
  border-radius: 23px;
  background: var(--uhd-s-pill-bg);
  border: 1px solid var(--uhd-s-pill-border);
  box-shadow: var(--uhd-s-shadow-pill);
  backdrop-filter: blur(12px) saturate(160%);
  -webkit-backdrop-filter: blur(12px) saturate(160%);
}
.st-capsule--nav {
  gap: 2px;
  padding: 0 6px 0 10px;
}
.st-logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  color: var(--uhd-s-text);
  text-decoration: none;
  flex: none;
}
.st-nav-item {
  display: inline-flex;
  align-items: center;
  height: 34px;
  padding: 0 19px;
  border-radius: 17px;
  font-size: 15px;
  font-weight: var(--uhd-fw-medium);
  color: var(--uhd-s-text-2);
  text-decoration: none;
  white-space: nowrap;
}
.st-nav-item--on {
  background: var(--uhd-s-pill-active-bg);
  color: var(--uhd-s-pill-active-text);
  font-weight: var(--uhd-fw-semibold);
  box-shadow: var(--uhd-shadow-seg);
}
.st-right {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.st-capsule--tools {
  gap: 2px;
  padding: 0 8px;
  flex: none;
}
.st-tool {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--uhd-s-text);
  cursor: pointer;
}
.st-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  margin-left: 4px;
  border-radius: 50%;
  background: var(--uhd-s-text);
  color: var(--uhd-s-bg);
  font-size: 13px;
  font-weight: var(--uhd-fw-bold);
  text-decoration: none;
  flex: none;
  align-self: center;
}
/* 展开搜索条：宽约 560、高 46（窄屏下占满可用宽度） */
.st-searchbar {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  width: min(560px, calc(100vw - 220px));
  height: 46px;
  padding: 0 8px 0 16px;
  border-radius: 23px;
  background: var(--uhd-s-pill-bg);
  border: 1px solid var(--uhd-s-pill-border);
  box-shadow: var(--uhd-s-shadow-pill);
  backdrop-filter: blur(12px) saturate(160%);
  -webkit-backdrop-filter: blur(12px) saturate(160%);
}
.st-searchbar__icon {
  color: var(--uhd-s-text-2);
  flex: none;
}
.st-searchbar__input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--uhd-s-text);
  font: inherit;
  font-size: 15px;
}
.st-searchbar__input::placeholder {
  color: var(--uhd-s-text-2);
}
.st-searchbar__x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--uhd-s-text-2);
  cursor: pointer;
  flex: none;
}
/* 下拉面板：宽 480、高约 490、圆角 20 */
.st-panel {
  position: absolute;
  top: calc(100% + 10px);
  right: 0;
  width: min(480px, calc(100vw - 32px));
  max-height: 490px;
  overflow: hidden auto;
  border-radius: 20px;
  background: var(--uhd-s-panel-bg);
  border: 1px solid var(--uhd-s-border);
  box-shadow: var(--uhd-shadow-modal);
  padding: 10px;
  z-index: 70;
}
.st-panel__more {
  display: block;
  width: 100%;
  padding: 8px 10px;
  border: none;
  background: transparent;
  color: var(--uhd-s-text);
  font-size: 14px;
  font-weight: var(--uhd-fw-semibold);
  text-align: left;
  cursor: pointer;
}
.st-panel__count {
  margin: 0;
  padding: 0 10px 6px;
  font-size: 13px;
  color: var(--uhd-s-text-2);
}
.st-panel__row {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 72px;
  padding: 0 10px;
  border-radius: 12px;
  text-decoration: none;
  color: var(--uhd-s-text);
}
.st-panel__row:hover {
  background: var(--uhd-s-highlight);
}
.st-panel__thumb {
  flex: none;
  width: 40px;
  height: 60px;
  border-radius: 6px;
  overflow: hidden;
  background: var(--uhd-s-card-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--uhd-s-text-2);
}
.st-panel__thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.st-panel__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.st-panel__title {
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.st-hit {
  background: var(--uhd-s-highlight);
  color: inherit;
  border-radius: 3px;
  padding: 0 1px;
}
.st-panel__meta {
  font-size: 13px;
  color: var(--uhd-s-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.st-panel__type {
  flex: none;
  padding: 2px 8px;
  border: 1px solid var(--uhd-s-border);
  border-radius: 10px;
  font-size: 12px;
  color: var(--uhd-s-text-2);
}
.st-panel__empty {
  margin: 0;
  padding: 18px 10px;
  font-size: 14px;
  color: var(--uhd-s-text-2);
  text-align: center;
}
@media (max-width: 767px) {
  .stream-topbar {
    padding: 10px 12px 0;
  }
  /* 手机：左胶囊只留标志 + 当前四项压缩（底部玻璃导航承担主导航，顶栏不重复占位） */
  .st-nav-item {
    padding: 0 10px;
    font-size: 14px;
  }
  .st-searchbar {
    width: calc(100vw - 24px);
    position: absolute;
    left: 12px;
    right: 12px;
    top: 10px;
  }
}
</style>
