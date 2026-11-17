/**
 * 网页播放器设置同步（⚠️11，2026-11-15）：拉取/合并/回推的唯一实现，
 * 不许散落到各播放器组件里。
 *
 * - 本地存：localStorage `aetrix-player-prefs`（整个设置对象）；
 *   弹幕子对象同时镜像写旧键 `aetrix-player-danmaku`（⚠️11 之前的
 *   唯一本地键，读时也从它补，存量用户不丢设置）。
 * - 服务端存：GET/PUT /api/user/player-preferences（web_users 列，
 *   整对象覆盖、服务端不解释结构）。preferences=null 表示从未存过。
 * - 合并口径＝本地优先（顶层键粒度）：本地已有的键用本地值，本地
 *   没有的键用服务端补——新设备第一次打开能拿到老设备的设置。
 * - 同步不阻塞起播：进播放器时异步拉，失败/超时（5 秒）静默用本地；
 *   任一设置变更后由视图调 schedulePushPlayerPrefs 防抖回推，
 *   失败静默（本地已存为准，下次变更再推完整对象）。
 *
 * 同步范围：danmaku（弹幕显示 7 项）、rate（倍速）、subtitle
 * （字号/延迟）、autoPlayNext（自动连播开关，缺省＝开）。主/副字幕
 * 选轨是「这部片选哪条轨」的内容选择、不是个人偏好，不进同步对象。
 */
import api from '@/api'
import type { DanmakuSettings } from '@/components/player/PlayerDanmakuSheet.vue'
import type { SubtitleFontSize } from '@/components/player/PlayerSubtitleSheet.vue'

export interface PlayerSubtitlePrefs {
  fontSize: SubtitleFontSize
  delayMs: number
}

export interface PlayerPrefs {
  danmaku?: DanmakuSettings
  rate?: number
  subtitle?: PlayerSubtitlePrefs
  /** 自动连播：播完自动接下一集。undefined＝从未设置，按开处理 */
  autoPlayNext?: boolean
}

export const PLAYER_PREFS_LOCAL_KEY = 'aetrix-player-prefs'
export const LEGACY_DANMAKU_LOCAL_KEY = 'aetrix-player-danmaku'

const PUSH_DEBOUNCE_MS = 800
const FETCH_TIMEOUT_MS = 5000
const PREFS_URL = '/api/user/player-preferences'

// ---------------- 本地存取 ----------------

function parsePrefs(raw: string | null): PlayerPrefs | null {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as unknown
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as PlayerPrefs
    }
  } catch {
    /* 坏数据当没存过 */
  }
  return null
}

/** 读本地完整设置对象；danmaku 缺失时从旧键补（存量兼容）。 */
export function loadLocalPlayerPrefs(): PlayerPrefs {
  let prefs: PlayerPrefs = {}
  try {
    prefs = parsePrefs(localStorage.getItem(PLAYER_PREFS_LOCAL_KEY)) ?? {}
    if (prefs.danmaku === undefined) {
      const legacy = parsePrefs(localStorage.getItem(LEGACY_DANMAKU_LOCAL_KEY))
      if (legacy) prefs = { ...prefs, danmaku: legacy as DanmakuSettings }
    }
  } catch {
    /* localStorage 不可用（隐私模式等）时按空 */
  }
  return prefs
}

/** 写本地完整设置对象；danmaku 在场时同步镜像旧键。 */
export function saveLocalPlayerPrefs(prefs: PlayerPrefs): void {
  try {
    localStorage.setItem(PLAYER_PREFS_LOCAL_KEY, JSON.stringify(prefs))
    if (prefs.danmaku !== undefined) {
      localStorage.setItem(LEGACY_DANMAKU_LOCAL_KEY, JSON.stringify(prefs.danmaku))
    }
  } catch {
    /* 存不下就算了 */
  }
}

// ---------------- 合并（本地优先） ----------------

/** 顶层键粒度本地优先：本地有的键用本地，没有的用服务端补。 */
export function mergePlayerPrefs(local: PlayerPrefs, remote: PlayerPrefs): PlayerPrefs {
  const merged: PlayerPrefs = { ...remote }
  if (local.danmaku !== undefined) merged.danmaku = local.danmaku
  if (local.rate !== undefined) merged.rate = local.rate
  if (local.subtitle !== undefined) merged.subtitle = local.subtitle
  if (local.autoPlayNext !== undefined) merged.autoPlayNext = local.autoPlayNext
  return merged
}

// ---------------- 服务端存取 ----------------

interface PlayerPrefsResponse {
  preferences: PlayerPrefs | null
}

/** 拉服务端设置；从未存过回 null。失败/超时直接抛，由 sync 吞。 */
export async function fetchServerPlayerPrefs(): Promise<PlayerPrefs | null> {
  const res = await api.get<never, PlayerPrefsResponse>(PREFS_URL, {
    timeout: FETCH_TIMEOUT_MS,
  })
  return res?.preferences ?? null
}

/** 整对象覆盖回推服务端。 */
export async function pushServerPlayerPrefs(prefs: PlayerPrefs): Promise<void> {
  await api.put<never, PlayerPrefsResponse>(PREFS_URL, prefs)
}

/**
 * 进播放器时的一次同步：拉服务端 → 本地优先合并 → 合并结果与本地
 * 不同才写回本地 → 返回合并后的完整对象（视图据此应用「服务端补
 * 来的」顶层键）。拉取失败/超时静默，返回本地对象。
 */
export async function syncPlayerPrefsFromServer(): Promise<PlayerPrefs> {
  const local = loadLocalPlayerPrefs()
  let remote: PlayerPrefs | null
  try {
    remote = await fetchServerPlayerPrefs()
  } catch {
    return local
  }
  if (!remote) return local
  const merged = mergePlayerPrefs(local, remote)
  if (JSON.stringify(merged) !== JSON.stringify(local)) {
    saveLocalPlayerPrefs(merged)
  }
  return merged
}

// ---------------- 防抖回推 ----------------

let pushTimer: number | undefined
let pendingPrefs: PlayerPrefs | null = null

/** 设置变更后调：800ms 内合并成一次 PUT，失败静默（本地为准）。 */
export function schedulePushPlayerPrefs(prefs: PlayerPrefs): void {
  pendingPrefs = prefs
  if (pushTimer !== undefined) window.clearTimeout(pushTimer)
  pushTimer = window.setTimeout(() => {
    pushTimer = undefined
    const toPush = pendingPrefs
    pendingPrefs = null
    if (toPush) void pushServerPlayerPrefs(toPush).catch((): void => undefined)
  }, PUSH_DEBOUNCE_MS)
}
