<script setup lang="ts">
/**
 * 片库首页（/library，批 3 —— UHDNow /stream 首页对应面）
 * 骨架：全幅 Hero 轮播 + 继续观看 16:9 卡行 + 海报横滑行（数据驱动）。
 * 数据口径全部复用现行接口，不新造：
 * - Hero / 海报行：Emby 协议口 getLatest + getViews 逐库 getItems；
 * - 继续观看：门户口径 getResume（与发现页同一只读口）；
 * - 分区行：getHomeSections 的 recently_updated 分区（有才渲染）。
 * 播放入口：Hero「立即播放」打开 PlayerSchemeSheet（批 3 底部弹层形态：
 * 选版本 + 选线路，开始播放默认进网页播放器 /watch、第三方播放器保留），
 * 海报卡只进详情页、不挂播放入口。
 */
import { computed, onActivated, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { Clapperboard, Trash2 } from 'lucide-vue-next'
import { useToast } from '@/composables/useToast'
import { useUserStore } from '@/stores/user'
import StreamShell from '@/components/stream/StreamShell.vue'
import StreamHero, { type StreamHeroItem } from '@/components/stream/StreamHero.vue'
import StreamRow from '@/components/stream/StreamRow.vue'
import StreamPosterCard from '@/components/stream/StreamPosterCard.vue'
import PlayerSchemeSheet from '@/components/panel/PlayerSchemeSheet.vue'
import { embyApi, type HomeSection, type PortalResumeItem } from '@/api'
import {
  embyApi as embyProtocol,
  backdropUrl,
  formatDuration,
  posterUrl,
  ticksToSeconds,
  type EmbyItem,
} from '@/api/emby'
import { imageAuthUrl } from '@/api/emby'

interface PosterRowData {
  key: string
  title: string
  count: number | null
  moreTo?: string
  items: Array<{ id: string; title: string; meta: string; rating: number | null; image: string }>
}

/* KeepAlive include 按组件 name 匹配：显式声明，保活命中不靠文件名推断 */
defineOptions({ name: 'StreamHomeView' })

const toast = useToast()
const userStore = useUserStore()

/* ---------- 渐进渲染状态：四条腿各自 pending → ready / error，先到先画 ----------
   旧版 load() 两段瀑布全 settle 才撤骨架：任一条腿慢（全局超时 30s）整页
   骨架最长挂约 60s，数据到手也不先画（2026-11-14 用户真机截图实锤）。
   现每条腿独立落画：hero / 继续观看 / 分区 / 逐库行各有自己的小骨架，
   整页大骨架只在「什么都还没到」时出现；部分失败给行级重试，只有全部
   为空 / 全失败才出整页错误态。 */
type LegStatus = 'pending' | 'ready' | 'error'

interface RowEntry {
  key: string
  status: LegStatus
  /** ready 且有内容时的行数据；ready 但该库没条目为 null，不渲染 */
  row: PosterRowData | null
  /** view 行重试所需的视图对象（section 行不用） */
  view?: EmbyItem
  title?: string
}

const heroItems = ref<StreamHeroItem[]>([])
const heroStatus = ref<LegStatus>('pending')
const resumeItems = ref<PortalResumeItem[]>([])
const resumeStatus = ref<LegStatus>('pending')
const sectionRows = ref<RowEntry[]>([])
const sectionsStatus = ref<LegStatus>('pending')
const viewRows = ref<RowEntry[]>([])
/** views 腿口径：列表请求 + 逐库 items 全 settle 才离开 pending；
    列表本身失败才置 error（单库失败是 entry 级 error，不算整腿失败） */
const viewsStatus = ref<LegStatus>('pending')

/** 整页大骨架只在四条腿全 pending（什么都还没到）时出现 */
const showFullSkeleton = computed(
  () =>
    heroStatus.value === 'pending' &&
    resumeStatus.value === 'pending' &&
    sectionsStatus.value === 'pending' &&
    viewsStatus.value === 'pending',
)
const allSettled = computed(
  () =>
    heroStatus.value !== 'pending' &&
    resumeStatus.value !== 'pending' &&
    sectionsStatus.value !== 'pending' &&
    viewsStatus.value !== 'pending',
)
const hasContent = computed(
  () =>
    heroItems.value.length > 0 ||
    resumeItems.value.length > 0 ||
    sectionRows.value.some((r) => r.row) ||
    viewRows.value.some((r) => r.row),
)
/** 全部为空 / 全失败才出整页错误态（带重试） */
const showFatalError = computed(() => allSettled.value && !hasContent.value)

/** 首页读请求分级超时（毫秒）：全局 axios 默认 30s 不动，只在本页调用点覆盖 */
const HOME_TIMEOUT_MS = 10_000
/** 保活页签静默刷新阈值：切回首页超过这个时长才后台刷新（不闪骨架） */
const HOME_REFRESH_TTL_MS = 5 * 60 * 1000
/** 首页 SWR 缓存有效期：超过即丢弃走冷加载 */
const HOME_CACHE_TTL_MS = 10 * 60 * 1000
const HOME_CACHE_VERSION = 1

/** 只对超时 / 网络失败重试一次（HTTP 错误是服务端明确答复，不重试） */
function isTimeoutOrNetwork(err: unknown): boolean {
  const e = err as { code?: string; response?: unknown; message?: string } | null
  if (!e || e.response) return false
  return (
    e.code === 'ECONNABORTED' ||
    e.code === 'ERR_NETWORK' ||
    /timeout/i.test(e.message || '') ||
    e.message === 'Network Error'
  )
}
async function fetchHome<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn()
  } catch (e) {
    if (isTimeoutOrNetwork(e)) return fn()
    throw e
  }
}

/* ---------- 首页 SWR 缓存（sessionStorage，按 user id + 会话序列隔离） ----------
   只存四条腿的原始 DTO 响应，不存拼好的图片地址（地址里带图片凭据，
   凭据不许进缓存）。冷启动 / 重进首页先用缓存秒画，再后台静默刷新替换。 */
interface HomeRawData {
  latest?: EmbyItem[]
  resume?: { items: PortalResumeItem[] }
  sections?: { sections: HomeSection[] }
  views?: EmbyItem[]
  viewItems?: Record<string, { Items: EmbyItem[]; TotalRecordCount: number }>
}
interface HomeCachePayload {
  v: number
  ts: number
  userId: number | string
  seq: number
  data: HomeRawData
}
/** 与 App.vue viewCacheKey 同口径（user id + sessionSeq）：登出 / 换号 /
    重新登录后 seq 递增，旧键不再命中 */
function homeCacheKey(): string {
  return `aetrix:stream-home:v${HOME_CACHE_VERSION}:${userStore.user?.id ?? 'guest'}#${userStore.sessionSeq}`
}
function readHomeCache(): HomeCachePayload | null {
  try {
    const key = homeCacheKey()
    const rawText = sessionStorage.getItem(key)
    if (!rawText) return null
    const parsed = JSON.parse(rawText) as HomeCachePayload
    if (
      parsed?.v !== HOME_CACHE_VERSION ||
      parsed.userId !== (userStore.user?.id ?? 'guest') ||
      parsed.seq !== userStore.sessionSeq ||
      typeof parsed.ts !== 'number' ||
      Date.now() - parsed.ts > HOME_CACHE_TTL_MS ||
      !parsed.data
    ) {
      sessionStorage.removeItem(key)
      return null
    }
    return parsed
  } catch {
    return null
  }
}
function writeHomeCache(data: HomeRawData) {
  try {
    const payload: HomeCachePayload = {
      v: HOME_CACHE_VERSION,
      ts: Date.now(),
      userId: userStore.user?.id ?? 'guest',
      seq: userStore.sessionSeq,
      data,
    }
    sessionStorage.setItem(homeCacheKey(), JSON.stringify(payload))
  } catch {
    /* 配额满 / 隐私模式：缓存写不进不影响页面 */
  }
}
/** 单条腿重试成功后把新数据并回缓存，避免下次冷启动又画旧的 */
function patchHomeCache(patch: Partial<HomeRawData>) {
  const cur = readHomeCache()
  writeHomeCache({ ...(cur?.data || {}), ...patch })
}

const pickerOpen = ref(false)
const pickerTitle = ref('')
const pickerItemId = ref<string | number | null>(null)

function metaOf(item: EmbyItem): string {
  const parts: string[] = []
  if (item.ProductionYear) parts.push(String(item.ProductionYear))
  if (item.Genres?.length) parts.push(item.Genres.slice(0, 2).join(' / '))
  return parts.join(' · ')
}

function toPoster(item: EmbyItem) {
  return {
    id: item.Id,
    title: item.Name,
    meta: metaOf(item),
    rating: item.CommunityRating ?? null,
    image: posterUrl(item, 520),
  }
}

function resumeTitle(m: PortalResumeItem): string {
  return m.series_name || m.name
}
function resumeSub(m: PortalResumeItem): string {
  const parts: string[] = []
  if (m.type === 'series' && m.season_number != null && m.episode_number != null) {
    parts.push(`S${m.season_number} E${m.episode_number}`)
  }
  if (m.episode_name) parts.push(m.episode_name)
  return parts.join(' · ') || m.name
}
/** 进度行（录屏形态）：已看 X% · 已看时长 */
function resumeProgress(m: PortalResumeItem): string {
  const parts: string[] = []
  if (typeof m.progress === 'number') parts.push(`已看 ${Math.round(m.progress)}%`)
  if (m.position_ticks) parts.push(formatDuration(ticksToSeconds(m.position_ticks)))
  return parts.join(' · ')
}
const hidingId = ref('')
/** 继续观看卡右上垃圾桶：隐藏这条（现行 hideResumeItem 口径，可在别处恢复） */
async function hideResume(m: PortalResumeItem) {
  if (hidingId.value) return
  hidingId.value = m.id
  try {
    await embyApi.hideResumeItem(m.id)
    resumeItems.value = resumeItems.value.filter((x) => x.id !== m.id)
    /* 缓存里的续看列表同步摘掉，否则下次冷启动秒画会先把这条画回来 */
    const cached = readHomeCache()
    if (cached?.data.resume) {
      patchHomeCache({
        resume: { items: (cached.data.resume.items || []).filter((x) => x.id !== m.id) },
      })
    }
    toast.success('已从继续观看里移除')
  } catch {
    toast.error('移除失败，请稍后再试')
  } finally {
    hidingId.value = ''
  }
}
function resumeImage(m: PortalResumeItem): string {
  return m.poster_url ? imageAuthUrl(m.poster_url, 640) : ''
}
/** 缩略图加载失败（个别条目图 404 等）的卡：撤下 img 换占位，不留浏览器裂图图标 */
const brokenResumeThumbs = ref(new Set<string>())

function onHeroPlay(item: StreamHeroItem) {
  pickerTitle.value = item.title
  pickerItemId.value = item.id
  pickerOpen.value = true
}

/* ---------- 原始响应 → 视图数据（网络与缓存共用同一套映射） ---------- */
function applyLatest(items: EmbyItem[] | undefined) {
  heroItems.value = (items || [])
    .filter((it) => it.BackdropImageTags?.length || it.ImageTags?.Primary)
    .slice(0, 5)
    .map((it) => ({
      id: it.Id,
      title: it.Name,
      meta: metaOf(it),
      rating: it.CommunityRating ?? null,
      backdrop: backdropUrl(it, 1600) || posterUrl(it, 1080),
      poster: posterUrl(it, 1080),
      overview: it.Overview || '',
    }))
}
function applyResume(res: { items: PortalResumeItem[] } | undefined) {
  resumeItems.value = res?.items || []
}
function applySections(res: { sections: HomeSection[] } | undefined) {
  const out: RowEntry[] = []
  for (const sec of res?.sections || []) {
    if (sec.key === 'continue_watching') continue
    const items = (sec.items || []) as Array<{
      guid?: string
      id?: number | string
      name: string
      production_year?: number | null
      poster_url?: string | null
    }>
    if (!items.length) continue
    out.push({
      key: `sec-${sec.key}`,
      status: 'ready',
      row: {
        key: `sec-${sec.key}`,
        title: sec.title,
        count: null,
        items: items.slice(0, 14).map((it): PosterRowData['items'][number] => ({
          id: String(it.guid || it.id || ''),
          title: it.name,
          meta: it.production_year ? String(it.production_year) : '',
          rating: null,
          image: it.poster_url ? imageAuthUrl(it.poster_url, 520) : '',
        })).filter((it) => it.id),
      },
    })
  }
  sectionRows.value = out
}
function buildViewRow(
  view: EmbyItem,
  res: { Items: EmbyItem[]; TotalRecordCount: number },
): PosterRowData | null {
  const items = (res.Items || []).map(toPoster)
  if (!items.length) return null
  return {
    key: `view-${view.Id}`,
    title: view.Name,
    count: res.TotalRecordCount || null,
    moreTo:
      view.CollectionType === 'movies'
        ? '/library/movies'
        : view.CollectionType === 'tvshows'
          ? '/library/series'
          : undefined,
    items,
  }
}
function applyViewRow(view: EmbyItem, res: { Items: EmbyItem[]; TotalRecordCount: number }) {
  const key = `view-${view.Id}`
  const entry = viewRows.value.find((r) => r.key === key)
  const row = buildViewRow(view, res)
  if (entry) {
    entry.row = row
    entry.status = 'ready'
    entry.view = view
    entry.title = view.Name
  } else {
    /* 静默刷新 / 缓存画完后的新库：直接以 ready 落位，不闪骨架 */
    viewRows.value.push({ key, status: 'ready', row, view, title: view.Name })
  }
}

/* ---------- 分腿加载：每条腿独立 settle、独立落画 ----------
   silent = 保活切回 / 缓存秒画后的后台刷新：不重置状态、不闪骨架，
   失败保留已有数据（状态已是 ready 时不降级成 error）。 */
let loadSeq = 0
let loadInFlight = false
let lastLoadedAt = 0

async function load(opts?: { silent?: boolean }) {
  if (loadInFlight) return
  loadInFlight = true
  const silent = !!opts?.silent
  const my = ++loadSeq
  if (!silent) {
    heroStatus.value = 'pending'
    resumeStatus.value = 'pending'
    sectionsStatus.value = 'pending'
    viewsStatus.value = 'pending'
    sectionRows.value = []
    viewRows.value = []
  }
  const raw: HomeRawData = {}
  let settledLegs = 0
  const legDone = () => {
    settledLegs += 1
    if (settledLegs === 4 && my === loadSeq) {
      loadInFlight = false
      lastLoadedAt = Date.now()
      writeHomeCache(raw)
    }
  }

  // Hero（Latest）
  void (async () => {
    try {
      const items = await fetchHome(() => embyProtocol.getLatest(12, { timeout: HOME_TIMEOUT_MS }))
      if (my !== loadSeq) return
      raw.latest = items
      applyLatest(items)
      heroStatus.value = 'ready'
    } catch {
      if (my === loadSeq && heroStatus.value !== 'ready') heroStatus.value = 'error'
    } finally {
      if (my === loadSeq) legDone()
    }
  })()

  // 继续观看
  void (async () => {
    try {
      const res = await fetchHome(() => embyApi.getResume(12, { timeout: HOME_TIMEOUT_MS }))
      if (my !== loadSeq) return
      raw.resume = res
      applyResume(res)
      resumeStatus.value = 'ready'
    } catch {
      if (my === loadSeq && resumeStatus.value !== 'ready') resumeStatus.value = 'error'
    } finally {
      if (my === loadSeq) legDone()
    }
  })()

  // 首页分区
  void (async () => {
    try {
      const res = await fetchHome(() => embyApi.getHomeSections({ timeout: HOME_TIMEOUT_MS }))
      if (my !== loadSeq) return
      raw.sections = res
      applySections(res)
      sectionsStatus.value = 'ready'
    } catch {
      if (my === loadSeq && sectionsStatus.value !== 'ready') sectionsStatus.value = 'error'
    } finally {
      if (my === loadSeq) legDone()
    }
  })()

  // 逐库行：先拿 Views，再逐库拉 items（每库独立 settle、独立落画 / 独立重试）
  void (async () => {
    try {
      const views = await fetchHome(() => embyProtocol.getViews({ timeout: HOME_TIMEOUT_MS }))
      if (my !== loadSeq) return
      raw.views = views
      raw.viewItems = {}
      const viewList = (views || []).slice(0, 8)
      if (!silent) {
        viewRows.value = viewList.map(
          (view): RowEntry => ({
            key: `view-${view.Id}`,
            status: 'pending',
            row: null,
            view,
            title: view.Name,
          }),
        )
      }
      await Promise.allSettled(
        viewList.map(async (view) => {
          try {
            const res = await fetchHome(() =>
              embyProtocol.getItems({
                parentId: view.Id,
                includeTypes: ['Movie', 'Series'],
                sortBy: 'DateCreated',
                sortOrder: 'Descending',
                limit: 14,
                timeout: HOME_TIMEOUT_MS,
              }),
            )
            if (my !== loadSeq) return
            if (raw.viewItems) raw.viewItems[view.Id] = res
            applyViewRow(view, res)
          } catch {
            if (my !== loadSeq) return
            const entry = viewRows.value.find((r) => r.key === `view-${view.Id}`)
            if (entry && entry.status !== 'ready') entry.status = 'error'
          }
        }),
      )
      if (my !== loadSeq) return
      if (silent) {
        /* 静默刷新收尾：已下线的库行撤掉，顺序按最新 Views 对齐 */
        const order = new Map<string, number>(viewList.map((v, i) => [`view-${v.Id}`, i] as const))
        viewRows.value = viewRows.value
          .filter((r) => order.has(r.key))
          .sort((a, b) => (order.get(a.key) ?? 0) - (order.get(b.key) ?? 0))
      }
      viewsStatus.value = 'ready'
    } catch {
      if (my === loadSeq && viewsStatus.value !== 'ready') viewsStatus.value = 'error'
    } finally {
      if (my === loadSeq) legDone()
    }
  })()
}

function reloadAll() {
  void load()
}

/* ---------- 行级重试（只重跑失败的那条腿 / 那一行） ---------- */
async function retryHero() {
  heroStatus.value = 'pending'
  try {
    const items = await fetchHome(() => embyProtocol.getLatest(12, { timeout: HOME_TIMEOUT_MS }))
    applyLatest(items)
    heroStatus.value = 'ready'
    patchHomeCache({ latest: items })
  } catch {
    heroStatus.value = 'error'
  }
}
async function retryResume() {
  resumeStatus.value = 'pending'
  try {
    const res = await fetchHome(() => embyApi.getResume(12, { timeout: HOME_TIMEOUT_MS }))
    applyResume(res)
    resumeStatus.value = 'ready'
    patchHomeCache({ resume: res })
  } catch {
    resumeStatus.value = 'error'
  }
}
async function retrySections() {
  sectionsStatus.value = 'pending'
  try {
    const res = await fetchHome(() => embyApi.getHomeSections({ timeout: HOME_TIMEOUT_MS }))
    applySections(res)
    sectionsStatus.value = 'ready'
    patchHomeCache({ sections: res })
  } catch {
    sectionsStatus.value = 'error'
  }
}
async function retryViews() {
  /* 列表腿整体重跑：走一次非静默 load（其余腿已 ready 的数据会被重新
     拉取并覆盖为最新，行为与整页重试一致但不撤已有画面） */
  viewsStatus.value = 'pending'
  viewRows.value = viewRows.value.filter((r) => r.status === 'ready')
  try {
    const views = await fetchHome(() => embyProtocol.getViews({ timeout: HOME_TIMEOUT_MS }))
    const viewList = (views || []).slice(0, 8)
    const pending = viewList.filter(
      (v) => !viewRows.value.some((r) => r.key === `view-${v.Id}` && r.status === 'ready'),
    )
    for (const view of pending) {
      viewRows.value.push({
        key: `view-${view.Id}`,
        status: 'pending',
        row: null,
        view,
        title: view.Name,
      })
    }
    const viewItems: Record<string, { Items: EmbyItem[]; TotalRecordCount: number }> = {}
    await Promise.allSettled(
      pending.map(async (view) => {
        try {
          const res = await fetchHome(() =>
            embyProtocol.getItems({
              parentId: view.Id,
              includeTypes: ['Movie', 'Series'],
              sortBy: 'DateCreated',
              sortOrder: 'Descending',
              limit: 14,
              timeout: HOME_TIMEOUT_MS,
            }),
          )
          viewItems[view.Id] = res
          applyViewRow(view, res)
        } catch {
          const entry = viewRows.value.find((r) => r.key === `view-${view.Id}`)
          if (entry && entry.status !== 'ready') entry.status = 'error'
        }
      }),
    )
    viewsStatus.value = 'ready'
    patchHomeCache({ views, viewItems })
  } catch {
    viewsStatus.value = 'error'
  }
}
async function retryViewRow(entry: RowEntry) {
  if (!entry.view) return
  const view = entry.view
  entry.status = 'pending'
  try {
    const res = await fetchHome(() =>
      embyProtocol.getItems({
        parentId: view.Id,
        includeTypes: ['Movie', 'Series'],
        sortBy: 'DateCreated',
        sortOrder: 'Descending',
        limit: 14,
        timeout: HOME_TIMEOUT_MS,
      }),
    )
    entry.row = buildViewRow(view, res)
    entry.status = 'ready'
    const cur = readHomeCache()
    if (cur) {
      patchHomeCache({ viewItems: { ...(cur.data.viewItems || {}), [view.Id]: res } })
    }
  } catch {
    entry.status = 'error'
  }
}

/* ---------- 缓存秒画：先把缓存 DTO 按同一套映射落画 ---------- */
function paintFromCache(payload: HomeCachePayload) {
  const d = payload.data
  if (d.latest) {
    applyLatest(d.latest)
    heroStatus.value = 'ready'
  }
  if (d.resume) {
    applyResume(d.resume)
    resumeStatus.value = 'ready'
  }
  if (d.sections) {
    applySections(d.sections)
    sectionsStatus.value = 'ready'
  }
  if (d.views) {
    viewRows.value = d.views.slice(0, 8).map((view) => {
      const res = d.viewItems?.[view.Id]
      return res
        ? {
            key: `view-${view.Id}`,
            status: 'ready' as LegStatus,
            row: buildViewRow(view, res),
            view,
            title: view.Name,
          }
        : {
            key: `view-${view.Id}`,
            status: 'pending' as LegStatus,
            row: null,
            view,
            title: view.Name,
          }
    })
    viewsStatus.value = viewRows.value.every((r) => r.status !== 'pending') ? 'ready' : 'pending'
  }
  lastLoadedAt = payload.ts
}

onMounted(() => {
  const cached = readHomeCache()
  if (cached) {
    /* SWR：缓存先秒画，后台静默刷新替换 */
    paintFromCache(cached)
    void load({ silent: true })
  } else {
    void load()
  }
})

/* 保活切回：数据还新鲜（< 5 分钟）就不重拉；过期才静默刷新，不闪骨架 */
onActivated(() => {
  if (loadInFlight) return
  if (lastLoadedAt && Date.now() - lastLoadedAt > HOME_REFRESH_TTL_MS) {
    void load({ silent: true })
  }
})
</script>

<template>
  <StreamShell>
    <!-- 骨架屏（录屏形态）：灰色圆角块 + 居中站标占位，尺寸与真实卡一致，
         内容到位后整体淡入替换，不白屏不跳动 -->
    <div v-if="showFullSkeleton" class="sl-skel" role="status" aria-label="正在加载">
      <div class="sl-skel__hero"><span class="sl-skel__logo">A</span></div>
      <div class="sl-skel__rowhead"><span class="sl-skel__bar" style="width: 120px"></span></div>
      <div class="sl-skel__track">
        <div v-for="i in 5" :key="`p${i}`" class="sl-skel__poster">
          <span class="sl-skel__logo">A</span>
        </div>
      </div>
      <div class="sl-skel__rowhead"><span class="sl-skel__bar" style="width: 88px"></span></div>
      <div class="sl-skel__track">
        <div v-for="i in 5" :key="`q${i}`" class="sl-skel__poster">
          <span class="sl-skel__logo">A</span>
        </div>
      </div>
    </div>
    <div v-else class="sl-content">
      <!-- Hero：pending 显示同尺寸骨架块；失败给行级重试（不挡其他行） -->
      <div
        v-if="heroStatus === 'pending'"
        class="sl-skel sl-skel--heroonly"
        role="status"
        aria-label="正在加载"
      >
        <div class="sl-skel__hero"><span class="sl-skel__logo">A</span></div>
      </div>
      <StreamHero v-else-if="heroItems.length" :items="heroItems" @play="onHeroPlay" />
      <div v-else-if="heroStatus === 'error' && !showFatalError" class="sl-rowerror">
        <span>推荐内容加载失败</span>
        <button type="button" class="sl-retry" @click="retryHero">重试</button>
      </div>

      <!-- 继续观看：pending 显示 16:9 行骨架；失败给行级重试 -->
      <div v-if="resumeStatus === 'pending'" class="sl-rowskel" role="status" aria-label="正在加载">
        <div class="sl-skel__rowhead"><span class="sl-skel__bar" style="width: 120px"></span></div>
        <div class="sl-rowskel__track">
          <div v-for="i in 3" :key="`c${i}`" class="sl-skel__ccard">
            <span class="sl-skel__logo">A</span>
          </div>
        </div>
      </div>
      <!-- 继续观看：16:9 卡约 340×191、圆角 12、无进度条（存档第 3 节）；
           录屏补充：标题带总数、卡面右上垃圾桶（隐藏）、进度文字行 -->
      <section v-else-if="resumeItems.length" class="sl-continue">
        <div class="sl-continue__head">
          <h2 class="sl-continue__title">
            继续观看 <span class="sl-continue__count">{{ resumeItems.length }} 部</span>
          </h2>
        </div>
        <div class="sl-continue__track">
          <RouterLink
            v-for="m in resumeItems"
            :key="m.id"
            class="sl-ccard"
            :to="{ name: 'media-detail', params: { id: m.id } }"
          >
            <span class="sl-ccard__thumb">
              <img
                v-if="resumeImage(m) && !brokenResumeThumbs.has(m.id)"
                :src="resumeImage(m)"
                alt=""
                loading="lazy"
                @error="brokenResumeThumbs.add(m.id)"
              />
              <Clapperboard
                v-else
                :size="30"
                :stroke-width="1.5"
                class="sl-ccard__fallback"
                aria-hidden="true"
              />
              <button
                type="button"
                class="sl-ccard__trash"
                :disabled="hidingId === m.id"
                aria-label="从继续观看移除"
                title="从继续观看移除"
                @click.prevent.stop="hideResume(m)"
              >
                <Trash2 :size="15" />
              </button>
            </span>
            <span class="sl-ccard__title">{{ resumeTitle(m) }}</span>
            <span class="sl-ccard__sub">{{ resumeSub(m) }}</span>
            <span v-if="resumeProgress(m)" class="sl-ccard__prog">{{ resumeProgress(m) }}</span>
          </RouterLink>
        </div>
      </section>
      <div v-if="resumeStatus === 'error' && !showFatalError" class="sl-rowerror">
        <span>继续观看加载失败</span>
        <button type="button" class="sl-retry" @click="retryResume">重试</button>
      </div>

      <!-- 分区行：pending 显示一行海报骨架；失败给行级重试 -->
      <div v-if="sectionsStatus === 'pending'" class="sl-rowskel" role="status" aria-label="正在加载">
        <div class="sl-skel__rowhead"><span class="sl-skel__bar" style="width: 120px"></span></div>
        <div class="sl-skel__track">
          <div v-for="i in 5" :key="`s${i}`" class="sl-skel__poster">
            <span class="sl-skel__logo">A</span>
          </div>
        </div>
      </div>
      <template v-else-if="sectionsStatus === 'ready'">
        <template v-for="entry in sectionRows" :key="entry.key">
          <StreamRow
            v-if="entry.row"
            :title="entry.row.title"
            :count="entry.row.count"
            :more-to="entry.row.moreTo"
          >
            <StreamPosterCard
              v-for="item in entry.row.items"
              :key="item.id"
              :title="item.title"
              :meta="item.meta"
              :rating="item.rating"
              :image="item.image"
              :to="`/media/${item.id}`"
            />
          </StreamRow>
        </template>
      </template>
      <div v-else-if="sectionsStatus === 'error' && !showFatalError" class="sl-rowerror">
        <span>分区内容加载失败</span>
        <button type="button" class="sl-retry" @click="retrySections">重试</button>
      </div>

      <!-- 逐库行：Views 未到先一行骨架占位；到后每库一行，各自骨架 / 内容 / 重试 -->
      <div
        v-if="viewsStatus === 'pending' && !viewRows.length"
        class="sl-rowskel"
        role="status"
        aria-label="正在加载"
      >
        <div class="sl-skel__rowhead"><span class="sl-skel__bar" style="width: 88px"></span></div>
        <div class="sl-skel__track">
          <div v-for="i in 5" :key="`p${i}`" class="sl-skel__poster">
            <span class="sl-skel__logo">A</span>
          </div>
        </div>
      </div>
      <template v-for="entry in viewRows" :key="entry.key">
        <div v-if="entry.status === 'pending'" class="sl-rowskel" role="status" aria-label="正在加载">
          <div class="sl-skel__rowhead"><span class="sl-skel__bar" style="width: 88px"></span></div>
          <div class="sl-skel__track">
            <div v-for="i in 5" :key="`v${i}`" class="sl-skel__poster">
              <span class="sl-skel__logo">A</span>
            </div>
          </div>
        </div>
        <StreamRow
          v-else-if="entry.status === 'ready' && entry.row"
          :title="entry.row.title"
          :count="entry.row.count"
          :more-to="entry.row.moreTo"
        >
          <StreamPosterCard
            v-for="item in entry.row.items"
            :key="item.id"
            :title="item.title"
            :meta="item.meta"
            :rating="item.rating"
            :image="item.image"
            :to="`/media/${item.id}`"
          />
        </StreamRow>
        <div v-else-if="entry.status === 'error' && !showFatalError" class="sl-rowerror">
          <span>「{{ entry.title }}」加载失败</span>
          <button type="button" class="sl-retry" @click="retryViewRow(entry)">重试</button>
        </div>
      </template>
      <div v-if="viewsStatus === 'error' && !showFatalError" class="sl-rowerror">
        <span>片库列表加载失败</span>
        <button type="button" class="sl-retry" @click="retryViews">重试</button>
      </div>

      <!-- 全部为空 / 全失败才出整页错误态 -->
      <div v-if="showFatalError" class="sl-error">
        片库暂时没有拉到内容<br />
        <button type="button" class="sl-retry" @click="reloadAll">重试</button>
      </div>
      <div style="height: 40px"></div>
    </div>

    <PlayerSchemeSheet v-model:open="pickerOpen" :title="pickerTitle" :item-id="pickerItemId" />
  </StreamShell>
</template>

<style scoped>
/* 内容不做整块入场淡入：0.45s 的半透明中间帧会被看成封面重影
   （2026-11-13 录屏逐帧实证），骨架→内容直接切换。
   契约 scripts/check_no_ghost_entrance.py 守住，不许加回。 */
/* 骨架屏：灰块尺寸与真实卡一致（Hero 占位 + 海报行占位） */
.sl-skel {
  padding: 76px clamp(16px, 5vw, 96px) 40px;
}
.sl-skel__hero {
  height: min(560px, 62vh);
  border-radius: 16px;
  background: var(--uhd-s-card-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: sl-pulse 1.4s ease-in-out infinite;
}
.sl-skel__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: var(--uhd-s-border);
  color: var(--uhd-s-text-2);
  font-size: 20px;
  font-weight: var(--uhd-fw-black);
}
.sl-skel__rowhead {
  margin: 34px 0 14px;
}
.sl-skel__bar {
  display: block;
  height: 20px;
  border-radius: 6px;
  background: var(--uhd-s-card-soft);
}
.sl-skel__track {
  display: flex;
  gap: 25px;
  overflow: hidden;
}
.sl-skel__poster {
  flex: 0 0 260px;
  aspect-ratio: 2 / 3;
  border-radius: 13px;
  background: var(--uhd-s-card-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: sl-pulse 1.4s ease-in-out infinite;
}
@keyframes sl-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}
@media (max-width: 767px) {
  .sl-skel {
    /* 手机端首页已无整条顶栏（浮动胶囊不占流），骨架不再让 76px */
    padding-top: 20px;
  }
  .sl-skel__poster {
    flex-basis: 152px;
  }
  .sl-skel__hero {
    height: 300px;
  }
}
.sl-continue {
  margin-top: 8px;
}
.sl-continue__head {
  padding: 0 clamp(16px, 5vw, 96px);
}
.sl-continue__title {
  margin: 0;
  font-size: 29px;
  font-weight: var(--uhd-fw-extrabold);
  color: var(--uhd-s-text);
}
.sl-continue__track {
  display: flex;
  /* 卡间距：友站真机截图实测约 12（初版 20 偏空） */
  gap: 12px;
  margin-top: 18px;
  padding: 0 clamp(16px, 5vw, 96px) 8px;
  overflow-x: auto;
  scrollbar-width: none;
  /* 两端渐隐：与海报横滑行同口径（mask 淡出到页面底色） */
  -webkit-mask-image: linear-gradient(to right, transparent 0, #000 36px, #000 calc(100% - 36px), transparent 100%);
  mask-image: linear-gradient(to right, transparent 0, #000 36px, #000 calc(100% - 36px), transparent 100%);
}
.sl-continue__track::-webkit-scrollbar {
  display: none;
}
.sl-ccard {
  flex: 0 0 340px;
  width: 340px;
  text-decoration: none;
  color: var(--uhd-s-text);
}
.sl-ccard__thumb {
  position: relative;
  display: block;
  aspect-ratio: 16 / 9;
  border-radius: 12px;
  overflow: hidden;
  background: var(--uhd-s-card-soft);
}
/* 卡面右上垃圾桶（录屏形态）：半透明深色圆钮 */
.sl-ccard__trash {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.45);
  color: #ffffff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.sl-ccard__trash:disabled {
  opacity: 0.5;
}
.sl-continue__count {
  font-size: 14px;
  font-weight: var(--uhd-fw-regular);
  color: var(--uhd-s-text-2);
}
.sl-ccard__prog {
  display: block;
  margin-top: 2px;
  font-size: 13px;
  color: var(--uhd-s-text-2);
}
.sl-ccard__thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
/* 无图 / 加载失败占位（与详情页、海报卡同款 Clapperboard，居中落在底色上） */
.sl-ccard__fallback {
  position: absolute;
  inset: 0;
  margin: auto;
  color: var(--uhd-s-text-2);
}
.sl-ccard__title {
  display: block;
  margin-top: 12px;
  font-size: 17px;
  font-weight: var(--uhd-fw-bold);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sl-ccard__sub {
  display: block;
  margin-top: 3px;
  font-size: 14px;
  color: var(--uhd-s-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sl-error {
  margin: 60px clamp(16px, 5vw, 96px);
  font-size: 15px;
  color: var(--uhd-s-text-2);
  text-align: center;
}
.sl-retry {
  margin-top: 12px;
  padding: 8px 22px;
  border-radius: 999px;
  border: 1px solid var(--uhd-s-border);
  background: var(--uhd-s-pill-active-bg);
  color: var(--uhd-s-text);
  font-size: 14px;
  cursor: pointer;
}
/* 渐进渲染：行级骨架与行级错误（单条腿 pending / 失败时只占自己那一行，
   不再整页遮挡） */
.sl-skel--heroonly {
  padding-bottom: 0;
}
.sl-rowskel {
  padding: 0 clamp(16px, 5vw, 96px);
}
.sl-rowskel .sl-skel__rowhead {
  margin: 26px 0 14px;
}
.sl-rowskel__track {
  display: flex;
  gap: 12px;
  overflow: hidden;
}
.sl-skel__ccard {
  flex: 0 0 340px;
  aspect-ratio: 16 / 9;
  border-radius: 12px;
  background: var(--uhd-s-card-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: sl-pulse 1.4s ease-in-out infinite;
}
.sl-rowerror {
  margin: 26px clamp(16px, 5vw, 96px) 0;
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 14px;
  color: var(--uhd-s-text-2);
}
.sl-rowerror .sl-retry {
  margin-top: 0;
}
@media (max-width: 767px) {
  .sl-rowskel {
    padding: 0 16px;
  }
  .sl-skel__ccard {
    flex-basis: 220px;
  }
  .sl-rowerror {
    margin-left: 16px;
    margin-right: 16px;
  }
}
@media (max-width: 767px) {
  /* 内容左右内距：友站实测 16（clamp 在 390 宽下给 19.5，偏大） */
  .sl-continue__head {
    padding: 0 16px;
  }
  .sl-continue__track {
    padding: 0 16px 8px;
  }
  .sl-continue__title {
    font-size: 22px;
  }
  /* D2-06 复核：录屏帧实测原站继续观看卡约占屏宽 55%（≈216pt@390），取 220 */
  .sl-ccard {
    flex-basis: 220px;
    width: 220px;
  }
}
</style>
