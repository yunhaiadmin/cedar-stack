<!-- 播放前弹层（批 3 录屏形态）：底部升起的大圆角弹层——
     顶部剧照头图 + 选集播放小字 + 标题（仅手机；桌面存档弹窗无标题头）；
     影片版本单选卡（单选圆点 + 规格 chip + 码率·大小·时长 + 复制/展开
     圆钮）；彩虹边框「开始播放 · 版本名」（网页播放器主入口）；
     第三方播放器以「用播放器打开」行列保留（既有业务入口，点行即打开）。
     D3-05 裁定：不自创「播放方式」下拉；Aetrix 无独立线路数据，
     原站的「播放线路」段整段省略（理由见批 3 对照清单）。
     口径：未绑盘先出绑盘引导（门禁不变）；第三方播放器走现行导入
     scheme（pick）；网页播放走播放器单契约（PR #85）：路由名
     web-player（/play/:id），params.id=起播目标 id（剧集为集 id）、
     query.ms=选定 MediaSource Id（可选）、query.at=起始秒（可选，
     本弹层不传、播放器用续看位置），路由未落地时点按给诚实提示。 -->
<template>
  <Teleport to="body">
    <div v-if="open" class="psp" @keydown.esc.prevent="close">
      <button type="button" class="psp-mask" aria-label="关闭" @click="close"></button>
      <div
        ref="panelEl"
        class="psp-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="psp-title"
        tabindex="-1"
      >
        <!-- 关闭钮挂在弹层本体上（桌面无头图时也必须在） -->
        <button type="button" class="psp-x" aria-label="关闭" @click="close">
          <X :size="17" />
        </button>
        <!-- 头图区（仅手机显示） -->
        <div class="psp-hero">
          <img v-if="heroImg" :src="heroImg" alt="" class="psp-hero__img" />
          <div class="psp-hero__fade" aria-hidden="true"></div>
          <div class="psp-hero__meta">
            <span class="psp-hero__label">{{ heroLabel }}</span>
            <div id="psp-title" class="psp-hero__title">{{ heroTitle }}</div>
            <div v-if="heroSub" class="psp-hero__sub">{{ heroSub }}</div>
          </div>
        </div>

        <div class="psp-body">
          <PlayStatusBar v-if="!needsBinding && itemId !== null && itemId !== undefined" :item-id="itemId" />

          <div v-if="loading" class="psp-loading" role="status">
            <span class="psp-spinner" aria-hidden="true"></span> 正在获取资源和线路…
          </div>

          <BindDriveGuide v-else-if="needsBinding" :title="title" @navigate="close" />

          <template v-else>
            <!-- 影片版本（单选） -->
            <div class="psp-sec-head">
              <span>影片版本</span>
              <span v-if="versions.length" class="psp-sec-count">{{ versions.length }} 个可选版本</span>
            </div>
            <div v-if="versions.length" class="psp-vers">
              <div
                v-for="v in versions"
                :key="v.id"
                class="psp-ver"
                :class="{ 'psp-ver--on': v.id === selectedVersionId }"
                role="radio"
                :aria-checked="v.id === selectedVersionId"
                tabindex="0"
                @click="selectedVersionId = v.id"
                @keydown.enter.prevent="selectedVersionId = v.id"
              >
                <span class="psp-ver__radio" aria-hidden="true"></span>
                <div class="psp-ver__main">
                  <div class="psp-ver__name">{{ v.name }}</div>
                  <div v-if="v.chips.length" class="psp-ver__chips">
                    <span v-for="c in v.chips" :key="c" class="psp-ver__chip">{{ c }}</span>
                  </div>
                  <div v-if="v.meta" class="psp-ver__meta">{{ v.meta }}</div>
                  <div v-if="expandedId === v.id && v.streams.length" class="psp-ver__streams">
                    <div v-for="(s, i) in v.streams" :key="i">{{ s }}</div>
                  </div>
                </div>
                <div class="psp-ver__tools">
                  <button type="button" class="psp-tool" aria-label="复制片名去播放器搜" title="复制片名" @click.stop="copyTitle">
                    <Copy :size="15" />
                  </button>
                  <button
                    v-if="v.streams.length"
                    type="button"
                    class="psp-tool"
                    :aria-label="expandedId === v.id ? '收起轨道详情' : '展开轨道详情'"
                    @click.stop="expandedId = expandedId === v.id ? '' : v.id"
                  >
                    <ChevronDown :size="15" :style="{ transform: expandedId === v.id ? 'rotate(180deg)' : '' }" />
                  </button>
                </div>
              </div>
            </div>
            <p v-else class="psp-note">这个条目的版本信息没拉到，开始播放会用默认版本。</p>

            <!-- 开始播放：彩虹边框全宽钮（网页播放器主入口，契约 web-player） -->
            <div class="psp-start-sec">
              <span class="psp-start-label">开始播放</span>
              <button type="button" class="psp-start" @click="start">
                <Play :size="17" fill="currentColor" />
                开始播放<template v-if="selectedVersion"> · {{ selectedVersion.name }}</template>
              </button>
              <p class="psp-tip">
                在网页里直接播放；个别版本浏览器播不了时，播放页会提示你换个版本或改用下面的播放器打开。
              </p>
            </div>

            <!-- 第三方播放器（既有业务入口）：点行即按现行 scheme 打开 -->
            <div v-if="schemes.length" class="psp-sec-head">
              <span>用播放器打开</span>
            </div>
            <div v-if="schemes.length" class="psp-players">
              <button
                v-for="s in schemes"
                :key="s.name"
                type="button"
                class="psp-player"
                @click="pick(s)"
              >
                <span class="psp-player__name">{{ s.name }}</span>
                <ChevronRight :size="16" />
              </button>
            </div>
            <p v-if="schemes.length" class="psp-tip">
              首播说明：没转存过的片子，用播放器首播会先转存到你自己的盘，不走共享池。
              播放器里转圈、报 409（转存还没完成）或 503（转存失败）时，关掉播放器回到这里再点一次就是重试，不用重绑。
            </p>
            <p v-if="schemes.length === 0" class="psp-note">
              还没有导入第三方播放器；要导入去
              <RouterLink to="/game/operator" @click="close">播放账号页</RouterLink>。
            </p>
          </template>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ChevronDown, ChevronRight, Copy, Play, X } from 'lucide-vue-next'
import BindDriveGuide from '@/components/panel/BindDriveGuide.vue'
import { useToast } from '@/composables/useToast'
import PlayStatusBar from '@/components/panel/PlayStatusBar.vue'
import { storageBindingApi } from '@/api/economy'
import {
  embyApi as embyProtocol,
  backdropUrl,
  formatDuration,
  ticksToSeconds,
  type EmbyItem,
} from '@/api/emby'
import {
  assignImportScheme,
  loadImportSchemes,
  type PlayerScheme,
} from '@/composables/usePlayerSchemes'

const open = defineModel<boolean>('open', { default: false })
const props = defineProps<{
  title?: string
  /** 条目 id（可选）：给了才拉版本/头图并展示起播状态条 */
  itemId?: string | number | null
}>()

const toast = useToast()
const router = useRouter()
const schemes = ref<PlayerScheme[]>([])
const loading = ref(false)
// 播放门禁引导：名下无 active 绑定时为 true，弹层只展示绑定引导，
// 不直接播放（播也会被服务端门禁拦下）。
const needsBinding = ref(false)
const panelEl = ref<HTMLElement | null>(null)
let jumpTimer = 0

/* ---------- 条目与版本（详情 DTO 口径，不新造接口） ---------- */
const itemDetail = ref<EmbyItem | null>(null)
/** 版本来源：电影/单集 = 条目本身；剧集 = 续看集（无续看取第一集）的详情 */
const versionSource = ref<EmbyItem | null>(null)
/** 实际起播目标 id（剧集时为集 id），web-player 契约用它 */
const playTargetId = ref('')
const epInfo = ref<{ season?: number | null; episode?: number | null; name?: string } | null>(null)

interface VersionCard {
  id: string
  name: string
  chips: string[]
  meta: string
  streams: string[]
}

function resChip(height?: number | null): string {
  if (!height) return ''
  if (height >= 2000) return '4K'
  if (height >= 1000) return '1080p'
  if (height >= 700) return '720p'
  return `${height}p`
}
function sizeText(bytes?: number | null): string {
  if (!bytes) return ''
  const gb = bytes / 1_000_000_000
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1_000_000))} MB`
}

const versions = computed<VersionCard[]>(() => {
  const it = versionSource.value || itemDetail.value
  if (!it) return []
  if (it.Versions?.length) {
    return it.Versions.map((v): VersionCard => ({
      id: v.Id,
      name: v.VersionLabel || v.Name || '默认版本',
      chips: [resChip(v.Height), v.Container ? v.Container.toUpperCase() : ''].filter(Boolean),
      meta: sizeText(v.SizeBytes),
      streams: [],
    }))
  }
  return (it.MediaSources || []).map((s, i) => {
    const video = (s.MediaStreams || []).find((x) => x.Type === 'Video')
    const chips = [
      resChip(video?.Height ?? null),
      video?.VideoRange || '',
      video?.Codec ? video.Codec.toUpperCase() : '',
      s.Container ? s.Container.toUpperCase() : '',
    ].filter(Boolean)
    const metaParts: string[] = []
    if (s.Bitrate) metaParts.push(`${(s.Bitrate / 1_000_000).toFixed(1)} Mbps`)
    if (s.Size) metaParts.push(sizeText(s.Size))
    const ticks = s.RunTimeTicks || it.RunTimeTicks
    if (ticks) metaParts.push(formatDuration(ticksToSeconds(ticks)))
    return {
      id: s.Id,
      name: s.Name || video?.DisplayTitle || `版本 ${i + 1}`,
      chips: [...new Set(chips)],
      meta: metaParts.join(' · '),
      streams: (s.MediaStreams || []).map((x) => x.DisplayTitle || `${x.Type} ${x.Codec || ''}`).filter(Boolean),
    }
  })
})

const selectedVersionId = ref('')
const selectedVersion = computed(() => versions.value.find((v) => v.id === selectedVersionId.value) || null)
const expandedId = ref('')

/* ---------- 头图区文案 ---------- */
const heroImg = computed(() => (itemDetail.value ? backdropUrl(itemDetail.value, 1280) : ''))
const heroTitle = computed(() => props.title || itemDetail.value?.Name || '播放')
const heroLabel = computed(() => {
  const t = itemDetail.value?.Type
  return t === 'Episode' || t === 'Series' ? '选集播放' : '播放'
})
const heroSub = computed(() => {
  const it = itemDetail.value
  if (!it) return ''
  if (it.Type === 'Episode') {
    const s = it.ParentIndexNumber
    const e = it.IndexNumber
    const series = (it as { SeriesName?: string }).SeriesName || props.title || ''
    return [series, s != null && e != null ? `第 ${s} 季第 ${e} 集` : ''].filter(Boolean).join(' · ')
  }
  if (it.Type === 'Series' && epInfo.value) {
    const { season: s, episode: e, name } = epInfo.value
    return [s != null && e != null ? `第 ${s} 季第 ${e} 集` : '', name || ''].filter(Boolean).join(' · ')
  }
  const parts: string[] = []
  if (it.ProductionYear) parts.push(String(it.ProductionYear))
  if (it.Type === 'Series' && it.ChildCount) parts.push(`${it.ChildCount} 季`)
  if (it.Genres?.length) parts.push(it.Genres.slice(0, 2).join(' / '))
  return parts.join(' · ')
})

watch(open, (now) => {
  if (!now) return
  expandedId.value = ''
  void refresh()
  void nextTick(() => panelEl.value?.focus())
})

async function refresh() {
  loading.value = true
  try {
    const id = props.itemId
    const [schemesResult, bindingsResult, itemResult] = await Promise.allSettled([
      loadImportSchemes(),
      storageBindingApi.list(),
      id !== null && id !== undefined && String(id) !== ''
        ? embyProtocol.getItem(String(id))
        : Promise.resolve(null),
    ])
    if (schemesResult.status === 'fulfilled') schemes.value = schemesResult.value
    // 绑定列表拉取失败时保持旧行为（服务端门禁仍会拦截），引导只是尽力而为。
    if (bindingsResult.status === 'fulfilled') {
      const bindings = bindingsResult.value?.bindings ?? []
      needsBinding.value = !bindings.some((b) => b.status === 'active')
    } else {
      needsBinding.value = false
    }
    itemDetail.value = itemResult.status === 'fulfilled' ? itemResult.value : null
    versionSource.value = itemDetail.value
    playTargetId.value = id !== null && id !== undefined ? String(id) : ''
    epInfo.value = null
    // 剧集本身没有版本：版本挂在集上，取续看集（无续看取第一集）
    if (itemDetail.value?.Type === 'Series') {
      try {
        const eps = await embyProtocol.getEpisodes(itemDetail.value.Id)
        const ep =
          eps.find((e) => (e.UserData?.PlaybackPositionTicks ?? 0) > 0) ||
          eps.find((e) => !e.UserData?.Played && (e.UserData?.PlayedPercentage ?? 0) > 0) ||
          eps.find((e) => !e.UserData?.Played) ||
          eps[0]
        if (ep) {
          epInfo.value = {
            season: ep.ParentIndexNumber,
            episode: ep.IndexNumber,
            name: ep.Name,
          }
          playTargetId.value = ep.Id
          const epDetail = await embyProtocol.getItem(ep.Id)
          if (epDetail) versionSource.value = epDetail
        }
      } catch { /* 集列表拉不到就只给线路，版本区留空 */ }
    }
    const primary = versionSource.value?.Versions?.find((v) => v.IsPrimary)
    selectedVersionId.value =
      primary?.Id || versions.value[0]?.id || ''
  } finally {
    loading.value = false
  }
}

function close() {
  window.clearTimeout(jumpTimer)
  open.value = false
}

async function copyTitle() {
  const t = (props.title || itemDetail.value?.Name || '').trim()
  if (!t) return
  try {
    await navigator.clipboard.writeText(t)
    toast.success('片名已复制，去播放器里粘贴搜索')
  } catch {
    toast.info('复制失败，请手动复制上面的片名')
  }
}

async function pick(s: PlayerScheme) {
  // 跳转前先把提示留在页面上：没装 App 时 scheme 不会离开本页
  toast.info(`正在打开${s.name}。没装的话去播放账号页导入`)
  await nextTick()
  await new Promise<void>((resolve) => {
    jumpTimer = window.setTimeout(resolve, 600)
  })
  if (!open.value) return
  if (!assignImportScheme(s.url)) toast.info('没装的话去播放账号页导入')
}

/**
 * 开始播放（网页播放器主入口）：走播放器单契约 —— 路由名
 * web-player、params.id=起播目标 id（剧集为集 id）、query.ms=
 * 选定版本 MediaSource Id（PR #85）。路由未落地时不假装能播，
 * 给诚实提示。第三方播放器走下方播放器行 pick。
 */
async function start() {
  const id = playTargetId.value || props.itemId
  if (id === null || id === undefined || String(id) === '') {
    toast.info('这个入口没有带条目，暂时只能用播放器打开')
    return
  }
  if (router.hasRoute('web-player')) {
    close()
    void router.push({
      name: 'web-player',
      params: { id: String(id) },
      query: selectedVersionId.value ? { ms: selectedVersionId.value } : {},
    })
  } else {
    toast.info('网页播放器马上就来；先用下面的播放器打开')
  }
}
</script>

<style scoped>
/* 批 3 播放前弹层（录屏形态）：手机 = 悬浮大卡（左右/底部留 12、圆角 28）；
   桌面 = 居中 610 宽、圆角 24（存档第 7 节）。头图 + 版本单选卡 +
   版本单选卡 + 彩虹边框开始播放钮 + 第三方播放器行列（D3-05 后形态）。
   Teleport 到 body：浅色走 --uhd-l-* :root 令牌；片库面深色态由
   StreamShell 在 body 打 .uhd-stream-dark 标记桥接深色值。 */
.psp {
  position: fixed;
  inset: 0;
  z-index: 90;
}
/* 弹层 Teleport 在 .stream-scope 之外，全局 mobile.css 的 min-height:44px
   触控规则会撑高关闭钮等小控件——在弹层自己的作用域里中和（同 StreamShell 口径） */
.psp button,
.psp a {
  min-height: 0;
}
.psp-mask {
  position: absolute;
  inset: 0;
  border: 0;
  padding: 0;
  background: rgba(10, 10, 12, 0.45);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  cursor: pointer;
}
.psp-sheet {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: calc(env(safe-area-inset-bottom) + 12px);
  background: var(--uhd-l-surface, #ffffff);
  color: var(--uhd-l-text, #111111);
  border-radius: 28px;
  max-height: 88dvh;
  overflow: auto;
  font-family: var(--uhd-font-sans, sans-serif);
  box-shadow: var(--uhd-shadow-modal, 0 24px 64px -24px rgba(0, 0, 0, 0.35));
}
/* 头图区 */
.psp-hero {
  position: relative;
  height: 196px;
  background: linear-gradient(120deg, #3a3f4a, #191b20);
  overflow: hidden;
}
.psp-hero__img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.psp-hero__fade {
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, rgba(0, 0, 0, 0.05) 40%, rgba(0, 0, 0, 0.55) 100%);
}
.psp-x {
  position: absolute;
  top: 14px;
  right: 14px;
  z-index: 2;
  width: 38px;
  height: 38px;
  border: none;
  border-radius: 50%;
  background: rgba(20, 20, 22, 0.5);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  color: #ffffff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.psp-hero__meta {
  position: absolute;
  left: 20px;
  right: 20px;
  bottom: 16px;
  z-index: 1;
  color: #ffffff;
}
.psp-hero__label {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.85);
}
.psp-hero__title {
  margin-top: 4px;
  font-size: 24px;
  font-weight: 700;
  line-height: 1.25;
}
.psp-hero__sub {
  margin-top: 4px;
  font-size: 13.5px;
  color: rgba(255, 255, 255, 0.78);
}
.psp-body {
  padding: 18px 20px 22px;
}
/* 加载态：正在获取资源和线路 */
.psp-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 26px 0;
  font-size: 14.5px;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-spinner {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 2px solid var(--uhd-l-border, #e5e5e5);
  border-top-color: var(--uhd-l-text, #111111);
  animation: uhd-spin 0.8s linear infinite;
}
/* 版本区 */
.psp-sec-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  font-size: 15px;
  font-weight: 700;
  margin-top: 6px;
}
.psp-sec-count {
  font-size: 13px;
  font-weight: 400;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-vers {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 12px;
}
.psp-ver {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 14px 16px;
  border-radius: 16px;
  border: 1.5px solid var(--uhd-l-border, #e5e5e5);
  background: var(--uhd-l-input-bg, #f5f5f5);
  cursor: pointer;
}
.psp-ver--on {
  border-color: var(--uhd-l-text, #111111);
  background: var(--uhd-l-surface, #ffffff);
}
.psp-ver__radio {
  flex: none;
  width: 20px;
  height: 20px;
  margin-top: 2px;
  border-radius: 50%;
  border: 2px solid #b6bcc4;
  position: relative;
}
.psp-ver--on .psp-ver__radio {
  border-color: var(--uhd-l-text, #111111);
}
.psp-ver--on .psp-ver__radio::after {
  content: '';
  position: absolute;
  inset: 3px;
  border-radius: 50%;
  background: var(--uhd-l-text, #111111);
}
.psp-ver__main {
  flex: 1 1 auto;
  min-width: 0;
}
.psp-ver__name {
  font-size: 15.5px;
  font-weight: 700;
}
.psp-ver__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 7px;
}
.psp-ver__chip {
  font-size: 11.5px;
  line-height: 1;
  padding: 4px 8px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.07);
  color: var(--uhd-l-text, #111111);
}
.psp-ver__meta {
  margin-top: 7px;
  font-size: 13px;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-ver__streams {
  margin-top: 8px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-ver__tools {
  flex: none;
  display: flex;
  gap: 8px;
}
.psp-tool {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 1px solid var(--uhd-l-border, #e5e5e5);
  background: var(--uhd-l-surface, #ffffff);
  color: var(--uhd-l-text-2, #6b7280);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  padding: 0;
}
.psp-note {
  margin: 12px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-note a {
  color: var(--uhd-l-text, #111111);
  font-weight: 700;
}
.psp-tip {
  margin: 14px 0 0;
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--uhd-l-text-2, #6b7280);
}
/* 第三方播放器行列（既有业务入口，点行即打开） */
.psp-players {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 12px;
}
.psp-player {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  min-height: 52px;
  padding: 10px 16px;
  border-radius: 16px;
  border: 1.5px solid var(--uhd-l-border, #e5e5e5);
  background: var(--uhd-l-surface, #ffffff);
  color: var(--uhd-l-text, #111111);
  font-size: 15px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  text-align: left;
}
.psp-player:active {
  transform: scale(0.99);
}
/* 开始播放 */
.psp-start-sec {
  margin-top: 20px;
}
.psp-start-label {
  font-size: 13px;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-start {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  height: 52px;
  margin-top: 8px;
  /* D3-05：原站为白底＋彩虹细边（非渐变填充），与 Hero 播放钮同技术 */
  border: 2px solid transparent;
  border-radius: 26px;
  background:
    linear-gradient(var(--uhd-l-surface, #ffffff), var(--uhd-l-surface, #ffffff)) padding-box,
    var(--uhd-rainbow) border-box;
  color: var(--uhd-l-text, #111111);
  font-size: 16px;
  font-weight: 700;
  font-family: inherit;
  cursor: pointer;
}
.psp-start:active {
  transform: scale(0.99);
}
.psp-sheet:focus-visible,
.psp-x:focus-visible,
.psp-tool:focus-visible,
.psp-player:focus-visible,
.psp-start:focus-visible {
  outline: 2px solid var(--uhd-l-link, #2563eb);
  outline-offset: 2px;
}
@media (min-width: 768px) {
  .psp-sheet {
    left: 50%;
    right: auto;
    bottom: auto;
    top: 50%;
    transform: translate(-50%, -50%);
    width: min(610px, calc(100vw - 48px));
    border-radius: 24px;
  }
  /* D3-05：原站桌面弹窗无标题头（存档 610 弹窗只有版本/线路/开始播放），
     剧照头图只保留在手机底部弹层（录屏形态） */
  .psp-hero {
    display: none;
  }
  /* 关闭钮浮在弹层右上，版块头右侧计数给它让位 */
  .psp-sec-head {
    padding-right: 44px;
  }
  .psp-body {
    padding: 24px 28px 26px;
  }
}
</style>

<style>
/* 片库面深色态（body 标记桥接） */
body.uhd-stream-dark .psp-sheet {
  background: #1c1c1c;
  color: #ffffff;
}
body.uhd-stream-dark .psp-sec-count,
body.uhd-stream-dark .psp-ver__meta,
body.uhd-stream-dark .psp-ver__streams,
body.uhd-stream-dark .psp-note,
body.uhd-stream-dark .psp-tip,
body.uhd-stream-dark .psp-loading,
body.uhd-stream-dark .psp-start-label {
  color: #9ca3af;
}
body.uhd-stream-dark .psp-ver {
  background: #222222;
  border-color: rgba(255, 255, 255, 0.14);
  color: #ffffff;
}
body.uhd-stream-dark .psp-ver--on {
  border-color: #ffffff;
}
body.uhd-stream-dark .psp-ver__radio {
  border-color: rgba(255, 255, 255, 0.4);
}
body.uhd-stream-dark .psp-ver--on .psp-ver__radio {
  border-color: #ffffff;
}
body.uhd-stream-dark .psp-ver--on .psp-ver__radio::after {
  background: #ffffff;
}
body.uhd-stream-dark .psp-ver__chip {
  background: rgba(255, 255, 255, 0.12);
  color: #ffffff;
}
body.uhd-stream-dark .psp-tool {
  background: #262626;
  border-color: rgba(255, 255, 255, 0.14);
  color: #ffffff;
}
body.uhd-stream-dark .psp-player {
  background: #222222;
  border-color: rgba(255, 255, 255, 0.14);
  color: #ffffff;
}
/* 深色开始播放：黑底毛玻璃＋同一彩虹边框（与深色 Hero 播放钮同口径） */
body.uhd-stream-dark .psp-start {
  background:
    linear-gradient(rgba(20, 20, 22, 0.92), rgba(20, 20, 22, 0.92)) padding-box,
    var(--uhd-rainbow) border-box;
  color: #ffffff;
}
body.uhd-stream-dark .psp-note a {
  color: #ffffff;
}
body.uhd-stream-dark .psp-spinner {
  border-color: rgba(255, 255, 255, 0.25);
  border-top-color: #ffffff;
}
@media (prefers-reduced-motion: reduce) {
  .psp-start:active { transform: none; }
}
</style>
