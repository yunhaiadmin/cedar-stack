// 软解音频解锁回归测试（2026-11-26）：
// 运行：cd user_frontend && node tests/softDecodeAudioUnlock.test.mjs
// 背景：iPhone Safari 软解有画面没声音——libmedia 的 AudioContext
// 在起播异步链路里创建，落在用户手势窗口之外，iOS 给 suspended；
// libmedia 内部只在选流时抢救一次 resume（0.1 秒竞速），抢不到就
// 只剩日志 "the audioContext was not allowed to start. It must be
// resumed after a user gesture"。我方此前从未在手势里 resume。
// 被测的是 softDecode.ts 的纯判定与解锁编排（假 AVPlayer 类），
// 不触 DOM、不加载真实播放器。
import { register } from 'node:module'
import assert from 'node:assert'

register('./softDecode.hooks.mjs', import.meta.url)

const {
  audioContextNeedsUnlock,
  unlockAudioContextOf,
} = await import('../src/utils/softDecode.ts')

let passed = 0
function check(name, actual, expected) {
  assert.deepStrictEqual(actual, expected, name)
  passed += 1
  console.log(`PASS ${name}`)
}

// ---- 状态判定 ----
check('running 不需要解锁', audioContextNeedsUnlock('running'), false)
check('suspended 需要解锁', audioContextNeedsUnlock('suspended'), true)
check('interrupted 需要解锁', audioContextNeedsUnlock('interrupted'), true)
check('closed 不需要解锁', audioContextNeedsUnlock('closed'), false)
check('null 不需要解锁', audioContextNeedsUnlock(null), false)
check('undefined 不需要解锁', audioContextNeedsUnlock(undefined), false)

// ---- 解锁编排（假类） ----
function fakeClass(state, { resumeFails = false } = {}) {
  const calls = { start: 0, resume: 0 }
  const ctx = {
    state,
    async resume() {
      calls.resume += 1
      if (resumeFails) throw new Error('not allowed')
      ctx.state = 'running'
    },
  }
  const cls = {
    audioContext: ctx,
    async startAudioContext() {
      calls.start += 1
    },
  }
  return { cls, ctx, calls }
}

{
  const { cls, calls } = fakeClass('running')
  check('running：解锁后仍 running', await unlockAudioContextOf(cls), 'running')
  check('running：不调 resume', calls.resume, 0)
}
{
  const { cls, calls } = fakeClass('suspended')
  check('suspended：resume 后 running', await unlockAudioContextOf(cls), 'running')
  check('suspended：start 与 resume 各一次', [calls.start, calls.resume], [1, 1])
}
{
  const { cls } = fakeClass('interrupted', { resumeFails: true })
  check('resume 被拒不抛错、回真实状态', await unlockAudioContextOf(cls), 'interrupted')
}
check('类未加载：返回 null', await unlockAudioContextOf(null), null)
check('类无 startAudioContext：返回 null', await unlockAudioContextOf({}), null)

console.log(`\n${passed} passed`)
