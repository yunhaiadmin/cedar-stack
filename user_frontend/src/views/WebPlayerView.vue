<template>
  <div
    class="wp-root"
    :class="[`wp-cue-${subtitleFontSize}`, { 'wp-idle-cursor': phase === 'playing' && !controlsVisible && !locked }]"
    @mousemove="pokeControls"
    @touchstart.passive="pokeControls"
  >
    <video
      ref="videoEl"
      v-show="!softActive"
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

    <!-- 软解画布：浏览器硬解失败且开关开时，AVPlayer 在此渲染（canvas），
         原生 video 隐藏。点一下切换控制层显隐，口径同原生 video 点按。 -->
    <div v-show="softActive" ref="softEl" class="wp-soft" @click="onSoftTap" />

    <!-- 站内弹幕层：站点开关开 + 用户弹幕开关开 + 原生播放路径时才渲染。
         弹幕按时间窗分片拉取、到点从右向左飘过，样式（区域/透明度/
         字号/密度/速度/偏移）服从弹幕设置弹层。 -->
    <div
      v-if="danmakuSiteOn && danmaku.enabled && !softActive"
      class="wp-dm-layer"
      :class="{ half: danmaku.area === 'half' }"
      aria-hidden="true"
    >
      <span
        v-for="f in flying"
        :key="f.key"
        class="wp-dm-item"
        :style="{
          top: `${f.lane * 12.5}%`,
          animationDuration: `${f.duration}s`,
          fontSize: `${f.sizePx}px`,
          opacity: danmaku.opacity / 100,
        }"
      >{{ f.text }}</span>
    </div>

    <!-- 顶部栏：返回钮 + 一起看（差距 #1/#2）。桌面（≥768px）返回是
         Ø40 玻璃圆钮、顶栏不置标题（标题只留左下元信息区）；手机返回是
         文字胶囊（高 40、圆角 20、与一起看同款玻璃）：无集标题时
         「← 返回」，有集标题时「← ＋集标题单行截断」。 -->
    <header v-show="chromeVisible" class="wp-top">
      <div class="wp-top-left">
        <button type="button" class="wp-back" aria-label="返回" @click="goBack">
          <ChevronLeft :size="21" class="wp-back-icon" />
          <span class="wp-back-text">{{ backPillText }}</span>
        </button>
      </div>
      <button
        type="button"
        class="wp-together"
        :class="{ on: wtInRoom }"
        @click="onTogether"
      >
        <Users :size="15" />
        <span>一起看</span>
        <span v-if="wtInRoom" class="wp-together-count">{{ wtMembers.length }} 人</span>
      </button>
    </header>

    <!-- 播放信息叠显（差距 #17）：设置里打开后，原键值信息以小字叠在
         画面左上（顶栏下方），关掉即隐藏；内容随播放进度/倍速实时更新 -->
    <dl v-if="infoVisible" class="wp-info-overlay" aria-hidden="true">
      <div v-for="line in infoLines" :key="line.label" class="wp-info-line">
        <dt>{{ line.label }}</dt>
        <dd>{{ line.value }}</dd>
      </div>
    </dl>

    <!-- 未开始：中央彩虹边开始胶囊 -->
    <div v-if="phase === 'idle'" class="wp-center">
      <div class="wp-start-pill">
        <button type="button" class="wp-start-main" @click="start(false)">
          <Play :size="19" class="wp-start-icon" />
          <span>{{ resumeSeconds > 5 ? '播放' : '开始播放' }}</span>
        </button>
        <template v-if="resumeSeconds > 5">
          <span class="wp-start-div" />
          <button type="button" class="wp-start-restart" @click="start(true)">重启</button>
        </template>
      </div>
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

      <!-- 弹幕发送行：站点开关开时才出现，贴在控制条上方 -->
      <form
        v-if="danmakuSiteOn"
        v-show="controlsVisible && !locked"
        class="wp-dm-send"
        @submit.prevent="sendDanmaku"
      >
        <input
          v-model="danmakuText"
          class="wp-dm-input"
          type="text"
          :maxlength="danmakuMaxLen"
          :placeholder="`发条弹幕，最多 ${danmakuMaxLen} 字`"
          aria-label="弹幕内容"
          @click.stop
        />
        <button
          type="submit"
          class="wp-dm-send-btn"
          :disabled="danmakuSending || !danmakuText.trim()"
        >
          发送
        </button>
      </form>

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
              v-if="danmakuSiteOn"
              type="button"
              class="wp-icon wp-danmaku-btn"
              :class="{ off: !danmaku.enabled, on: danmaku.enabled }"
              aria-label="弹幕设置"
              @click="openDanmaku"
            >
              <span class="wp-danmaku-txt">弹</span>
              <span v-if="!danmaku.enabled" class="wp-danmaku-slash" />
            </button>
            <button
              type="button"
              class="wp-icon"
              :class="{ on: primarySub !== null }"
              aria-label="字幕"
              @click="openSubtitles"
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
      <h2>{{ noticeTitle }}</h2>
      <p>{{ noticeText }}</p>
      <p v-if="phase === 'unsupported' && softFailReason" class="wp-notice-sub">
        软解也没能启动：{{ softFailReason }}
      </p>
      <div class="wp-notice-actions">
        <button
          v-if="phase === 'unsupported' && decodeAlt"
          type="button"
          class="wp-notice-btn wp-notice-primary"
          @click="switchToAlt"
        >
          换到能播的版本
        </button>
        <button
          v-if="phase === 'unsupported' && decodeAlt"
          type="button"
          class="wp-notice-btn"
          @click="goBack"
        >
          返回详情
        </button>
        <button
          v-else
          type="button"
          class="wp-notice-btn wp-notice-primary"
          @click="goBack"
        >
          返回详情
        </button>
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
      @update:font-size="onSubtitleFontSize"
    />
    <PlayerTogetherSheet
      :open="togetherOpen"
      :in-room="wtInRoom"
      :is-host="wtIsHost"
      :code="wtCode"
      :invite-link="wtInviteLink"
      :members="wtMembers"
      :self-id="selfUserId"
      :busy="wtBusy"
      :error="wtError"
      :join-code="wtJoinCode"
      @update:open="togetherOpen = $event"
      @create="wt.createRoom(itemId)"
      @join="wt.joinRoom(wtJoinCode)"
      @leave="onWtLeave"
      @dissolve="onWtDissolve"
    />
    <PlayerSettingsSheet
      :open="settingsOpen"
      :sources="sourceOptions"
      :current-source-id="currentSource?.Id || ''"
      :episodes="episodeOptions"
      :current-episode-id="item?.Id || ''"
      :rate="rate"
      :info-visible="infoVisible"
      :auto-play-next="autoPlayNext"
      :pip-supported="pipSupported"
      @update:open="settingsOpen = $event"
      @update:rate="onRate"
      @update:info-visible="onInfoVisible"
      @update:auto-play-next="onAutoPlayNext"
      @select-source="onSelectSource"
      @select-episode="onSelectEpisode"
      @screenshot="onScreenshot"
      @pip="onPip"
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
import {
  fetchDanmakuWindow,
  postDanmaku,
  type DanmakuItem,
} from '@/api/danmaku'
import { validatePlayLine } from '@/composables/usePlayLines'
import {
  probeDecode,
  probeDecodeSync,
  type DecodeVerdict,
} from '@/utils/decodeSupport'
import {
  createSoftPlayer,
  fetchSoftDecodeEnabled,
  humanizeSoftError,
  pickSoftRelayUrl,
  probeSoftMseSupport,
  shouldSoftDecode,
  softDecodePipelineDecision,
  softDecodeResolutionGate,
  SOFTDECODE_MSE_ENABLED,
  SOFT_UHD_BLOCK_MESSAGE,
  type SoftPlayerHandle,
} from '@/utils/softDecode'
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
import PlayerTogetherSheet from '@/components/player/PlayerTogetherSheet.vue'
import {
  useWatchTogether,
  type WtSnapshot,
} from '@/composables/useWatchTogether'
import { useUserStore } from '@/stores/user'
import {
  loadLocalPlayerPrefs,
  saveLocalPlayerPrefs,
  schedulePushPlayerPrefs,
  syncPlayerPrefsFromServer,
  type PlayerPrefs,
} from '@/api/playerPrefs'

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
// 弹幕设置默认值：必须声明在 danmaku ref（loadDanmaku 调用点）之前，
// 放函数体外靠后会 TDZ（loadDanmaku 在 setup 前段就被调用）。
const DANMAKU_DEFAULTS: DanmakuSettings = {
  enabled: true,
  area: 'full',
  opacity: 100,
  fontSize: 'medium',
  density: 'standard',
  speed: 'standard',
  offsetSeconds: 0,
}
function withDanmakuDefaults(p?: Partial<DanmakuSettings> | null): DanmakuSettings {
  return { ...DANMAKU_DEFAULTS, ...(p ?? {}) }
}

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
/** 软解失败的人话原因（回落提示卡小字）：软解试过又失败时才有值，不许再静默吞掉 */
const softFailReason = ref('')
const toast = ref('')
// 解码探测结论（utils/decodeSupport）：当前版本浏览器解不了时，
// 提示卡走「解码」口径并给出能播的替代版本（decodeAlt）
const decodeVerdict = ref<DecodeVerdict | null>(null)
const decodeAlt = ref<EmbyMediaSource | null>(null)
const verdictCache = ref(new Map<string, DecodeVerdict>())
// 软解播放器（批 2）：浏览器硬解失败且开关开时启用，渲染在 .wp-soft
// 容器里（AVPlayer 自带 canvas）。softFailedFor 记最近一次软解失败的
// 版本 id——失败只回落提示卡一次，不许自动重试循环；用户点「再试一次」
// 才清掉它（retry 里）。
const softEl = ref<HTMLDivElement | null>(null)
const softHandle = ref<SoftPlayerHandle | null>(null)
/** 软解起播中标记：构造 AVPlayer 前就得亮出容器——它按容器尺寸初始化
 * 渲染器，display:none（0×0）下建出来只会黑屏（2026-11-15 实证） */
const softStarting = ref(false)
const softActive = computed(() => softHandle.value !== null || softStarting.value)
const softFailedFor = ref('')
const noticeTitle = computed(() => {
  if (phase.value === 'unsupported') {
    return decodeVerdict.value ? '这个版本浏览器解不了' : '这个格式网页里播不了'
  }
  return '播放没能开始'
})

const isPaused = ref(true)
const currentTime = ref(0)
const duration = ref(0)
// 播放器设置（⚠️11 同步）：先用本地值起播（不阻塞），挂载后异步拉
// 服务端合并补缺（见 applyRemotePlayerPrefs）；本地存取/合并/回推的
// 唯一实现在 @/api/playerPrefs。
const bootPrefs = loadLocalPlayerPrefs()
const rate = ref(
  typeof bootPrefs.rate === 'number' && bootPrefs.rate > 0 ? bootPrefs.rate : 1,
)
/** 自动连播（差距 #20）：缺省开（保持旧行为），关后播完停在本集；
    随播放器偏好一同持久化（见 currentPlayerPrefs） */
const autoPlayNext = ref(bootPrefs.autoPlayNext !== false)
/** 播放信息叠显（差距 #17）：设置里的开/关，开时键值信息叠在画面一角 */
const infoVisible = ref(false)
const controlsVisible = ref(true)
const locked = ref(false)
const seeking = ref(false)
const seekPreview = ref(0)

const danmakuOpen = ref(false)
const subtitleOpen = ref(false)
const settingsOpen = ref(false)
const togetherOpen = ref(false)
const primarySub = ref<number | null>(null)
const secondarySub = ref<number | null>(null)
const subtitleDelayMs = ref(bootPrefs.subtitle?.delayMs ?? 0)
const subtitleFontSize = ref<SubtitleFontSize>(bootPrefs.subtitle?.fontSize ?? 'default')
const danmaku = ref<DanmakuSettings>(loadDanmaku())

// ---------- 站内弹幕（数据面） ----------
// 站点开关（后端 danmaku_enabled）关时拉取口 404 → danmakuSiteOn=false，
// 弹幕按钮/发送行/弹幕层整组不渲染。数据按 180 秒时间窗分片拉取、
// 合并进 danmakuPool（按 id 去重、按时间轴排序），播放头走到点上冒出。
const DANMAKU_CHUNK_SECONDS = 180
const danmakuSiteOn = ref(false)
const danmakuMaxLen = ref(100)
const danmakuPool = ref<DanmakuItem[]>([])
const danmakuText = ref('')
const danmakuSending = ref(false)
interface FlyingDanmaku {
  key: number
  text: string
  lane: number
  duration: number
  sizePx: number
}
const flying = ref<FlyingDanmaku[]>([])
let danmakuFetchedUntil = 0
let danmakuFetching = false
let danmakuSpawnIdx = 0
let danmakuLastTick = 0
let danmakuKeySeq = 1
let danmakuLaneCursor = 0
const danmakuTimers = new Set<number>()

let controlsTimer: number | undefined
let progressTimer: number | undefined
let toastTimer: number | undefined
let probeTimer: number | undefined
let probeTimer2: number | undefined
let lastOpenAt = 0
let startedFromResume = false
let reportedPlayingFor = ''
let lastVideoErrorAt = 0

// ---------- 一起看 ----------
// 通道只传控制事件与进度，取流仍走本页自己的 PlaybackInfo（各自
// 账号的签名链路），见 composables/useWatchTogether.ts 的红线说明。
const userStore = useUserStore()
const selfUserId = computed(() => userStore.user?.id ?? 0)
const wtJoinCode = computed(() =>
  String(route.query.wt || '').trim().toUpperCase(),
)
let wtSuppressUntil = 0
let wtPendingAlign: { position: number; playing: boolean; at: number } | null =
  null
let wtStateTimer: number | undefined

const wt = useWatchTogether({
  onCreated: () => {
    showToast('一起看已创建，把链接发给朋友')
  },
  onJoined: (snap: WtSnapshot) => {
    wtPendingAlign = {
      position: snap.state.position,
      playing: snap.state.playing,
      at: Date.now(),
    }
    togetherOpen.value = false
    showToast('已加入一起看，正在对齐进度')
    if (phase.value === 'idle') void start(false)
    else if (phase.value === 'playing') wtConsumeAlign()
  },
  onEvent: (e) => {
    if (phase.value !== 'playing' && phase.value !== 'preparing') return
    const v = videoEl.value
    if (!v) return
    wtSuppressUntil = Date.now() + 900
    const who = e.from_username || '同伴'
    if (e.event === 'seek') {
      wtSafeSeek(e.position)
      showToast(`${who} 跳到了 ${formatClock(e.position)}`)
    } else if (e.event === 'play') {
      if (Math.abs((v.currentTime || 0) - e.position) > 2.5) {
        wtSafeSeek(e.position)
      }
      void v.play().catch(() => {})
      showToast(`${who} 继续播放`)
    } else if (e.event === 'pause') {
      v.pause()
      showToast(`${who} 暂停了`)
    }
  },
  onSync: (s) => {
    if (phase.value !== 'playing') return
    const v = videoEl.value
    if (!v) return
    if (Math.abs((v.currentTime || 0) - s.position) > 2.5) {
      wtSuppressUntil = Date.now() + 900
      wtSafeSeek(s.position)
    }
    if (s.playing && v.paused) {
      wtSuppressUntil = Date.now() + 900
      void v.play().catch(() => {})
    } else if (!s.playing && !v.paused) {
      wtSuppressUntil = Date.now() + 900
      v.pause()
    }
  },
  onMemberJoined: (m) => {
    showToast(`${m.username || '朋友'} 加入了一起看`)
  },
  onMemberLeft: () => {
    showToast('有成员退出了一起看')
  },
  onHostChanged: (hostId) => {
    if (hostId === selfUserId.value) showToast('房主转给了你')
  },
  onEnded: (reason) => {
    wtPendingAlign = null
    showToast(reason === 'disconnected' ? '一起看连接断了' : '一起看已结束')
  },
  onError: (message) => {
    showToast(message)
  },
})
const {
  inRoom: wtInRoom,
  isHost: wtIsHost,
  code: wtCode,
  members: wtMembers,
  busy: wtBusy,
  lastError: wtError,
} = wt

const wtInviteLink = computed(() =>
  wtCode.value
    ? `${window.location.origin}/play/${itemId.value}?wt=${wtCode.value}`
    : '',
)

function wtSafeSeek(pos: number): void {
  const v = videoEl.value
  if (!v || !Number.isFinite(pos)) return
  try {
    v.currentTime = Math.max(0, pos)
  } catch {
    /* 不支持定位的流忽略 */
  }
}

function wtConsumeAlign(): void {
  const pending = wtPendingAlign
  if (!pending) return
  wtPendingAlign = null
  const v = videoEl.value
  if (!v) return
  const elapsed = pending.playing ? (Date.now() - pending.at) / 1000 : 0
  wtSuppressUntil = Date.now() + 900
  wtSafeSeek(pending.position + elapsed)
  if (!pending.playing) v.pause()
}

/** 本地控制动作往房间广播；刚应用完远端事件的抑制窗内不回声 */
function wtBroadcast(kind: 'play' | 'pause' | 'seek'): void {
  if (!wtInRoom.value) return
  if (Date.now() < wtSuppressUntil) return
  const v = videoEl.value
  wt.sendEvent(
    kind,
    v?.currentTime || currentTime.value,
    kind === 'seek' ? !isPaused.value : undefined,
  )
}

function onWtLeave(): void {
  wt.leave()
  togetherOpen.value = false
}

function onWtDissolve(): void {
  wt.dissolve()
  togetherOpen.value = false
  showToast('一起看已结束')
}

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

/** 手机顶栏返回胶囊的文字（差距 #1）：有集标题（剧集的本集名）时显
    集标题（单行截断），否则「返回」；桌面圆钮形态下文字被 CSS 隐藏 */
const backPillText = computed(() => {
  const it = item.value
  if (it?.Type === 'Episode' && it.Name) return it.Name
  return '返回'
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
  if (softHandle.value) return '浏览器硬解不支持，正在用站内软解播放（首次加载解码器约需几秒）'
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
    const verdict = verdictCache.value.get(s.Id)
    const metaParts = [
      s.Size ? formatSize(s.Size) : '',
      verdict && (verdict.badVideo || verdict.badAudio) ? '浏览器解不了' : '',
    ].filter(Boolean)
    return {
      id: s.Id,
      label: s.Name || parts.join(' ') || '默认版本',
      meta: metaParts.length ? metaParts.join(' · ') : undefined,
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
  // 本地存取收敛在 @/api/playerPrefs（新键整对象 + 旧键 danmaku 补入）
  return withDanmakuDefaults(loadLocalPlayerPrefs().danmaku)
}
function errorText(e: unknown, fallback: string): string {
  const err = e as {
    response?: {
      status?: number
      data?: {
        detail?: string | { error_code?: string; message?: string }
        message?: string
        msg?: string
      }
    }
    message?: string
  }
  const data = err?.response?.data
  // 流量拦截（后端 403 + 结构化 detail）：透出它的人话 message，
  // 不许落到下面 403 的播放门禁文案上（那是另一件事）。
  const detail = data?.detail
  if (detail && typeof detail === 'object') {
    if (detail.error_code === 'TRAFFIC_EXHAUSTED' && detail.message) return detail.message
    if (typeof detail.message === 'string' && detail.message) return detail.message
  }
  const text = (typeof detail === 'string' ? detail : undefined) || data?.message || data?.msg
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
    // 站内弹幕：条目定了就探一次站点开关并预拉首窗（关时 404 静默隐藏）
    void initDanmaku(id)
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

// ---------- 弹层选线 / 选字幕（路由 query 契约，见 PlayerSchemeSheet） ----------

/** 已校验的选定线路入口（空串 = 账号默认入口）。本次播放的 EA 请求
    （PlaybackInfo + 会话上报）都走它；流地址仍由服务端下发不改。 */
const lineBase = ref('')
let lineResolvedFor: string | null = null

/** 认弹层带来的 line 参数：必须命中账号下发清单才生效，手改不认 */
async function ensureLineBase(): Promise<void> {
  const raw = String(route.query.line || '')
  if (lineResolvedFor === raw) return
  lineResolvedFor = raw
  lineBase.value = raw ? await validatePlayLine(raw) : ''
}

/** 认弹层带来的 sub 参数：off = 本次不挂字幕；数字 = 字幕流 Index
    （须是本条目某源的文本字幕轨才认，最终挂哪条由 rebuild 时按源过滤）。
    没带 sub 时保持服务端默认轨口径。 */
function applyRouteSub(): void {
  const raw = String(route.query.sub || '')
  if (raw === 'off') {
    primarySub.value = null
    return
  }
  const idx = raw ? Number(raw) : NaN
  if (Number.isFinite(idx)) {
    const ok = (item.value?.MediaSources || []).some((ms) =>
      streamsOf(ms).some(
        (s) => s.Type === 'Subtitle' && s.IsTextSubtitleStream && s.DeliveryUrl && s.Index === idx,
      ),
    )
    if (ok) {
      primarySub.value = idx
      return
    }
  }
  const dft = item.value?.MediaSources?.[0]?.DefaultSubtitleStreamIndex
  if (dft != null && primarySub.value === null) primarySub.value = dft
}

// ---------- 起播 ----------

async function start(restart: boolean): Promise<void> {
  const v = videoEl.value
  if (!v || !item.value) return
  startedFromResume = !restart && resumeSeconds.value > 5
  phase.value = 'preparing'
  prepStepIndex.value = 0
  try {
    await ensureLineBase()
    if (!playbackInfo.value) {
      playbackInfo.value = await fetchPlaybackInfo(item.value.Id, lineBase.value || undefined)
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
    // 起播前先把所有版本的解码能力探一遍（学友站播放页：先探测、
    // 再决定路径）：选中的版本浏览器明确解不了，就先看能不能走站内
    // 软解兜底，不行再出解码提示卡给出路，不让它白转 30 秒最后误报成
    // 「源暂时不可用」
    decodeVerdict.value = null
    decodeAlt.value = null
    await probeAllSources(info.MediaSources)
    // 一起看晚入者优先对齐房间进度（按快照时间戳外推），盖过本地续播点
    const wtStartAt = wtPendingAlign
      ? wtPendingAlign.position +
        (wtPendingAlign.playing
          ? (Date.now() - wtPendingAlign.at) / 1000
          : 0)
      : null
    await playSource(
      source,
      wtStartAt ?? (startedFromResume ? resumeSeconds.value : 0),
    )
  } catch (e) {
    noticeText.value = errorText(e, '取播放地址失败，稍后再试。')
    phase.value = 'error'
  }
}

async function openSource(source: EmbyMediaSource, atSeconds: number): Promise<void> {
  const v = videoEl.value
  if (!v) return
  decodeVerdict.value = null
  decodeAlt.value = null
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
      failWithDiagnosis(source, '视频数据迟迟没到')
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

/**
 * 选定版本后的统一入口：解不了的版本先看能不能走站内软解兜底，
 * 不行再按现行口径（硬拦的出解码提示卡，其余先试着播）。
 */
async function playSource(source: EmbyMediaSource, atSeconds: number): Promise<void> {
  // 切版本：旧的软解实例先停掉，新版本再按决策重建（软解/原生二选一）
  if (softHandle.value) await destroySoft()
  softFailReason.value = ''
  const verdict = verdictCache.value.get(source.Id)
  if (verdict?.status === 'unsupported' && (verdict.badVideo || verdict.badAudio)) {
    if (await startSoftDecode(source, verdict, atSeconds)) return
    if (verdict.hardBlock) {
      showDecodeUnsupported(source, verdict)
      return
    }
  }
  await openSource(source, atSeconds)
}

/** 销毁软解实例（切版本/回落/卸载时调用），失败不影响回落。 */
async function destroySoft(): Promise<void> {
  const h = softHandle.value
  softHandle.value = null
  softStarting.value = false
  if (!h) return
  try {
    await h.destroy()
  } catch {
    /* 销毁失败不影响回落 */
  }
}

/**
 * 站内软解起播：开关关、编解码不在子集里、本版本已失败过，三者任一
 * 命中即返回 false（调用方按现行路径走）。起播成功返回 true；起播中
 * 的任何失败（wasm 拉不到、分析失败、超时）由 createSoftPlayer 内部
 * 销毁实例，这里只记失败版本、返回 false——调用方回落解码提示卡，
 * 不重试、不黑屏。
 */
async function startSoftDecode(
  source: EmbyMediaSource,
  verdict: DecodeVerdict,
  atSeconds: number,
): Promise<boolean> {
  if (softFailedFor.value === source.Id) return false
  const container = softEl.value
  if (!container) return false
  if (!shouldSoftDecode(verdict, source, await fetchSoftDecodeEnabled())) return false
  // 4K 分流：4K HEVC 纯 wasm 解码在 iOS Safari 上内存爆掉、页面被
  // 系统反复杀（2026-11-17 事故，阈值口径见 softDecode.ts 的
  // softDecodeResolutionGate，用所选源视频流的实际宽高）。达 4K 线
  // 的源第一顺位走 MSE 原生管线：先按真实参数做 MSE 预检（移植
  // 探针页 #279 已真机验通的闸门），过检才以 checkUseMSE 改判
  // 构造试播（帧交系统播、不进页面内存）；预检不过/开关关，维持
  // #260 行为——不实例化 AVPlayer、直接出提示卡，返回 true 表示
  // 已处理。MSE 起播失败（含被否回解码管线的看门狗判失败）的
  // 回落也在下面 catch 与 onError 里接回同一张提示卡，只试一次。
  // WebCodecs 硬解尝试（#261）已被真机证明照样崩页，整段退役。
  let mseAttempt = false
  if (softDecodeResolutionGate(source, true).blocked) {
    const mseSupported = SOFTDECODE_MSE_ENABLED
      ? probeSoftMseSupport(source).supported
      : false
    if (
      softDecodePipelineDecision(source, true, {
        enabled: SOFTDECODE_MSE_ENABLED,
        supported: mseSupported,
      }).pipeline === 'mse'
    ) {
      mseAttempt = true
    } else {
      showSoftUhdBlocked(source, verdict)
      return true
    }
  }
  // 先停掉原生 video（切版本时它可能还在播旧源），再建软解实例
  videoEl.value?.pause()
  softFailedFor.value = source.Id
  phase.value = 'preparing'
  prepStepIndex.value = 2
  // 软解取流地址：优先同条目的中继兄弟源（本人转存副本等场景下，
  // 选中源是无 CORS 的直链、机械换标记又换到不可用的变体，只有
  // 清单里的中继兄弟源能出字节）；没有兄弟源时用选中源原地址，
  // 后续仍走 createSoftPlayer 内的 resolveSoftDecodeUrl 裁决。
  const url =
    pickSoftRelayUrl(playbackInfo.value?.MediaSources, source.Id) ||
    source.DirectStreamUrl ||
    ''
  let started = false
  let seekDone = false
  // 先亮出软解容器再构造播放器（见 softStarting 注释：隐藏容器下
  // AVPlayer 渲染器按 0×0 初始化，只会黑屏）
  softStarting.value = true
  try {
    softHandle.value = await createSoftPlayer(
      container,
      url,
      {
        onFirstVideo: () => {
          const h = softHandle.value
          if (!h) return
          const d = h.durationSeconds()
          if (Number.isFinite(d) && d > 0) duration.value = d
          if (atSeconds > 0 && !seekDone) {
            seekDone = true
            void h.seek(atSeconds).catch(() => {})
          }
          if (phase.value === 'preparing') {
            phase.value = 'playing'
            prepStepIndex.value = 3
            pokeControls()
          }
        },
        onFirstAudio: () => {},
        onPlaying: () => {
          isPaused.value = false
          reportStartIfNeeded()
          wtBroadcast('play')
        },
        onPaused: () => {
          isPaused.value = true
          sendProgress(true)
          wtBroadcast('pause')
        },
        onTime: (s: number) => {
          currentTime.value = s
          const h = softHandle.value
          if (h) {
            const d = h.durationSeconds()
            if (Number.isFinite(d) && d > 0) duration.value = d
          }
        },
        onEnded: () => onEnded(),
        onError: (message: string) => {
          // 失败原因先记下（起播中/中途都记）：回落提示卡要带这句人话，
          // 不许再静默吞掉（2026-11-15 软解无声卡死教训）。
          softFailReason.value = humanizeSoftError(message)
          // 起播中的失败由调用方（playSource）决定回落，这里只处理
          // 播起来之后的中途失败：销毁实例、回落解码提示卡，只一次。
          if (!started) return
          if (phase.value !== 'playing' && phase.value !== 'preparing') return
          if (currentSource.value?.Id !== source.Id) return
          // MSE 试播的会话报错回落 #260 的 4K 提示卡；普通软解照旧
          // 回落解码提示卡。都只落一次，不重试。
          void destroySoft().then(() =>
            mseAttempt
              ? showSoftUhdBlocked(source, verdict)
              : showDecodeUnsupported(source, verdict),
          )
        },
      },
      {
        ext: (source.Container || '').toLowerCase() || undefined,
        mse: mseAttempt,
      },
    )
  } catch (e) {
    // createSoftPlayer 的 failOnce 已先把原因递到 onError；这里兜底
    // （如构造本身抛错、onError 没机会触发的情形），保证卡上有原因。
    if (!softFailReason.value) {
      softFailReason.value = humanizeSoftError(
        e instanceof Error ? e.message : String(e),
      )
    }
    softHandle.value = null
    softStarting.value = false
    if (mseAttempt) {
      // MSE 试播起播失败：实例已由 createSoftPlayer 内部销毁，这里
      // 直接回落 #260 的 4K 提示卡（返回 true＝已处理），不让调用
      // 方再走原生试播，不重试。
      showSoftUhdBlocked(source, verdict)
      return true
    }
    return false
  }
  // 实例已接管 softActive，起播中标记可以撤了
  softStarting.value = false
  started = true
  return true
}

/** 把 PlaybackInfo 里的每个版本都探一遍，结果进缓存（设置面板给版本标「浏览器解不了」也用它） */
async function probeAllSources(sources: EmbyMediaSource[]): Promise<void> {
  await Promise.all(
    sources.map(async (s) => {
      if (!verdictCache.value.has(s.Id)) {
        verdictCache.value.set(s.Id, await probeDecode(s))
      }
    }),
  )
}

/** 解码提示文案：点名解不了的编码，再给真实出路，不让用户去「稍后再试」 */
function decodeNoticeText(verdict: DecodeVerdict, hasAlt: boolean): string {
  let head: string
  if (verdict.badVideo && verdict.badAudio) {
    head = `这集的画面是 ${verdict.videoName} 编码、声音是 ${verdict.audioName} 编码，你现在用的浏览器这两个都解不了。`
  } else if (verdict.badVideo) {
    head = `这集的画面是 ${verdict.videoName} 编码，你现在用的浏览器没有这个解码能力，网页里放不出画面。`
  } else if (verdict.badAudio) {
    head = `这集的声音是 ${verdict.audioName} 编码，你现在用的浏览器解不了这个声音格式，播出来会没有声音。`
  } else {
    head = '这个版本用的编码，你现在用的浏览器解不了。'
  }
  const tail = hasAlt
    ? '不是源坏了，也不是网络问题，重试也不会变好。同片有其他版本是能播的，点下面按钮直接换过去；也可以回详情页用播放器 App（Infuse / SenPlayer / VidHub）打开，App 自带解码，这个版本能直接播。'
    : '不是源坏了，也不是网络问题，重试也不会变好。回详情页看看有没有其他能播的版本，或者用播放器 App（Infuse / SenPlayer / VidHub）打开，App 自带解码，这个版本能直接播。'
  return head + tail
}

function showDecodeUnsupported(source: EmbyMediaSource, verdict: DecodeVerdict): void {
  const alt = (playbackInfo.value?.MediaSources || []).find(
    (s) => s.Id !== source.Id && verdictCache.value.get(s.Id)?.status === 'supported',
  )
  decodeVerdict.value = verdict
  decodeAlt.value = alt || null
  noticeText.value = decodeNoticeText(verdict, !!alt)
  phase.value = 'unsupported'
}

/** 4K 软解护栏命中：与解码提示卡同一套样式与出路（能播的替代版本/
    返回详情），正文换成护栏的人话文案，不让用户对着崩溃页循环重进 */
function showSoftUhdBlocked(source: EmbyMediaSource, verdict: DecodeVerdict): void {
  const alt = (playbackInfo.value?.MediaSources || []).find(
    (s) => s.Id !== source.Id && verdictCache.value.get(s.Id)?.status === 'supported',
  )
  decodeVerdict.value = verdict
  decodeAlt.value = alt || null
  noticeText.value = SOFT_UHD_BLOCK_MESSAGE
  phase.value = 'unsupported'
}

async function switchToAlt(): Promise<void> {
  const alt = decodeAlt.value
  if (!alt) return
  await onSelectSource(alt.Id)
}

/**
 * 失败分流：先看解码探测结论——探明解不了的走解码提示卡，
 * 不再只按容器名猜（MP4 里装 HEVC+AC3 以前会被误报成「源不可用」）。
 */
function failWithDiagnosis(source: EmbyMediaSource, why: string): void {
  const verdict = verdictCache.value.get(source.Id) || probeDecodeSync(source)
  if (verdict.status === 'unsupported' && (verdict.badVideo || verdict.badAudio)) {
    showDecodeUnsupported(source, verdict)
    return
  }
  failByContainer(source, why)
}

function failByContainer(source: EmbyMediaSource, why: string): void {
  decodeVerdict.value = null
  decodeAlt.value = null
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
  // 用户明确点「再试一次」：清掉软解失败标记，给软解一次新的尝试机会；
  // 失败本身仍只回落一次，不会自动重试循环。
  await destroySoft()
  softFailedFor.value = ''
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
  wtConsumeAlign()
}
function onTimeUpdate(): void {
  const v = videoEl.value
  if (!v || seeking.value) return
  currentTime.value = v.currentTime || 0
}
function onPlayEvent(): void {
  isPaused.value = false
  reportStartIfNeeded()
  wtBroadcast('play')
}
function onPauseEvent(): void {
  isPaused.value = true
  sendProgress(true)
  wtBroadcast('pause')
}
function onEnded(): void {
  sendStopped()
  if (autoPlayNext.value && nextEpisode.value) {
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
  if (currentSource.value) failWithDiagnosis(currentSource.value, '浏览器拒绝了这个视频流')
}

function onVideoTap(): void {
  if (locked.value) return
  if (phase.value === 'playing') {
    controlsVisible.value = !controlsVisible.value
    if (controlsVisible.value) pokeControls()
  }
}

/** 软解画布点按：与原生 video 点按同一口径（切换控制层显隐） */
function onSoftTap(): void {
  onVideoTap()
}

/** 软解模式暂不支持字幕/弹幕（字幕轨挂在原生 video 的 <track> 上，
    软解画布不走这条路）：点按钮时明说，不静默无反应。 */
function openDanmaku(): void {
  if (softActive.value) {
    showToast('软解模式暂不支持弹幕')
    return
  }
  danmakuOpen.value = true
}
function openSubtitles(): void {
  if (softActive.value) {
    showToast('软解模式暂不支持字幕')
    return
  }
  subtitleOpen.value = true
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
  reportPlaying(reportPayload(), lineBase.value || undefined).catch(() => {})
  window.clearInterval(progressTimer)
  progressTimer = window.setInterval(() => sendProgress(false), 15000)
}
function sendProgress(paused: boolean): void {
  if (!item.value || phase.value !== 'playing') return
  reportProgress(reportPayload(), paused, lineBase.value || undefined).catch(() => {})
}
function sendStopped(): void {
  if (!item.value) return
  const payload = reportPayload()
  reportStopped(payload, lineBase.value || undefined).catch(() =>
    reportStoppedBeacon(payload, lineBase.value || undefined),
  )
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
  const soft = softHandle.value
  if (soft) {
    if (isPaused.value) void soft.play()
    else void soft.pause()
    pokeControls()
    return
  }
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
  const val = Number((e.target as HTMLInputElement).value)
  const target = (val / 1000) * (duration.value || 0)
  const soft = softHandle.value
  if (soft) {
    void soft.seek(target).catch(() => {})
    seeking.value = false
    currentTime.value = target
    sendProgress(isPaused.value)
    pokeControls()
    wtBroadcast('seek')
    return
  }
  const v = videoEl.value
  if (!v) return
  try {
    v.currentTime = target
  } catch {
    /* 不支持定位的流忽略 */
  }
  seeking.value = false
  currentTime.value = target
  sendProgress(isPaused.value)
  pokeControls()
  wtBroadcast('seek')
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

// ---------- 站内弹幕（拉取 / 冒出 / 发送） ----------

const DM_FONT_PX = { small: 15, medium: 18, large: 22 } as const
const DM_DURATION_S = { slow: 13, standard: 10, fast: 7.5 } as const
const DM_CAP = { low: 6, standard: 14, high: 26 } as const
const DM_LANES = 8
/** 自己刚发的弹幕已手动冒出过，播放头走到点上时不要再冒第二遍 */
const danmakuLocalPosted = new Set<number>()

function resetDanmakuData(): void {
  danmakuSiteOn.value = false
  danmakuPool.value = []
  flying.value = []
  danmakuFetchedUntil = 0
  danmakuFetching = false
  danmakuSpawnIdx = 0
  danmakuLastTick = 0
  danmakuText.value = ''
  danmakuLocalPosted.clear()
  for (const t of danmakuTimers) window.clearTimeout(t)
  danmakuTimers.clear()
}

async function initDanmaku(guid: string): Promise<void> {
  resetDanmakuData()
  try {
    const win = await fetchDanmakuWindow(guid, 0, DANMAKU_CHUNK_SECONDS)
    danmakuSiteOn.value = true
    danmakuMaxLen.value = win.max_text_length || 100
    mergeDanmakuItems(win.items)
    danmakuFetchedUntil = DANMAKU_CHUNK_SECONDS
  } catch {
    // 404 = 站点开关关：整组弹幕 UI 不出现，不打扰播放
    danmakuSiteOn.value = false
  }
}

function mergeDanmakuItems(items: DanmakuItem[]): void {
  if (!items.length) return
  const seen = new Set(danmakuPool.value.map((i) => i.id))
  const fresh = items.filter((i) => !seen.has(i.id))
  if (!fresh.length) return
  danmakuPool.value = [...danmakuPool.value, ...fresh].sort(
    (a, b) => a.position_seconds - b.position_seconds || a.id - b.id,
  )
}

async function prefetchDanmaku(): Promise<void> {
  if (!danmakuSiteOn.value || danmakuFetching) return
  if (
    duration.value > 0 &&
    danmakuFetchedUntil > duration.value + DANMAKU_CHUNK_SECONDS
  ) {
    return
  }
  danmakuFetching = true
  const from = danmakuFetchedUntil
  try {
    const win = await fetchDanmakuWindow(
      itemId.value,
      from,
      from + DANMAKU_CHUNK_SECONDS,
    )
    mergeDanmakuItems(win.items)
    danmakuFetchedUntil = from + DANMAKU_CHUNK_SECONDS
  } catch {
    /* 拉取失败静默：播放头继续走，下一轮预取点再试 */
  } finally {
    danmakuFetching = false
  }
}

function spawnDanmaku(entry: DanmakuItem): void {
  if (flying.value.length >= DM_CAP[danmaku.value.density]) return
  const key = danmakuKeySeq++
  const dur = DM_DURATION_S[danmaku.value.speed]
  flying.value = [
    ...flying.value,
    {
      key,
      text: entry.text,
      lane: danmakuLaneCursor++ % DM_LANES,
      duration: dur,
      sizePx: DM_FONT_PX[danmaku.value.fontSize],
    },
  ]
  const timer = window.setTimeout(() => {
    danmakuTimers.delete(timer)
    flying.value = flying.value.filter((f) => f.key !== key)
  }, dur * 1000 + 300)
  danmakuTimers.add(timer)
}

function danmakuTick(t: number): void {
  if (
    !danmakuSiteOn.value ||
    !danmaku.value.enabled ||
    softActive.value ||
    phase.value !== 'playing'
  ) {
    danmakuLastTick = t
    return
  }
  const offset = danmaku.value.offsetSeconds
  // 跳转（seek / 换集）：重定位冒出下标、清掉在飞的，避免旧点弹幕串场
  if (Math.abs(t - danmakuLastTick) > 2) {
    flying.value = []
    const idx = danmakuPool.value.findIndex(
      (i) => i.position_seconds + offset > t,
    )
    danmakuSpawnIdx = idx < 0 ? danmakuPool.value.length : idx
  }
  while (danmakuSpawnIdx < danmakuPool.value.length) {
    const entry = danmakuPool.value[danmakuSpawnIdx]
    if (entry.position_seconds + offset > t) break
    if (
      entry.position_seconds + offset > danmakuLastTick &&
      !danmakuLocalPosted.has(entry.id)
    ) {
      spawnDanmaku(entry)
    }
    danmakuSpawnIdx++
  }
  danmakuLastTick = t
  if (t > danmakuFetchedUntil - 60) void prefetchDanmaku()
}

watch(currentTime, (t) => danmakuTick(t))

async function sendDanmaku(): Promise<void> {
  const text = danmakuText.value.trim()
  if (!text || danmakuSending.value || !danmakuSiteOn.value) return
  danmakuSending.value = true
  try {
    const res = await postDanmaku(
      itemId.value,
      Math.max(0, Math.round(currentTime.value * 10) / 10),
      text,
    )
    danmakuText.value = ''
    danmakuLocalPosted.add(res.item.id)
    mergeDanmakuItems([res.item])
    if (danmaku.value.enabled && !softActive.value) spawnDanmaku(res.item)
  } catch (e) {
    showToast(errorText(e, '弹幕发送失败'))
  } finally {
    danmakuSending.value = false
  }
}
/** 当前完整设置对象（同步口径：danmaku / rate / subtitle / autoPlayNext 四个顶层键） */
function currentPlayerPrefs(): PlayerPrefs {
  return {
    danmaku: danmaku.value,
    rate: rate.value,
    subtitle: { fontSize: subtitleFontSize.value, delayMs: subtitleDelayMs.value },
    autoPlayNext: autoPlayNext.value,
  }
}
/** 任一设置变更后调：本地整存 + 防抖回推服务端（失败静默、本地为准）。 */
function persistPlayerPrefs(): void {
  const prefs = currentPlayerPrefs()
  saveLocalPlayerPrefs(prefs)
  schedulePushPlayerPrefs(prefs)
}
function persistDanmaku(): void {
  persistPlayerPrefs()
}
/**
 * 挂载后异步同步：拉服务端设置、本地优先合并，只应用「本地原本没有、
 * 服务端补来的」顶层键——不覆盖用户在本机已有的选择。全程不阻塞起播，
 * 失败静默（sync 内部已吞）。
 */
async function applyRemotePlayerPrefs(): Promise<void> {
  const local = loadLocalPlayerPrefs()
  const merged = await syncPlayerPrefsFromServer()
  if (local.danmaku === undefined && merged.danmaku) {
    danmaku.value = withDanmakuDefaults(merged.danmaku)
  }
  if (local.rate === undefined && typeof merged.rate === 'number' && merged.rate > 0) {
    rate.value = merged.rate
    const soft = softHandle.value
    if (soft) soft.setRate(merged.rate)
    else if (videoEl.value) videoEl.value.playbackRate = merged.rate
  }
  if (local.subtitle === undefined && merged.subtitle) {
    subtitleFontSize.value = merged.subtitle.fontSize
    if (merged.subtitle.delayMs !== subtitleDelayMs.value) {
      subtitleDelayMs.value = merged.subtitle.delayMs
      void rebuildSubtitleTracks()
    }
  }
  if (local.autoPlayNext === undefined && merged.autoPlayNext !== undefined) {
    autoPlayNext.value = merged.autoPlayNext !== false
  }
}
function onTogether(): void {
  togetherOpen.value = true
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
  persistPlayerPrefs()
}
function onSubtitleFontSize(fs: SubtitleFontSize): void {
  subtitleFontSize.value = fs
  persistPlayerPrefs()
}

// ---------- 设置面板动作 ----------

/** 播放信息叠显开关（差距 #17）：只管本页叠显层显隐，不进偏好持久化 */
function onInfoVisible(v: boolean): void {
  infoVisible.value = v
}
/** 自动连播开关（差距 #20）：随播放器偏好持久化 */
function onAutoPlayNext(v: boolean): void {
  autoPlayNext.value = v
  persistPlayerPrefs()
}
function onRate(r: number): void {
  rate.value = r
  persistPlayerPrefs()
  const soft = softHandle.value
  if (soft) {
    soft.setRate(r)
    return
  }
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
  let cached = verdictCache.value.get(source.Id)
  if (!cached) {
    cached = await probeDecode(source)
    verdictCache.value.set(source.Id, cached)
  }
  const at = currentTime.value
  const wasPlaying = phase.value === 'playing' && !isPaused.value
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
  // 解不了的新版本同样先过软解决策（软解/提示卡/原生试播三选一）
  await playSource(source, at)
  if (wasPlaying && !softActive.value) videoEl.value?.play().catch(() => {})
}

function onSelectEpisode(id: string): void {
  settingsOpen.value = false
  goEpisode(episodes.value.find((ep) => ep.Id === id))
}
function goEpisode(ep: EmbyItem | undefined): void {
  if (!ep) return
  sendStopped()
  // 选线是会话级选择、切集保留；字幕 Index 是每集自己的轨，切集丢弃回默认
  void router.push({
    name: 'web-player',
    params: { id: ep.Id },
    query: lineBase.value ? { line: lineBase.value } : {},
  })
}

function onScreenshot(): void {
  if (softActive.value) {
    showToast('软解模式暂不支持截图')
    return
  }
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

// 小窗（画中画）：只用标准 requestPictureInPicture；浏览器不支持时
// 设置弹层根本不渲染这一行（pipSupported 为假），不留假钮。
// Safari 的 webkitSetPresentationMode 非标准口径，不在本单范围内。
const pipSupported =
  typeof document !== 'undefined' &&
  'pictureInPictureEnabled' in document &&
  document.pictureInPictureEnabled &&
  typeof HTMLVideoElement !== 'undefined' &&
  'requestPictureInPicture' in HTMLVideoElement.prototype

async function onPip(): Promise<void> {
  if (softActive.value) {
    showToast('软解模式暂不支持小窗')
    return
  }
  const v = videoEl.value
  if (!v || !pipSupported) return
  try {
    if (document.pictureInPictureElement) {
      await document.exitPictureInPicture()
    } else {
      await v.requestPictureInPicture()
    }
    settingsOpen.value = false
  } catch {
    showToast('小窗没打开，可能被浏览器拦了')
  }
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
    wtBroadcast('seek')
  } else if (e.key === 'ArrowLeft') {
    v.currentTime = Math.max(0, v.currentTime - 10)
    wtBroadcast('seek')
  }
}
function onVisibility(): void {
  if (document.hidden) sendProgress(isPaused.value)
}

watch(
  () => route.params.id,
  (id) => {
    if (id) {
      if (wtInRoom.value) {
        wt.leave()
        showToast('已切换条目，退出了一起看')
      }
      reportedPlayingFor = ''
      window.clearInterval(progressTimer)
      // 切集：软解实例先停掉（新集按自己的版本重新决策）
      void destroySoft()
      // 切集：字幕选择是每集自己的，复位后按新集的 query/默认轨重新定
      primarySub.value = null
      secondarySub.value = null
      void loadItem(String(id)).then(() => applyRouteSub())
    }
  },
)

onMounted(() => {
  document.body.style.overflow = 'hidden'
  document.addEventListener('keydown', onKeydown)
  document.addEventListener('visibilitychange', onVisibility)
  // 播放器设置后端同步（⚠️11）：异步拉服务端设置补本地缺的键，不阻塞起播
  void applyRemotePlayerPrefs()
  // 字幕预选：弹层带了 sub 就按它，没带跟服务端 DefaultSubtitleStreamIndex（在 applyRouteSub 内兜底）
  void loadItem(itemId.value).then(() => {
    applyRouteSub()
    // 邀请链接带 ?wt=房间码 打开时自动入房（服务端逐人过播放门禁）
    if (wtJoinCode.value) wt.joinRoom(wtJoinCode.value)
  })
  // 房主锚点：每 10 秒把当前进度/播放态对齐给全房，成员据此纠偏
  wtStateTimer = window.setInterval(() => {
    const v = videoEl.value
    if (wtInRoom.value && wtIsHost.value && phase.value === 'playing' && v) {
      wt.sendState(v.currentTime || 0, !v.paused, v.playbackRate || 1)
    }
  }, 10000)
})
onUnmounted(() => {
  document.body.style.overflow = ''
  document.removeEventListener('keydown', onKeydown)
  document.removeEventListener('visibilitychange', onVisibility)
  window.clearTimeout(controlsTimer)
  window.clearTimeout(probeTimer)
  window.clearTimeout(probeTimer2)
  window.clearInterval(progressTimer)
  window.clearInterval(wtStateTimer)
  resetDanmakuData()
  wt.dispose()
  void destroySoft()
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
/* 软解画布（AVPlayer 自建 canvas）：与原生 video 同框同层，canvas
   撑满容器、按 contain 等比，不拉伸变形 */
.wp-soft {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  background: #000;
}
.wp-soft canvas {
  width: 100% !important;
  height: 100% !important;
  object-fit: contain;
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
/* 返回钮（差距 #1/#2）：手机＝文字胶囊——高 40、圆角 20、与 .wp-together
   同款玻璃（白 14% 边框＋rgba(18,18,20,.55)＋blur 14px）、白字 14px；
   ≥768px 变 Ø40 玻璃圆钮、文字隐藏，顶栏不再另置标题 */
.wp-back {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 40px;
  max-width: min(52vw, 320px);
  padding: 0 16px 0 10px;
  border-radius: 20px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(18, 18, 20, 0.55);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  color: #fff;
  font-size: 14px;
  cursor: pointer;
}
.wp-back-icon {
  flex: none;
}
.wp-back-text {
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
.wp-together.on {
  border-color: rgba(125, 211, 252, 0.55);
}
.wp-together-count {
  font-size: 12px;
  color: #7dd3fc;
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

/* 播放信息叠显（差距 #17）：键值小字叠在画面左上（顶栏下方），不挡操作 */
.wp-info-overlay {
  position: absolute;
  top: calc(64px + env(safe-area-inset-top));
  left: 14px;
  z-index: 18;
  margin: 0;
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(10, 10, 12, 0.55);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  font-size: 12px;
  line-height: 1.55;
  pointer-events: none;
  max-width: min(78vw, 340px);
}
.wp-info-line {
  display: flex;
  gap: 10px;
}
.wp-info-line dt {
  flex: none;
  color: rgba(255, 255, 255, 0.5);
}
.wp-info-line dd {
  margin: 0;
  color: #fff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
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

/* 站内弹幕层：覆盖画面上部（area=half 时只占上半屏），不挡点击 */
.wp-dm-layer {
  position: absolute;
  left: 0;
  right: 0;
  top: 0;
  height: 100%;
  z-index: 8;
  overflow: hidden;
  pointer-events: none;
}
.wp-dm-layer.half {
  height: 50%;
}
.wp-dm-item {
  position: absolute;
  left: 0;
  white-space: nowrap;
  color: #fff;
  font-weight: 600;
  line-height: 1.3;
  text-shadow:
    0 1px 2px rgba(0, 0, 0, 0.9),
    1px 0 2px rgba(0, 0, 0, 0.9),
    -1px 0 2px rgba(0, 0, 0, 0.9),
    0 -1px 2px rgba(0, 0, 0, 0.9);
  animation-name: wp-dm-fly;
  animation-timing-function: linear;
  animation-fill-mode: forwards;
  will-change: left, transform;
}
@keyframes wp-dm-fly {
  from {
    left: 100%;
    transform: translateX(0);
  }
  to {
    left: 0;
    transform: translateX(-100%);
  }
}

/* 弹幕发送行：贴在控制条上方居中，形态与控制条同族（深色玻璃） */
.wp-dm-send {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  bottom: calc(128px + env(safe-area-inset-bottom));
  z-index: 20;
  width: min(680px, calc(100% - 24px));
  display: flex;
  gap: 8px;
  background: rgba(22, 22, 25, 0.78);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 999px;
  padding: 6px 6px 6px 16px;
  backdrop-filter: blur(22px);
  -webkit-backdrop-filter: blur(22px);
}
.wp-dm-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: #fff;
  font-size: 14px;
}
.wp-dm-input::placeholder {
  color: rgba(255, 255, 255, 0.4);
}
.wp-dm-send-btn {
  flex: none;
  border: none;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.92);
  color: #111;
  font-size: 13.5px;
  font-weight: 600;
  padding: 7px 16px;
  cursor: pointer;
}
.wp-dm-send-btn:disabled {
  opacity: 0.4;
  cursor: default;
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
.wp-notice .wp-notice-sub {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.45);
}
.wp-notice-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
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
  /* 桌面返回钮回归 Ø40 玻璃圆钮，胶囊文字隐藏（差距 #1/#2） */
  .wp-back {
    width: 40px;
    padding: 0;
    border-radius: 50%;
    justify-content: center;
  }
  .wp-back-text {
    display: none;
  }
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
