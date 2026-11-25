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
 *
 * 第六轮（2026-11-25 复测）：静默冻结已消失，但冷跳仍不恢复——
 * 跳到 48:15 后「正在定位」立即出现，约 10 秒转错误页；服务端
 * 日志证明中继已吐出跳转区间字节（206，首字节约 1.7 秒），浏览
 * 器没能恢复。根因在到达判定：轮询在下发 0.5 秒后就凭 currentTime
 * 的乐观读数（设置后立即读回目标值）转入确认阶段，5 秒确认窗
 * 口从还没取到一个字节就开始数，5 秒一到重试（重发同目标
 * currentTime），再 5 秒判失败——合计约 11 秒，正是复测时序。
 * 修法见 noteProgress 的 seeking 门与 onRetry 的不重发：只认
 * seeked（定位真正完成），不认乐观读数；重试只等在途 seek，
 * 不重发定位。
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

/** 驱动只依赖 video 的这三个属性（测试用假元素同形态） */
export interface NativeSeekVideoLike {
  currentTime: number
  readonly paused: boolean
  /**
   * 浏览器是否正在执行 seek（HTMLVideoElement.seeking：设置
   * currentTime 后同步为 true，seeked 到后为 false）。第六轮：
   * currentTime 在 seek 下发后立刻读回目标值（乐观值），不能拿
   * 它当「已到达」——只有 seeking=false 时读数才是定位真正完成
   * 后的位置。
   */
  readonly seeking: boolean
}

export interface NativeSeekHooks {
  /** seek 在飞状态变化：true = 显示「正在定位」，false = 已落定/已判失败/已取消 */
  onSeekingChange?: (seeking: boolean) => void
  /**
   * 看门狗判失败（自动重试后仍无结果）：actualSeconds 是判失败
   * 时刻 video 的真实 currentTime——视图把显示时间 snap 回它
   * （去掉提交时的乐观值），再上错误页；targetSeconds 是失败那
   * 笔 seek 的目标（第九轮 late-recovery：失败后观察的基线与
   * 状态串用）。
   */
  onSeekFailed?: (message: string, actualSeconds: number, targetSeconds: number) => void
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
      // （第六轮）重试不重发 currentTime：原生 seek 下发后浏览器
      // 仍在取流/解码，同目标再设一次最多是空操作（Chrome 合并相
      // 同目标的 seek——第五轮复测里重试后服务端再无流请求，重发
      // 没拉到任何新字节），却可能打断在途的 demux/解码重启。
      // 重试 = 再给在途 seek 一个找目标窗口（看门狗自行重武装
      // 15 秒）：只刷新记账的下发时刻，不碰 video。
      tracker.issued(now())
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
      hooks.onSeekFailed?.(message, readTime(), tracker.target)
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
    // （第六轮）浏览器 seek 未完成时 currentTime 读回的是下发目
    // 标（乐观值），不是真正到达——此前轮询在下发 0.5 秒后就凭这
    // 个读数转入确认阶段，5 秒确认窗口从还没取到一个字节就开始
    // 数，5 秒一到重试，再 5 秒判失败（第五轮复测约 10 秒出错误
    // 页正是这条时序）。seeking 为真时不做任何记账判定，只等：
    // 要么 seeked 到（下一采样按真实位置记账），要么找目标 15 秒
    // 超时走重试/判失败。确认阶段里 seeking 一般已为 false，此
    // 门主要挡找目标阶段的乐观读数。
    if (video.seeking) return
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
