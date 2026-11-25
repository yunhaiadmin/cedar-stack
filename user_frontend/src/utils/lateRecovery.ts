// 失败后迟到恢复观察（第九轮 late-recovery，2026-11-25）。
//
// 背景（第八轮取证复测）：冷跳后 UI 落错误页、看门狗冻结在 failed，
// 但底层 <video> 元素在 180 秒内以约 1 倍速从 3356.38s 走到
// 3538.14s——元素已活、UI 判死。seek 本身只是慢（约 2 分钟才恢复），
// 不是死了；30 秒判失败后看门狗不再复核，错误页是终态。
//
// 本观察器让失败不再是终态：判失败后继续以 500ms 节奏（调用方复用
// 诊断区刷新，不另起轮询）喂 currentTime 采样；确认「持续前进」
// 即报告恢复，调用方清除错误页回播放态。确认口径复用
// SeekProgressTracker（只许一套，不另写）：累计 ≥0.5s、两次相隔
// sustainMs 的采样都看到、第二次读数大于第一次；暂停不算恢复。
// 一次播放会话（同一源）最多自动恢复 1 次，第二次失败即终态。
import { SeekProgressTracker } from './softDecode'

export interface LateRecoveryOptions {
  /** 时钟注入（测试用），缺省 Date.now */
  now?: () => number
}

export class LateRecoveryWatcher {
  private readonly tracker = new SeekProgressTracker()
  private readonly now: () => number
  private active = false
  private recoveredOnce = false
  private failedTarget = 0

  constructor(options: LateRecoveryOptions = {}) {
    this.now = options.now ?? (() => Date.now())
  }

  /** 是否正在失败后观察 */
  get observing(): boolean {
    return this.active
  }

  /** 本会话是否已用掉唯一一次自动恢复额度 */
  get hasRecoveredOnce(): boolean {
    return this.recoveredOnce
  }

  /** 新源（playSource）：清掉本会话的恢复额度与在飞观察 */
  reset(): void {
    this.active = false
    this.recoveredOnce = false
    this.failedTarget = 0
  }

  /**
   * 看门狗判失败时调用：开始失败后观察。targetSeconds 是失败那笔
   * seek 的目标（只进恢复后的状态串）；baselineSeconds 是判失败
   * 时刻 video 的真实位置（确认前进的累计基线）。已用掉本会话
   * 唯一一次恢复额度时返回 false（终态错误页，不再观察）。观察
   * 中又判一次失败时以新失败为准重新钉基线。
   */
  begin(targetSeconds: number, baselineSeconds: number): boolean {
    if (this.recoveredOnce) return false
    this.failedTarget = targetSeconds
    this.tracker.beginLateRecovery(baselineSeconds, this.now())
    this.active = true
    return true
  }

  /**
   * 喂一次采样（调用方复用诊断区 500ms 节奏）：返回 true = 确认
   * 持续前进，调用方清除错误页回播放态。只认 sustained 结论；
   * 暂停（时间不走是正常的）与单次 tick 都不算恢复，继续观察。
   */
  note(seconds: number, playing: boolean, pausedAt: number | null): boolean {
    if (!this.active) return false
    const verdict = this.tracker.note({ seconds, playing, pausedAt }, true, this.now())
    if (verdict !== null && verdict.kind === 'complete' && verdict.reason === 'sustained') {
      this.active = false
      this.recoveredOnce = true
      return true
    }
    return false
  }

  /** 恢复确认时写进看门狗状态串的备注（DOM data-seek-guard 留痕） */
  recoveredStateString(): string {
    return (
      `recovered-after-failed target=${this.failedTarget.toFixed(2)}s ` +
      `advanced=${this.tracker.advancedSeconds.toFixed(2)}s`
    )
  }
}
