// 失败后迟到恢复（第九轮 late-recovery）：看门狗 30 秒判失败后，
// 错误页不再是终态——失败后继续轻量观察 currentTime，确认「持续
// 前进」即自动清除错误页回播放态。
//
// 背景（第八轮取证复测，testplay1133）：冷跳到 55:56 后 UI 落错误
// 页、看门狗冻结在 failed advanced=0.00s，但底层 video 元素在
// 180 秒内以约 1 倍速从 3356.38s 走到 3538.14s，readyState 1→4，
// 已缓冲越过目标——元素已活、UI 判死。seek 本身只是慢（约 2 分
// 钟才恢复），不是死了。
//
// 模型（假时钟，不触 DOM、不拉流）：LateRecoveryWatcher 只认
// SeekProgressTracker 的 sustained 结论（累计 ≥0.5s、两次相隔
// ≥1s 的采样都看到、第二次读数大于第一次），与第四轮同一口径。
// 运行：cd user_frontend && node tests/lateRecovery.test.mjs
import { register } from 'node:module'
import assert from 'node:assert'

register('./lateRecovery.hooks.mjs', import.meta.url)

const { LateRecoveryWatcher } = await import('../src/utils/lateRecovery.ts')
const { SeekProgressTracker } = await import('../src/utils/softDecode.ts')

function makeWatcher() {
  let nowMs = 0
  const watcher = new LateRecoveryWatcher({ now: () => nowMs })
  return {
    watcher,
    setNow(ms) {
      nowMs = ms
    },
  }
}

let passed = 0
function check(name, fn) {
  fn()
  passed += 1
  console.log(`PASS ${name}`)
}

check('失败后时间持续前进即恢复', () => {
  const { watcher, setNow } = makeWatcher()
  assert.equal(watcher.begin(3356.38, 3356.38), true)
  assert.equal(watcher.observing, true)
  setNow(0)
  assert.equal(watcher.note(3356.38, true, null), false)
  setNow(500)
  assert.equal(watcher.note(3356.88, true, null), false)
  setNow(1500)
  assert.equal(watcher.note(3357.88, true, null), true)
  assert.equal(watcher.observing, false)
  assert.match(watcher.recoveredStateString(), /^recovered-after-failed target=3356.38s/)
})

check('一次播放会话最多恢复 1 次', () => {
  const { watcher, setNow } = makeWatcher()
  assert.equal(watcher.begin(100, 100), true)
  setNow(0)
  watcher.note(100, true, null)
  setNow(500)
  watcher.note(100.5, true, null)
  setNow(1500)
  assert.equal(watcher.note(101.5, true, null), true)
  // 第二次失败：不再观察，错误页终态停住
  assert.equal(watcher.begin(200, 200), false)
  assert.equal(watcher.observing, false)
  setNow(2500)
  assert.equal(watcher.note(201.5, true, null), false)
  // 新源重置后额度恢复
  watcher.reset()
  assert.equal(watcher.begin(200, 200), true)
  assert.equal(watcher.observing, true)
})

check('时间不动不恢复', () => {
  const { watcher, setNow } = makeWatcher()
  assert.equal(watcher.begin(100, 100), true)
  for (let t = 0; t <= 10000; t += 500) {
    setNow(t)
    assert.equal(watcher.note(100, true, null), false)
  }
  assert.equal(watcher.observing, true)
})

check('单次 tick 前进不算恢复', () => {
  const { watcher, setNow } = makeWatcher()
  assert.equal(watcher.begin(100, 100), true)
  setNow(0)
  assert.equal(watcher.note(100, true, null), false)
  setNow(500)
  assert.equal(watcher.note(100.6, true, null), false)
  // 0.6s 的一次 tick 后冻结：没有第二次相隔 ≥1s 的前进采样
  for (let t = 1000; t <= 6000; t += 500) {
    setNow(t)
    assert.equal(watcher.note(100.6, true, null), false)
  }
  // 累计不足 0.5s 的前进也不算
  const { watcher: w2, setNow: setNow2 } = makeWatcher()
  assert.equal(w2.begin(100, 100), true)
  setNow2(0)
  assert.equal(w2.note(100, true, null), false)
  setNow2(500)
  assert.equal(w2.note(100.4, true, null), false)
  setNow2(2000)
  assert.equal(w2.note(100.4, true, null), false)
  assert.equal(w2.observing, true)
})

check('暂停不算恢复，恢复播放后继续观察', () => {
  const { watcher, setNow } = makeWatcher()
  assert.equal(watcher.begin(200, 200), true)
  setNow(0)
  assert.equal(watcher.note(200, false, 0), false)
  setNow(6000)
  assert.equal(watcher.note(200, false, 0), false)
  setNow(6500)
  assert.equal(watcher.note(200.6, true, null), false)
  setNow(7500)
  assert.equal(watcher.note(201.6, true, null), true)
})

check('第二次失败可重新观察（未用掉恢复额度时）', () => {
  const { watcher, setNow } = makeWatcher()
  assert.equal(watcher.begin(100, 100), true)
  setNow(0)
  watcher.note(100, true, null)
  // 观察中又判一次失败：以新失败为准重新钉基线
  assert.equal(watcher.begin(200, 200), true)
  setNow(500)
  assert.equal(watcher.note(200, true, null), false)
  setNow(1000)
  assert.equal(watcher.note(200.5, true, null), false)
  setNow(2000)
  assert.equal(watcher.note(201.5, true, null), true)
  assert.match(watcher.recoveredStateString(), /target=200\.00s/)
})

check('tracker: beginLateRecovery 把确认基线钉在失败位置', () => {
  const tracker = new SeekProgressTracker()
  tracker.beginLateRecovery(100, 0)
  assert.equal(tracker.note({ seconds: 100, playing: true, pausedAt: null }, true, 0), null)
  assert.equal(tracker.note({ seconds: 100.4, playing: true, pausedAt: null }, true, 500), null)
  const verdict = tracker.note({ seconds: 101, playing: true, pausedAt: null }, true, 1500)
  assert.equal(verdict?.kind, 'complete')
  assert.equal(verdict?.reason, 'sustained')
  assert.equal(verdict?.advancedSeconds, 1)
})

console.log(`${passed} passed`)
