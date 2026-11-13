<template>
  <div
    class="wp-root"
    :class="[`wp-cue-${subtitleFontSize}`, { 'wp-idle-cursor': phase === 'playing' && !controlsVisible && !locked }]"
    @mousemove="pokeControls"
    @touchstart.passive="pokeControls"
  >
    <video
      ref="videoEl"
      class="wp-video"
      playsinline
      preload="auto"
      :poster="phase === 'idle' || phase === 'preparing' ? backdropSrc : ''"
      @click="onVideoTap"
      @loadedmetadata="onLoadedMetadata"
      @canplay="onCanPlay"
      @timeupdate="onTimeUpdate"
      @play="onPlayEvent"
      @pause="onPauseEvent"
      @ended="onEnded"
      @error="onVideoError"
    />

    <!-- 顶部栏：返回圆钮 + 标题 + 一起看 -->
    <header v-show="chromeVisible" class="wp-top">
      <div class="wp-top-left">
        <button type="button" class="wp-circle" aria-label="返回" @click="goBack">
          <ChevronLeft :size="21" />
        </button>
        <span class="wp-top-title">{{ displayTitle }}</span>
      </div>
      <button type="button" class="wp-together" @click="onTogether">
        <Users :size="15" />
        <span>一起看</span>
      </button>
    </header>

    <!-- 未开始：中央彩虹边开始胶囊 -->
    <div v-if="phase === 'idle'" class="wp-center">
      <div class="wp-start-pill">
        <button type="button" class="wp-start-main" @click="start(false)">
          <Play :size="19" class="wp-start-icon" />
          <span>{{ resumeSeconds > 5 ? '继续播放' : '开始播放' }}</span>
        </button>
        <template v-if="resumeSeconds > 5">
          <span class="wp-start-div" />
          <button type="button" class="wp-start-restart" @click="start(true)">重启</button>
        </template>
      </div>
      <p v-if="resumeSeconds > 5" class="wp-resume-hint">上次看到 {{ formatClock(resumeSeconds) }}</p>
    </div>

    <!-- 左下元信息（未开始/准备态） -->
    <div v-if="phase === 'idle' || phase === 'preparing'" class="wp-meta">
      <p v-if="episodeLine" class="wp-meta-ep">{{ episodeLine }}</p>
      <h1 class="wp-meta-title">{{ item?.Name || displayTitle }}</h1>
    </div>

    <!-- 右下规格（未开始/准备态）：版本规格行 + chip + 码率，照录屏右下角 -->
    <div
      v-if="(phase === 'idle' || phase === 'preparing') && specChips.length"
      class="wp-spec"
    >
      <p class="wp-spec-head">{{ specHeadline }}</p>
      <div class="wp-chips wp-chips-r">
        <span v-for="c in specChips" :key="c" class="wp-chip">{{ c }}</span>
      </div>
      <p v-if="bitrateText" class="wp-spec-bit">{{ bitrateText }}</p>
    </div>

    <!-- 准备态步骤卡 -->
    <div v-if="phase === 'preparing'" class="wp-prep" role="status">
      <div class="wp-prep-head">
        <LoaderCircle :size="17" class="wp-spin" />
        <strong>准备播放</strong>
      </div>
      <p class="wp-prep-step">{{ currentStepLabel }}</p>
      <p class="wp-prep-sub">{{ prepSub }}</p>
      <div class="wp-prep-dots">
        <span
          v-for="(s, i) in PREP_STEPS"
          :key="s.key"
          :class="{ on: i <= prepStepIndex, now: i === prepStepIndex }"
        />
      </div>
    </div>

    <!-- 播放中控制层 -->
    <template v-if="phase === 'playing'">
      <button
        type="button"
        class="wp-lock"
        :aria-label="locked ? '解锁' : '锁定'"
        @click.stop="toggleLock"
      >
        <LockOpen v-if="locked" :size="17" />
        <Lock v-else-if="controlsVisible" :size="17" />
      </button>

      <div v-show="controlsVisible && !locked" class="wp-bar">
        <div class="wp-bar-progress">
          <span class="wp-time">{{ formatClock(currentTime) }}</span>
          <input
            class="wp-progress"
            type="range"
            min="0"
            max="1000"
            step="1"
            :value="progressPermille"
            :style="{ background: progressBackground }"
            aria-label="播放进度"
            @input="onSeekInput"
            @change="onSeekCommit"
          />
          <span class="wp-time">{{ formatClock(duration) }}</span>
        </div>
        <div class="wp-bar-row">
          <div class="wp-bar-group">
            <button type="button" class="wp-icon" aria-label="上一集" :disabled="!prevEpisode" @click="goEpisode(prevEpisode)">
              <SkipBack :size="20" />
            </button>
            <button type="button" class="wp-icon" :aria-label="isPaused ? '播放' : '暂停'" @click="togglePlay">
              <Play v-if="isPaused" :size="22" />
              <Pause v-else :size="22" />
            </button>
            <button type="button" class="wp-icon" aria-label="下一集" :disabled="!nextEpisode" @click="goEpisode(nextEpisode)">
              <SkipForward :size="20" />
            </button>
          </div>
          <div class="wp-bar-group">
            <button
              type="button"
              class="wp-icon wp-danmaku-btn"
              :class="{ off: !danmaku.enabled, on: danmaku.enabled }"
              aria-label="弹幕设置"
              @click="danmakuOpen = true"
            >
              <span class="wp-danmaku-txt">弹</span>
              <span v-if="!danmaku.enabled" class="wp-danmaku-slash" />
            </button>
            <button
              type="button"
              class="wp-icon"
              :class="{ on: primarySub !== null }"
              aria-label="字幕"
              @click="subtitleOpen = true"
            >
              <Captions :size="20" />
            </button>
            <button type="button" class="wp-icon" aria-label="播放设置" @click="settingsOpen = true">
              <Settings2 :size="20" />
            </button>
            <button type="button" class="wp-icon" aria-label="全屏" @click="toggleFullscreen">
              <Maximize :size="19" />
            </button>
          </div>
        </div>
      </div>
    </template>

    <!-- 不支持 / 出错 -->
    <div v-if="phase === 'unsupported' || phase === 'error'" class="wp-notice">
      <TriangleAlert :size="30" class="wp-notice-icon" />
      <h2>{{ phase === 'unsupported' ? '这个格式网页里播不了' : '播放没能开始' }}</h2>
      <p>{{ noticeText }}</p>
      <div class="wp-notice-actions">
        <button type="button" class="wp-notice-btn wp-notice-primary" @click="goBack">返回详情</button>
        <button v-if="phase === 'error'" type="button" class="wp-notice-btn" @click="retry">再试一次</button>
      </div>
    </div>

    <!-- 轻提示 -->
    <Transition name="wp-toast">
      <p v-if="toast" class="wp-toast">{{ toast }}</p>
    </Transition>

    <PlayerDanmakuSheet
      :open="danmakuOpen"
      :settings="danmaku"
      @update:open="danmakuOpen = $event"
      @update:settings="onDanmakuSettings"
    />
    <PlayerSubtitleSheet
      :open="subtitleOpen"
      :tracks="subtitleTracks"
      :primary="primarySub"
      :secondary="secondarySub"
      :delay-ms="subtitleDelayMs"
      :font-size="subtitleFontSize"
      @update:open="subtitleOpen = $event"
      @update:primary="onPrimarySub"
      @update:secondary="onSecondarySub"
      @update:delay-ms="onSubtitleDelay"
      @update:font-size="subtitleFontSize = $event"
    />
    <PlayerSettingsSheet
      :open="settingsOpen"
      :sources="sourceOptions"
      :current-source-id="currentSource?.Id || ''"
      :current-source-label="currentSourceLabel"
      :episodes="episodeOptions"
      :current-episode-id="item?.Id || ''"
      :rate="rate"
      :info-lines="infoLines"
      @update:open="settingsOpen = $event"
      @update:rate="onRate"
      @select-source="onSelectSource"
      @select-episode="onSelectEpisode"
      @screenshot="onScreenshot"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Captions,
  ChevronLeft,
  LoaderCircle,
  Lock,
  LockOpen,
  Maximize,
  Pause,
  Play,
  Settings2,
  SkipBack,
  SkipForward,
  TriangleAlert,
  Users,
} from 'lucide-vue-next'
import {
  embyApi,
  backdropUrl,
  ticksToSeconds,
  type EmbyItem,
  type EmbyMediaSource,
} from '@/api/emby'
import {
  fetchPlaybackInfo,
  reportPlaying,
  reportProgress,
  reportStopped,
  reportStoppedBeacon,
  type PlaybackInfoResult,
} from '@/api/webPlayer'
import PlayerDanmakuSheet, { type DanmakuSettings } from '@/components/player/PlayerDanmakuSheet.vue'
import PlayerSubtitleSheet, {
  type SubtitleFontSize,
  type SubtitleTrackOption,
} from '@/components/player/PlayerSubtitleSheet.vue'
import PlayerSettingsSheet, {
  type EpisodeOption,
  type InfoLine,
  type SourceOption,
} from '@/components/player/PlayerSettingsSheet.vue'

type Phase = 'loading' | 'idle' | 'preparing' | 'playing' | 'unsupported' | 'error'

interface StreamLike {
  Index: number
  Type: string
  Codec?: string
  Language?: string
  DisplayTitle?: string
  IsExternal?: boolean
  IsTextSubtitleStream?: boolean
  IsDefault?: boolean
  DeliveryUrl?: string
  Width?: number
  Height?: number
  Channels?: number
  BitRate?: number
}

/** 浏览器基本播不动的容器：先尝试，失败/超时落到不支持提示，不黑屏装死 */
const RISKY_CONTAINERS = new Set(['mkv', 'avi', 'wmv', 'flv', 'rmvb', 'asf', 'ts', 'm2ts', 'vob'])
const PREP_STEPS = [
  { key: 'resolve', label: '正在解析版本与线路' },
  { key: 'open', label: '正在打开源' },
  { key: 'probe', label: '正在分析容器' },
  { key: 'ready', label: '就绪，正在起播' },
] as const
const DANMAKU_KEY = 'aetrix-player-danmaku'

const route = useRoute()
const router = useRouter()
const videoEl = ref<HTMLVideoElement | null>(null)

const phase = ref<Phase>('loading')
const item = ref<EmbyItem | null>(null)
const playbackInfo = ref<PlaybackInfoResult | null>(null)
const currentSource = ref<EmbyMediaSource | null>(null)
const episodes = ref<EmbyItem[]>([])
const prepStepIndex = ref(0)
const prepSlow = ref(false)
const noticeText = ref('')
const toast = ref('')

const isPaused = ref(true)
const currentTime = ref(0)
const duration = ref(0)
const rate = ref(1)
const controlsVisible = ref(true)
const locked = ref(false)
const seeking = ref(false)
const seekPreview = ref(0)

const danmakuOpen = ref(false)
const subtitleOpen = ref(false)
const settingsOpen = ref(false)
const primarySub = ref<number | null>(null)
const secondarySub = ref<number | null>(null)
const subtitleDelayMs = ref(0)
const subtitleFontSize = ref<SubtitleFontSize>('default')
const danmaku = ref<DanmakuSettings>(loadDanmaku())

let controlsTimer: number | undefined
let progressTimer: number | undefined
let toastTimer: number | undefined
let probeTimer: number | undefined
let probeTimer2: number | undefined
let lastOpenAt = 0
let startedFromResume = false
let reportedPlayingFor = ''
let lastVideoErrorAt = 0

// ---------- 基础派生 ----------

const itemId = computed(() => String(route.params.id || ''))
const backdropSrc = computed(() => (item.value ? backdropUrl(item.value, 1280) : ''))
const isEpisode = computed(() => item.value?.Type === 'Episode')

const displayTitle = computed(() => {
  const it = item.value
  if (!it) return ''
  if (it.Type === 'Episode' && it.SeriesName) {
    const s = it.ParentIndexNumber
    const e = it.IndexNumber
    const se = s != null && e != null ? ` S${s}E${e}` : ''
    return `${it.SeriesName}${se} · ${it.Name}`
  }
  return it.Name
})

const episodeLine = computed(() => {
  const it = item.value
  if (!it || it.Type !== 'Episode') return ''
  const s = it.ParentIndexNumber
  const e = it.IndexNumber
  if (s == null || e == null) return it.Name
  return `第${s}季第${e}集 · ${it.Name}`
})

const resumeSeconds = computed(() => {
  const at = Number(route.query.at)
  if (Number.isFinite(at) && at > 0) return at
  return ticksToSeconds(item.value?.UserData?.PlaybackPositionTicks)
})

function streamsOf(source: EmbyMediaSource | null): StreamLike[] {
  return (source?.MediaStreams || []) as StreamLike[]
}
function videoStreamOf(source: EmbyMediaSource | null): StreamLike | undefined {
  return streamsOf(source).find((s) => s.Type === 'Video')
}
function resolutionLabel(vs: StreamLike | undefined): string {
  if (!vs) return ''
  const h = vs.Height || 0
  const w = vs.Width || 0
  if (h >= 2000 || w >= 3800) return '4K'
  if (h >= 1000 || w >= 1900) return '1080p'
  if (h >= 700 || w >= 1200) return '720p'
  if (h > 0) return `${h}p`
  return ''
}
function specChipsOf(source: EmbyMediaSource | null): string[] {
  if (!source) return []
  const chips: string[] = []
  const vs = videoStreamOf(source)
  const res = resolutionLabel(vs)
  if (res) chips.push(res)
  const title = vs?.DisplayTitle || ''
  for (const token of ['Dolby Vision', '杜比视界', 'HDR10+', 'HDR10', 'HDR', 'HLG']) {
    if (title.includes(token)) {
      chips.push(token === 'Dolby Vision' ? '杜比视界' : token)
      break
    }
  }
  if (vs?.Codec) chips.push(vs.Codec.toUpperCase())
  const audio = streamsOf(source).find((s) => s.Type === 'Audio')
  if (audio?.Channels && audio.Channels >= 6) chips.push('5.1声道')
  if (source.Container) chips.push(source.Container.toUpperCase())
  return chips
}

const specChips = computed(() =>
  specChipsOf(currentSource.value || item.value?.MediaSources?.[0] || null),
)

/** 右下规格主行：如「1080p SDR HEVC」，照录屏右下角的粗体规格行 */
const specHeadline = computed(() => {
  const src = currentSource.value || item.value?.MediaSources?.[0] || null
  const vs = videoStreamOf(src)
  if (!vs) return ''
  const title = vs.DisplayTitle || ''
  const range =
    ['Dolby Vision', 'HDR10+', 'HDR10', 'HDR', 'HLG'].find((t) => title.includes(t)) || 'SDR'
  return [resolutionLabel(vs), range, vs.Codec?.toUpperCase()].filter(Boolean).join(' ')
})

/** 右下码率行：视频+音频码率合计，没有数据就不显示，不编数 */
const bitrateText = computed(() => {
  const src = currentSource.value || item.value?.MediaSources?.[0] || null
  if (!src) return ''
  const total = streamsOf(src)
    .filter((s) => s.Type === 'Video' || s.Type === 'Audio')
    .reduce((sum, s) => sum + (s.BitRate || 0), 0)
  if (!total) return ''
  return `${(total / 1_000_000).toFixed(1)} Mbps`
})

const currentStepLabel = computed(() => PREP_STEPS[prepStepIndex.value]?.label || '')
const prepSub = computed(() => {
  const src = currentSource.value
  if (prepSlow.value) return '源响应有点慢，仍在尝试打开（网盘源第一次打开经常要等）'
  if (prepStepIndex.value === 0) return '正在向服务器确认可播放的版本'
  if (prepStepIndex.value === 1 && src) return src.Name || '正在连接播放地址'
  if (prepStepIndex.value === 2 && src?.Container) return `容器 ${src.Container.toUpperCase()}，正在读取媒体信息`
  return '马上就好'
})

const chromeVisible = computed(
  () => phase.value !== 'playing' || (controlsVisible.value && !locked.value),
)

const progressPermille = computed(() => {
  const t = seeking.value ? seekPreview.value : currentTime.value
  if (!duration.value) return 0
  return Math.min(1000, Math.round((t / duration.value) * 1000))
})
const progressBackground = computed(() => {
  const p = progressPermille.value / 10
  return `linear-gradient(90deg, #fff ${p}%, rgba(255,255,255,.28) ${p}%)`
})

const subtitleTracks = computed<SubtitleTrackOption[]>(() => {
  return streamsOf(currentSource.value)
    .filter((s) => s.Type === 'Subtitle' && s.IsTextSubtitleStream && s.DeliveryUrl)
    .map((s) => ({
      index: s.Index,
      label: s.DisplayTitle || s.Language || `字幕 ${s.Index}`,
      language: s.Language,
    }))
})

const sourceOptions = computed<SourceOption[]>(() => {
  return (playbackInfo.value?.MediaSources || []).map((s) => {
    const vs = videoStreamOf(s)
    const parts = [resolutionLabel(vs), vs?.Codec?.toUpperCase()].filter(Boolean)
    return {
      id: s.Id,
      label: s.Name || parts.join(' ') || '默认版本',
      meta: s.Size ? formatSize(s.Size) : undefined,
    }
  })
})
const currentSourceLabel = computed(() => {
  const hit = sourceOptions.value.find((o) => o.id === currentSource.value?.Id)
  return hit?.label || currentSource.value?.Name || ''
})

const episodeOptions = computed<EpisodeOption[]>(() =>
  episodes.value.map((ep) => ({
    id: ep.Id,
    label:
      ep.IndexNumber != null ? `第${ep.IndexNumber}集 · ${ep.Name}` : ep.Name,
  })),
)
const currentEpisodePos = computed(() =>
  episodes.value.findIndex((ep) => ep.Id === item.value?.Id),
)
const prevEpisode = computed(() =>
  currentEpisodePos.value > 0 ? episodes.value[currentEpisodePos.value - 1] : undefined,
)
const nextEpisode = computed(() =>
  currentEpisodePos.value >= 0 && currentEpisodePos.value < episodes.value.length - 1
    ? episodes.value[currentEpisodePos.value + 1]
    : undefined,
)

const infoLines = computed<InfoLine[]>(() => {
  const src = currentSource.value
  const vs = videoStreamOf(src)
  const audio = streamsOf(src).find((s) => s.Type === 'Audio')
  const lines: InfoLine[] = [
    { label: '条目', value: displayTitle.value || '—' },
    { label: '版本', value: currentSourceLabel.value || '—' },
    { label: '容器', value: (src?.Container || '—').toUpperCase() },
    {
      label: '分辨率',
      value: vs?.Width && vs?.Height ? `${vs.Width}×${vs.Height}` : '—',
    },
    { label: '视频编码', value: (vs?.Codec || '—').toUpperCase() },
    { label: '音频编码', value: (audio?.Codec || '—').toUpperCase() },
    { label: '大小', value: src?.Size ? formatSize(src.Size) : '—' },
    {
      label: '进度',
      value: `${formatClock(currentTime.value)} / ${formatClock(duration.value)}`,
    },
    { label: '倍速', value: `${rate.value}x` },
  ]
  return lines
})

// ---------- 工具 ----------

function formatClock(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) sec = 0
  const s = Math.floor(sec % 60)
  const m = Math.floor((sec / 60) % 60)
  const h = Math.floor(sec / 3600)
  const mm = h > 0 ? String(m).padStart(2, '0') : String(m)
  const ss = String(s).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}
function formatSize(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`
  return `${bytes} B`
}
function showToast(text: string): void {
  toast.value = text
  window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => (toast.value = ''), 2600)
}
function loadDanmaku(): DanmakuSettings {
  const fallback: DanmakuSettings = {
    enabled: true,
    area: 'full',
    opacity: 100,
    fontSize: 'medium',
    density: 'standard',
    speed: 'standard',
    offsetSeconds: 0,
  }
  try {
    const raw = localStorage.getItem(DANMAKU_KEY)
    if (raw) return { ...fallback, ...(JSON.parse(raw) as Partial<DanmakuSettings>) }
  } catch {
    /* 坏数据用默认 */
  }
  return fallback
}
function errorText(e: unknown, fallback: string): string {
  const err = e as {
    response?: { status?: number; data?: { detail?: string; message?: string; msg?: string } }
    message?: string
  }
  const data = err?.response?.data
  const text = data?.detail || data?.message || data?.msg
  if (typeof text === 'string' && text) return text
  if (err?.response?.status === 403) return '还没过播放门禁：先绑定自己的网盘，或等公共池副本就绪后再来。'
  if (err?.response?.status === 409) return '这部片正在准备中（转存/搬运），过一会儿再点播放。'
  return err?.message || fallback
}

// ---------- 数据加载 ----------

async function loadItem(id: string): Promise<void> {
  phase.value = 'loading'
  playbackInfo.value = null
  currentSource.value = null
  episodes.value = []
  try {
    const it = await embyApi.getItem(id)
    item.value = it
    document.title = `${it.Name} · 播放`
    if (it.Type === 'Episode' && it.SeriesId) {
      embyApi
        .getEpisodes(it.SeriesId, it.SeasonId || undefined)
        .then((eps) => (episodes.value = eps))
        .catch(() => {})
    }
    phase.value = 'idle'
  } catch (e) {
    noticeText.value = errorText(e, '条目加载失败，可能不存在或不在你的可见范围内。')
    phase.value = 'error'
  }
}

function pickSource(info: PlaybackInfoResult, wantedId?: string): EmbyMediaSource | null {
  const list = info.MediaSources || []
  if (wantedId) {
    const hit = list.find((s) => s.Id === wantedId)
    if (hit) return hit
  }
  return list.find((s) => s.DirectStreamUrl) || list[0] || null
}

// ---------- 起播 ----------

async function start(restart: boolean): Promise<void> {
  const v = videoEl.value
  if (!v || !item.value) return
  startedFromResume = !restart && resumeSeconds.value > 5
  phase.value = 'preparing'
  prepStepIndex.value = 0
  try {
    if (!playbackInfo.value) {
      playbackInfo.value = await fetchPlaybackInfo(item.value.Id)
    }
    const info = playbackInfo.value
    const wanted = String(route.query.ms || '') || undefined
    const source = pickSource(info, currentSource.value?.Id || wanted)
    if (!source?.DirectStreamUrl) {
      noticeText.value = '服务器没有下发可播放的地址，可能正在准备中，过一会儿再试。'
      phase.value = 'error'
      return
    }
    currentSource.value = source
    prepStepIndex.value = 1
    await openSource(source, startedFromResume ? resumeSeconds.value : 0)
  } catch (e) {
    noticeText.value = errorText(e, '取播放地址失败，稍后再试。')
    phase.value = 'error'
  }
}

async function openSource(source: EmbyMediaSource, atSeconds: number): Promise<void> {
  const v = videoEl.value
  if (!v) return
  const url = source.DirectStreamUrl || ''
  const isHls = /\.m3u8($|\?)/i.test(url)
  if (isHls && !v.canPlayType('application/vnd.apple.mpegurl')) {
    noticeText.value =
      '这条线路是 HLS 流，当前浏览器不支持原生 HLS 播放。换 Safari 打开，或用第三方播放器播这一集。'
    phase.value = 'unsupported'
    return
  }
  prepStepIndex.value = 2
  prepSlow.value = false
  lastOpenAt = Date.now()
  // 慢源宽限：网盘/挂载源首开经常十几秒才出元数据。
  // 12 秒没到先提示「有点慢」继续等，30 秒仍没到才判失败，不许慢一点就报错。
  window.clearTimeout(probeTimer)
  window.clearTimeout(probeTimer2)
  probeTimer = window.setTimeout(() => {
    if (phase.value === 'preparing' && v.readyState < 1) prepSlow.value = true
  }, 12000)
  probeTimer2 = window.setTimeout(() => {
    if (phase.value === 'preparing' && v.readyState < 1) {
      failByContainer(source, '视频数据迟迟没到')
    }
  }, 30000)
  v.src = url
  v.playbackRate = rate.value
  if (atSeconds > 0) {
    const onMeta = () => {
      try {
        v.currentTime = atSeconds
      } catch {
        /* 个别流不支持定位，忽略 */
      }
      v.removeEventListener('loadedmetadata', onMeta)
    }
    v.addEventListener('loadedmetadata', onMeta)
  }
  rebuildSubtitleTracks()
  try {
    await v.play()
  } catch {
    /* 自动播放被拦时留在准备态，等用户再点一次开始 */
  }
}

function failByContainer(source: EmbyMediaSource, why: string): void {
  const container = (source.Container || '').toLowerCase()
  if (RISKY_CONTAINERS.has(container)) {
    noticeText.value = `这是 ${container.toUpperCase()} 容器，浏览器内核解不了（${why}）。用第三方播放器（Infuse / SenPlayer 等）打开同一集就能播。`
    phase.value = 'unsupported'
  } else {
    noticeText.value = `视频加载失败（${why}），可能是源暂时不可用，稍后再试。`
    phase.value = 'error'
  }
}

async function retry(): Promise<void> {
  playbackInfo.value = null
  await start(false)
}

// ---------- video 事件 ----------

function onLoadedMetadata(): void {
  const v = videoEl.value
  if (!v) return
  window.clearTimeout(probeTimer)
  window.clearTimeout(probeTimer2)
  prepSlow.value = false
  duration.value = v.duration || ticksToSeconds(item.value?.RunTimeTicks)
  if (phase.value === 'preparing') prepStepIndex.value = 3
}
function onCanPlay(): void {
  window.clearTimeout(probeTimer)
  window.clearTimeout(probeTimer2)
  prepSlow.value = false
  if (phase.value === 'preparing') {
    phase.value = 'playing'
    pokeControls()
  }
}
function onTimeUpdate(): void {
  const v = videoEl.value
  if (!v || seeking.value) return
  currentTime.value = v.currentTime || 0
}
function onPlayEvent(): void {
  isPaused.value = false
  reportStartIfNeeded()
}
function onPauseEvent(): void {
  isPaused.value = true
  sendProgress(true)
}
function onEnded(): void {
  sendStopped()
  if (nextEpisode.value) {
    showToast('已播完，自动接下一集')
    goEpisode(nextEpisode.value)
  }
}
function onVideoError(): void {
  const v = videoEl.value
  if (!v || phase.value === 'idle' || phase.value === 'loading') return
  // 刚换 src 的 800ms 内、或已经有可播数据时，error 多半是旧资源/轨道的
  // 迟报，不能拿它把正在播的画面打成错误页
  if (Date.now() - lastOpenAt < 800) return
  if (v.readyState >= 2) return
  const now = Date.now()
  if (now - lastVideoErrorAt < 600 && phase.value === 'preparing') return
  lastVideoErrorAt = now
  window.clearTimeout(probeTimer)
  window.clearTimeout(probeTimer2)
  if (currentSource.value) failByContainer(currentSource.value, '浏览器拒绝了这个视频流')
}

function onVideoTap(): void {
  if (locked.value) return
  if (phase.value === 'playing') {
    controlsVisible.value = !controlsVisible.value
    if (controlsVisible.value) pokeControls()
  }
}

// ---------- 上报 ----------

function reportPayload() {
  return {
    itemId: item.value?.Id || itemId.value,
    positionSeconds: videoEl.value?.currentTime || currentTime.value,
    playMethod: 'DirectPlay',
    mediaSourceId: currentSource.value?.Id,
    playSessionId: playbackInfo.value?.PlaySessionId,
  }
}
function reportStartIfNeeded(): void {
  const key = `${item.value?.Id}:${currentSource.value?.Id}`
  if (reportedPlayingFor === key) return
  reportedPlayingFor = key
  reportPlaying(reportPayload()).catch(() => {})
  window.clearInterval(progressTimer)
  progressTimer = window.setInterval(() => sendProgress(false), 15000)
}
function sendProgress(paused: boolean): void {
  if (!item.value || phase.value !== 'playing') return
  reportProgress(reportPayload(), paused).catch(() => {})
}
function sendStopped(): void {
  if (!item.value) return
  const payload = reportPayload()
  reportStopped(payload).catch(() => reportStoppedBeacon(payload))
}

// ---------- 控制 ----------

function pokeControls(): void {
  if (phase.value !== 'playing' || locked.value) return
  controlsVisible.value = true
  window.clearTimeout(controlsTimer)
  controlsTimer = window.setTimeout(() => {
    if (!isPaused.value) controlsVisible.value = false
  }, 3500)
}
function togglePlay(): void {
  const v = videoEl.value
  if (!v) return
  if (v.paused) void v.play()
  else v.pause()
  pokeControls()
}
function onSeekInput(e: Event): void {
  seeking.value = true
  const val = Number((e.target as HTMLInputElement).value)
  seekPreview.value = (val / 1000) * (duration.value || 0)
}
function onSeekCommit(e: Event): void {
  const v = videoEl.value
  if (!v) return
  const val = Number((e.target as HTMLInputElement).value)
  const target = (val / 1000) * (duration.value || 0)
  try {
    v.currentTime = target
  } catch {
    /* 不支持定位的流忽略 */
  }
  seeking.value = false
  currentTime.value = target
  sendProgress(isPaused.value)
  pokeControls()
}
function toggleLock(): void {
  locked.value = !locked.value
  controlsVisible.value = !locked.value
  if (!locked.value) pokeControls()
}
function toggleFullscreen(): void {
  const root = videoEl.value?.closest('.wp-root') as HTMLElement | null
  if (document.fullscreenElement) void document.exitFullscreen()
  else if (root) void root.requestFullscreen?.()
}
function onDanmakuSettings(s: DanmakuSettings): void {
  danmaku.value = s
  persistDanmaku()
}
function persistDanmaku(): void {
  try {
    localStorage.setItem(DANMAKU_KEY, JSON.stringify(danmaku.value))
  } catch {
    /* 存不下就算了 */
  }
}
function onTogether(): void {
  showToast('一起看还没上线，这个按钮先占个位')
}
function goBack(): void {
  sendStopped()
  if (window.history.length > 1) router.back()
  else void router.push({ name: 'media-detail', params: { id: itemId.value } })
}

// ---------- 字幕 ----------

const subBlobUrls = new Map<string, string>()

async function processedSubUrl(track: StreamLike): Promise<string> {
  const url = track.DeliveryUrl || ''
  if (!url || subtitleDelayMs.value === 0) return url
  const cacheKey = `${track.Index}:${subtitleDelayMs.value}`
  const hit = subBlobUrls.get(cacheKey)
  if (hit) return hit
  const res = await fetch(url)
  if (!res.ok) throw new Error('subtitle fetch failed')
  const text = await res.text()
  const shifted = shiftVtt(text, subtitleDelayMs.value / 1000)
  const blobUrl = URL.createObjectURL(new Blob([shifted], { type: 'text/vtt' }))
  subBlobUrls.set(cacheKey, blobUrl)
  return blobUrl
}

function shiftVtt(text: string, deltaSeconds: number): string {
  const toSeconds = (h: string, m: string, s: string) =>
    Number(h) * 3600 + Number(m) * 60 + Number(s)
  const fmt = (total: number) => {
    const t = Math.max(0, total)
    const hh = String(Math.floor(t / 3600)).padStart(2, '0')
    const mm = String(Math.floor((t % 3600) / 60)).padStart(2, '0')
    const ss = (t % 60).toFixed(3).padStart(6, '0')
    return `${hh}:${mm}:${ss}`
  }
  return text.replace(
    /(\d{2}):(\d{2}):(\d{2}\.\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}\.\d{3})/g,
    (_all, h1, m1, s1, h2, m2, s2) =>
      `${fmt(toSeconds(h1, m1, s1) + deltaSeconds)} --> ${fmt(toSeconds(h2, m2, s2) + deltaSeconds)}`,
  )
}

async function rebuildSubtitleTracks(): Promise<void> {
  const v = videoEl.value
  if (!v) return
  v.querySelectorAll('track').forEach((t) => t.remove())
  const wanted: Array<{ track: StreamLike; kind: 'primary' | 'secondary' }> = []
  const all = streamsOf(currentSource.value).filter(
    (s) => s.Type === 'Subtitle' && s.IsTextSubtitleStream && s.DeliveryUrl,
  )
  for (const t of all) {
    if (t.Index === primarySub.value) wanted.push({ track: t, kind: 'primary' })
    else if (t.Index === secondarySub.value) wanted.push({ track: t, kind: 'secondary' })
  }
  for (const { track } of wanted) {
    let src = track.DeliveryUrl || ''
    if (subtitleDelayMs.value !== 0) {
      try {
        src = await processedSubUrl(track)
      } catch {
        showToast('字幕延迟没能应用（字幕文件取不到），先按原时间显示')
      }
    }
    const el = document.createElement('track')
    el.kind = 'subtitles'
    el.label = track.DisplayTitle || track.Language || `字幕 ${track.Index}`
    if (track.Language) el.srclang = track.Language.slice(0, 2)
    el.src = src
    el.default = true
    v.appendChild(el)
  }
  applySubtitleModes()
}

function applySubtitleModes(): void {
  const v = videoEl.value
  if (!v) return
  const modes = Array.from(v.textTracks)
  modes.forEach((t) => {
    t.mode = 'showing'
  })
}

function onPrimarySub(index: number | null): void {
  primarySub.value = index
  void rebuildSubtitleTracks()
}
function onSecondarySub(index: number | null): void {
  secondarySub.value = index
  void rebuildSubtitleTracks()
}
function onSubtitleDelay(ms: number): void {
  subtitleDelayMs.value = ms
  void rebuildSubtitleTracks()
}

// ---------- 设置面板动作 ----------

function onRate(r: number): void {
  rate.value = r
  const v = videoEl.value
  if (v) v.playbackRate = r
}

async function onSelectSource(sourceId: string): Promise<void> {
  const info = playbackInfo.value
  if (!info) return
  const source = info.MediaSources.find((s) => s.Id === sourceId)
  if (!source || source.Id === currentSource.value?.Id) {
    settingsOpen.value = false
    return
  }
  settingsOpen.value = false
  const v = videoEl.value
  const at = v?.currentTime || currentTime.value
  const wasPlaying = !!v && !v.paused
  currentSource.value = source
  reportedPlayingFor = ''
  // 切版本后字幕轨索引随新源走：保留同 Index 选择，新源没有就清掉
  const valid = new Set(
    streamsOf(source)
      .filter((s) => s.Type === 'Subtitle')
      .map((s) => s.Index),
  )
  if (primarySub.value !== null && !valid.has(primarySub.value)) primarySub.value = null
  if (secondarySub.value !== null && !valid.has(secondarySub.value)) secondarySub.value = null
  phase.value = 'preparing'
  prepStepIndex.value = 1
  await openSource(source, at)
  if (wasPlaying) videoEl.value?.play().catch(() => {})
}

function onSelectEpisode(id: string): void {
  settingsOpen.value = false
  goEpisode(episodes.value.find((ep) => ep.Id === id))
}
function goEpisode(ep: EmbyItem | undefined): void {
  if (!ep) return
  sendStopped()
  void router.push({ name: 'web-player', params: { id: ep.Id } })
}

function onScreenshot(): void {
  const v = videoEl.value
  if (!v || !v.videoWidth) {
    showToast('现在还没有画面可截')
    return
  }
  try {
    const canvas = document.createElement('canvas')
    canvas.width = v.videoWidth
    canvas.height = v.videoHeight
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.drawImage(v, 0, 0)
    canvas.toBlob((blob) => {
      if (!blob) {
        showToast('截图失败')
        return
      }
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `${item.value?.Name || 'aetrix'}-${Math.floor(v.currentTime)}s.png`
      a.click()
      window.setTimeout(() => URL.revokeObjectURL(a.href), 4000)
      showToast('截图已保存')
    }, 'image/png')
  } catch {
    showToast('这条线路不允许截图（跨域限制）')
  }
  settingsOpen.value = false
}

// ---------- 生命周期 ----------

function onKeydown(e: KeyboardEvent): void {
  if (phase.value !== 'playing') return
  const v = videoEl.value
  if (!v) return
  if (e.key === ' ') {
    e.preventDefault()
    togglePlay()
  } else if (e.key === 'ArrowRight') {
    v.currentTime = Math.min((v.duration || 0), v.currentTime + 10)
  } else if (e.key === 'ArrowLeft') {
    v.currentTime = Math.max(0, v.currentTime - 10)
  }
}
function onVisibility(): void {
  if (document.hidden) sendProgress(isPaused.value)
}

watch(
  () => route.params.id,
  (id) => {
    if (id) {
      reportedPlayingFor = ''
      window.clearInterval(progressTimer)
      void loadItem(String(id))
    }
  },
)

onMounted(() => {
  document.body.style.overflow = 'hidden'
  document.addEventListener('keydown', onKeydown)
  document.addEventListener('visibilitychange', onVisibility)
  // 默认字幕：跟服务端 DefaultSubtitleStreamIndex
  void loadItem(itemId.value).then(() => {
    const dft = item.value?.MediaSources?.[0]?.DefaultSubtitleStreamIndex
    if (dft != null && primarySub.value === null) primarySub.value = dft
  })
})
onUnmounted(() => {
  document.body.style.overflow = ''
  document.removeEventListener('keydown', onKeydown)
  document.removeEventListener('visibilitychange', onVisibility)
  window.clearTimeout(controlsTimer)
  window.clearTimeout(probeTimer)
  window.clearTimeout(probeTimer2)
  window.clearInterval(progressTimer)
  sendStopped()
  subBlobUrls.forEach((u) => URL.revokeObjectURL(u))
})
</script>

<style scoped>
.wp-root {
  position: fixed;
  inset: 0;
  z-index: 300;
  background: #000;
  color: #fff;
  overflow: hidden;
  font-family: inherit;
}
.wp-idle-cursor {
  cursor: none;
}
.wp-video {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: #000;
}
.wp-video::cue {
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
}
.wp-cue-small .wp-video::cue {
  font-size: 0.72em;
}
.wp-cue-medium .wp-video::cue {
  font-size: 1em;
}
.wp-cue-large .wp-video::cue {
  font-size: 1.35em;
}

.wp-top {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: calc(10px + env(safe-area-inset-top)) 14px 10px;
  background: linear-gradient(180deg, rgba(0, 0, 0, 0.55), transparent);
}
.wp-top-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.wp-circle {
  flex: none;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(18, 18, 20, 0.55);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.wp-top-title {
  font-size: 15px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.wp-together {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 40px;
  padding: 0 16px;
  border-radius: 20px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(18, 18, 20, 0.55);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  color: #fff;
  font-size: 14px;
  cursor: pointer;
}

.wp-center {
  position: absolute;
  inset: 0;
  z-index: 15;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  pointer-events: none;
}
.wp-start-pill {
  pointer-events: auto;
  display: inline-flex;
  align-items: center;
  height: 58px;
  border-radius: 29px;
  padding: 2px;
  background: var(--uhd-rainbow, linear-gradient(90deg, #2f8fe0, #6f7ad8, #c46c90, #e8766f, #f2a45a, #ecd04f, #95c77d, #39b3ec));
}
.wp-start-main,
.wp-start-restart {
  height: 100%;
  border: none;
  background: rgba(10, 10, 12, 0.78);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.wp-start-main {
  border-radius: 27px;
  padding: 0 26px;
}
.wp-start-pill:has(.wp-start-restart) .wp-start-main {
  border-radius: 27px 4px 4px 27px;
  padding: 0 18px 0 24px;
}
.wp-start-restart {
  border-radius: 4px 27px 27px 4px;
  padding: 0 22px 0 16px;
  font-weight: 500;
  color: rgba(255, 255, 255, 0.85);
}
.wp-start-div {
  width: 1px;
  align-self: stretch;
  margin: 10px 0;
  background: rgba(255, 255, 255, 0.25);
}
.wp-start-icon {
  fill: #fff;
}
.wp-resume-hint {
  margin: 0;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.75);
  text-shadow: 0 1px 8px rgba(0, 0, 0, 0.8);
}

.wp-meta {
  position: absolute;
  left: 16px;
  bottom: calc(22px + env(safe-area-inset-bottom));
  z-index: 12;
  max-width: 58%;
  pointer-events: none;
  text-shadow: 0 1px 10px rgba(0, 0, 0, 0.85);
}
.wp-meta-ep {
  margin: 0 0 4px;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.65);
}
.wp-meta-title {
  margin: 0;
  /* D1-09：标题用无衬线特粗，不用衬线 */
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC',
    'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
  font-size: 24px;
  line-height: 1.25;
  font-weight: 800;
}
.wp-spec {
  position: absolute;
  right: 16px;
  bottom: calc(22px + env(safe-area-inset-bottom));
  z-index: 12;
  max-width: 40%;
  text-align: right;
  pointer-events: none;
  text-shadow: 0 1px 10px rgba(0, 0, 0, 0.85);
}
.wp-spec-head {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
}
.wp-chips-r {
  justify-content: flex-end;
  margin-top: 6px;
}
.wp-spec-bit {
  margin: 6px 0 0;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.55);
}
.wp-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}
.wp-chip {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-radius: 6px;
  padding: 2px 7px;
  background: rgba(0, 0, 0, 0.35);
}

.wp-prep {
  position: absolute;
  left: 16px;
  bottom: calc(96px + env(safe-area-inset-bottom));
  z-index: 14;
  width: min(400px, calc(100% - 32px));
  background: rgba(20, 20, 23, 0.82);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 18px;
  padding: 14px 16px;
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
}
.wp-prep-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
}
.wp-spin {
  animation: wp-rot 1s linear infinite;
}
@keyframes wp-rot {
  to {
    transform: rotate(360deg);
  }
}
.wp-prep-step {
  margin: 8px 0 0;
  font-size: 13.5px;
  color: rgba(255, 255, 255, 0.88);
}
.wp-prep-sub {
  margin: 3px 0 0;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.45);
}
.wp-prep-dots {
  display: flex;
  gap: 5px;
  margin-top: 12px;
}
.wp-prep-dots span {
  height: 3px;
  flex: 1;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.15);
}
.wp-prep-dots span.on {
  background: rgba(255, 255, 255, 0.85);
}
.wp-prep-dots span.now {
  background: var(--uhd-rainbow, linear-gradient(90deg, #2f8fe0, #e8766f, #ecd04f, #39b3ec));
}

.wp-lock {
  position: absolute;
  left: 10px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 22;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(18, 18, 20, 0.55);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.wp-bar {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  bottom: calc(14px + env(safe-area-inset-bottom));
  z-index: 20;
  width: min(680px, calc(100% - 24px));
  background: rgba(22, 22, 25, 0.78);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 20px;
  padding: 10px 16px 12px;
  backdrop-filter: blur(22px);
  -webkit-backdrop-filter: blur(22px);
}
.wp-bar-progress {
  display: flex;
  align-items: center;
  gap: 10px;
}
.wp-time {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.8);
  min-width: 38px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}
.wp-progress {
  flex: 1;
  -webkit-appearance: none;
  appearance: none;
  height: 4px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.28);
  outline: none;
  cursor: pointer;
}
.wp-progress::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 13px;
  height: 13px;
  border-radius: 50%;
  background: #fff;
  border: none;
  box-shadow: 0 1px 5px rgba(0, 0, 0, 0.5);
}
.wp-progress::-moz-range-thumb {
  width: 13px;
  height: 13px;
  border-radius: 50%;
  background: #fff;
  border: none;
}
.wp-bar-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
}
.wp-bar-group {
  display: flex;
  align-items: center;
  gap: 4px;
}
.wp-icon {
  width: 38px;
  height: 38px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  position: relative;
}
.wp-icon:hover {
  background: rgba(255, 255, 255, 0.1);
}
.wp-icon:disabled {
  opacity: 0.3;
  cursor: default;
}
.wp-icon:disabled:hover {
  background: transparent;
}
/* 激活态图标带圆底（照录屏：激活者白圆底黑图标） */
.wp-icon.on {
  background: rgba(255, 255, 255, 0.92);
  color: #111;
}
.wp-danmaku-txt {
  font-size: 14px;
  font-weight: 700;
  line-height: 1;
}
.wp-danmaku-btn.off {
  color: rgba(255, 255, 255, 0.45);
}
.wp-danmaku-slash {
  position: absolute;
  width: 22px;
  height: 2px;
  background: currentColor;
  transform: rotate(-45deg);
  border-radius: 1px;
}

.wp-notice {
  position: absolute;
  inset: 0;
  z-index: 25;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 24px;
  text-align: center;
  background: rgba(0, 0, 0, 0.72);
}
.wp-notice-icon {
  color: #f2c14e;
}
.wp-notice h2 {
  margin: 4px 0 0;
  font-size: 20px;
}
.wp-notice p {
  margin: 0;
  max-width: 440px;
  font-size: 14px;
  line-height: 1.7;
  color: rgba(255, 255, 255, 0.7);
}
.wp-notice-actions {
  display: flex;
  gap: 10px;
  margin-top: 10px;
}
.wp-notice-btn {
  border: 1px solid rgba(255, 255, 255, 0.25);
  background: transparent;
  color: #fff;
  font-size: 14px;
  padding: 10px 22px;
  border-radius: 999px;
  cursor: pointer;
}
.wp-notice-primary {
  background: #fff;
  color: #111;
  border-color: #fff;
  font-weight: 600;
}

.wp-toast {
  position: absolute;
  top: calc(64px + env(safe-area-inset-top));
  left: 50%;
  transform: translateX(-50%);
  z-index: 30;
  margin: 0;
  background: rgba(20, 20, 23, 0.88);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 999px;
  padding: 8px 16px;
  font-size: 13px;
  white-space: nowrap;
  max-width: calc(100% - 32px);
  overflow: hidden;
  text-overflow: ellipsis;
}
.wp-toast-enter-active,
.wp-toast-leave-active {
  transition: opacity 0.25s ease;
}
.wp-toast-enter-from,
.wp-toast-leave-to {
  opacity: 0;
}

@media (min-width: 768px) {
  .wp-meta {
    left: 28px;
    bottom: 30px;
  }
  .wp-spec {
    right: 28px;
    bottom: 30px;
  }
  .wp-meta-title {
    font-size: 28px;
  }
  .wp-prep {
    left: 28px;
    bottom: 110px;
  }
}
</style>
