/**
 * 原生 <video> seek 看门狗驱动（2026-11-25 第五轮）。
 *
 * 来历：前四轮修的全是软解（libmedia）路径的看门狗，但《黑洞
 * 表面》1080p 这类条目在 emby_media_streams 里没有探测行 →
 * PlaybackInfo 的 MediaStreams 为空 → 解码探测结论是 'unknown'
 * （不是 'unsupported'）→ playSource 只在 'unsupported' 时走
 * 软解，'unknown' 落入原生 <video> 播放。原生路径此前没有任何
 * seek 防护：onSeekCommit 把 currentTime 一设、显示值乐观地先
 * 跳到目标，冷段卡住时 timeupdate 不再来、seeked 不来、error
 * 也不来，进度永远停在乐观值，无提示、无重试、无错误页
 * （06:49 生产复测原样：3:03 跳 46:03 后 3 分多钟纹丝不动）。
 *
 * 本驱动给原生路径装上与软解同一套看门狗：状态机复用
 * SoftSeekGuard、到达＋确认记账复用 SeekProgressTracker（只许
 * 一套，不另写）。事件由视图层从 <video> 模板转发进来
 * （seeking/seeked/timeupdate/play/pause），另有 500ms 轮询兜
 * 底读 currentTime——冷段卡住时事件全停，靠轮询喂采样判死。
 * 判失败时把 video 的真实 currentTime 一并回调，视图据此把显
 * 示时间从乐观值 snap 回真实值，并上带「再试一次」的错误页。
 */
import {
  SoftSeekGuard,
  SeekProgressTracker,
  seekGuardStateString,
  SOFT_SEEK_TIMEOUT_MS,
  SOFT_SEEK_RESUME_MS,
  type SeekGuardTransition,
  type SoftSeekPhase,
} from './softDecode'

/** 驱动只依赖 video 的这两个属性（测试用假元素同形态） */
export interface NativeSeekVideoLike {
  currentTime: number
  readonly paused: boolean
}

export interface NativeSeekHooks {
  /** seek 在飞状态变化：true = 显示「正在定位」，false = 已落定/已判失败/已取消 */
  onSeekingChange?: (seeking: boolean) => void
  /**
   * 看门狗判失败（自动重试后仍无结果）：actualSeconds 是判失败
   * 时刻 video 的真实 currentTime——视图把显示时间 snap 回它
   * （去掉提交时的乐观值），再上错误页。
   */
  onSeekFailed?: (message: string, actualSeconds: number) => void
  /** 看门狗状态变迁上报（DOM 可观测，格式见 seekGuardStateString） */
  onGuardState?: (state: string) => void
}

export interface NativeSeekOptions {
  /** seek 看门狗单次超时（毫秒），缺省 SOFT_SEEK_TIMEOUT_MS */
  seekTimeoutMs?: number
  /** 到达后确认恢复的窗口（毫秒），缺省 SOFT_SEEK_RESUME_MS */
  seekResumeMs?: number
  /** 确认恢复要求时钟持续前进的跨度（毫秒），缺省 SOFT_SEEK_RESUME_SUSTAIN_MS */
  seekResumeSustainMs?: number
  /** 在飞期间轮询 currentTime 的间隔（毫秒），缺省 500 */
  seekPollMs?: number
  /** 时钟注入（测试用），缺省 Date.now */
  now?: () => number
  /** 计时器注入（测试用），缺省全局 setTimeout/clearTimeout */
  setTimer?: (fn: () => void, ms: number) => unknown
  clearTimer?: (handle: unknown) => void
  /** 轮询计时器注入（测试用），缺省全局 setInterval/clearInterval */
  setIntervalFn?: (fn: () => void, ms: number) => unknown
  clearIntervalFn?: (handle: unknown) => void
}

export interface NativeSeekDriver {
  /** 发起一次受看门狗管辖的 seek（进度条提交时调用） */
  seekTo(targetSeconds: number): void
  /** <video> seeking 事件转发 */
  notifySeeking(): void
  /** <video> seeked 事件转发 */
  notifySeeked(): void
  /** <video> timeupdate 事件转发 */
  notifyTimeUpdate(): void
  /** <video> play/playing 事件转发 */
  notifyPlay(): void
  /** <video> pause 事件转发 */
  notifyPause(): void
  /** 取消在飞 seek（切源/切管线时）：撤防且不再有任何回调 */
  cancel(): void
  /** 实例销毁：撤防、停轮询，此后不再有任何回调 */
  dispose(): void
}

export function createNativeSeekDriver(
  video: NativeSeekVideoLike,
  hooks: NativeSeekHooks,
  options: NativeSeekOptions = {},
): NativeSeekDriver {
  const now = options.now ?? (() => Date.now())
  const tracker = new SeekProgressTracker({
    resumeMs: options.seekResumeMs,
    sustainMs: options.seekResumeSustainMs,
  })
  let retriesUsed = 0
  let seekingNotified = false
  let pollTimer: unknown = null
  let disposed = false
  // 暂停开始时刻（与 now 同一时钟）：正在播为 null。驱动建成时
  // 若已暂停，按「早就停住了」记 0（≤ 任何下发时刻 → 落定）。
  let pausedAt: number | null = video.paused ? 0 : null

  const guard = new SoftSeekGuard({
    timeoutMs: options.seekTimeoutMs ?? SOFT_SEEK_TIMEOUT_MS,
    confirmTimeoutMs: options.seekResumeMs ?? SOFT_SEEK_RESUME_MS,
    ...(options.setTimer ? { setTimer: options.setTimer } : {}),
    ...(options.clearTimer ? { clearTimer: options.clearTimer } : {}),
    onRetry: (attempt) => {
      retriesUsed = attempt
      // 重试 = 同一目标再下发一次（原生以最新定位为准）
      issueSeek(tracker.target)
      reportGuardState('seeking')
    },
    onTimeout: (phase: SoftSeekPhase) => {
      setSeekingNotified(false)
      const target = tracker.target
      const message =
        phase === 'confirming'
          ? `seek stalled after arrival: time frozen at ${target}s after retry`
          : `seek timeout: no seeked/progress toward ${target}s after retry`
      // 判失败必须留痕：原始原因进控制台，视图翻成人话上错误页
      console.error('[nativeSeek] seek 看门狗判失败:', message)
      reportGuardState('failed')
      hooks.onSeekFailed?.(message, readTime())
    },
  })

  function readTime(): number {
    const t = Number(video.currentTime)
    return Number.isFinite(t) ? t : 0
  }

  function reportGuardState(transition: SeekGuardTransition): void {
    hooks.onGuardState?.(
      seekGuardStateString(
        transition,
        tracker.target,
        tracker.advancedSeconds,
        retriesUsed,
      ),
    )
  }

  function setSeekingNotified(v: boolean): void {
    if (seekingNotified === v) return
    seekingNotified = v
    if (v) startPoll()
    else stopPoll()
    hooks.onSeekingChange?.(v)
  }

  function startPoll(): void {
    if (pollTimer !== null) return
    const setIntervalFn =
      options.setIntervalFn ??
      ((fn: () => void, ms: number) => setInterval(fn, ms))
    pollTimer = setIntervalFn(() => {
      if (!guard.pending) {
        setSeekingNotified(false)
        return
      }
      noteProgress()
    }, options.seekPollMs ?? 500)
  }

  function stopPoll(): void {
    if (pollTimer === null) return
    const clearIntervalFn =
      options.clearIntervalFn ??
      ((h: unknown) => clearInterval(h as ReturnType<typeof setInterval>))
    clearIntervalFn(pollTimer)
    pollTimer = null
  }

  function issueSeek(target: number): void {
    tracker.issued(now())
    try {
      video.currentTime = target
    } catch {
      // 个别流不支持定位会抛：交给看门狗立刻走重试/判失败
      guard.fail()
    }
  }

  function noteProgress(): void {
    if (disposed || !guard.pending) return
    // pause 事件万一漏接：采样时发现已暂停但没记时刻，按当下补
    // 记（落定窗口从补记起算，保守不误杀）
    if (video.paused && pausedAt === null) pausedAt = now()
    const verdict = tracker.note(
      { seconds: readTime(), playing: !video.paused, pausedAt },
      guard.confirming,
      now(),
    )
    if (!verdict) return
    if (verdict.kind === 'arrive') {
      guard.arrive()
      reportGuardState('confirming')
      return
    }
    guard.complete()
    setSeekingNotified(false)
    reportGuardState('complete')
  }

  return {
    seekTo(targetSeconds: number): void {
      if (disposed) return
      const target = Math.max(0, targetSeconds)
      tracker.begin(target)
      retriesUsed = 0
      guard.begin(target)
      setSeekingNotified(true)
      reportGuardState('seeking')
      issueSeek(target)
    },
    notifySeeking(): void {
      noteProgress()
    },
    notifySeeked(): void {
      noteProgress()
    },
    notifyTimeUpdate(): void {
      noteProgress()
    },
    notifyPlay(): void {
      pausedAt = null
      noteProgress()
    },
    notifyPause(): void {
      if (pausedAt === null) pausedAt = now()
      noteProgress()
    },
    cancel(): void {
      guard.dispose()
      setSeekingNotified(false)
    },
    dispose(): void {
      disposed = true
      guard.dispose()
      setSeekingNotified(false)
      stopPoll()
    },
  }
}
