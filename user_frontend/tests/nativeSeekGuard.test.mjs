// 原生 <video> seek 看门狗（第五轮）：给原生路径装上与软解同一套
// 看门狗（SoftSeekGuard 状态机 + SeekProgressTracker 记账，见
// utils/nativeSeek.ts 与 utils/softDecode.ts）。
//
// 背景：前四轮修的全是软解看门狗，但无探测流信息的条目（MediaStreams
// 为空）解码探测结论是 'unknown'、不走软解，落入原生 <video>；原生
// onSeekCommit 把 currentTime 一设、显示值乐观地先跳到目标就不管
// 了——冷段卡住时 seeked/timeupdate/error 全不来，进度永远停在乐
// 观值，无提示、无重试、无错误页（2026-11-25 06:49 生产复测原样：
// 3:03 跳 46:03 后 3 分多钟纹丝不动）。
//
// 模型（假 video 元素 + 假时钟，不触 DOM、不拉流）：
//   落点冻结：seek 后元素位置跳到目标、但 seeked/timeupdate 永不
//     来（冷段断粮）→ 轮询喂冻结采样，必须重试一次后判失败；
//   位置不动：seek 后元素位置根本没动（定位没生效）→ 找目标阶段
//     超时、重试一次后判失败，且判失败回调带回真实位置——视图据
//     此把显示时间从乐观值 snap 回真实值（本模型里模拟这一步）；
//   健康 seek（对照组）：seeked + timeupdate 持续推进 → 落定，
//     不重试、不判失败；
//   暂停态 seek（对照组）：暂停中 seek 到达即按暂停落定，不判失败。
// 运行：cd user_frontend && node tests/nativeSeekGuard.test.mjs
// 计时全走假时钟（驱动的 now/setTimer/setInterval 都可注入），
// 单文件瞬时跑完，不依赖真实时间。
import { register } from 'node:module'
import assert from 'node:assert'

register('./nativeSeekGuard.hooks.mjs', import.meta.url)

const { createNativeSeekDriver } = await import('../src/utils/nativeSeek.ts')

let passed = 0
function check(name, actual, expected) {
  assert.deepStrictEqual(actual, expected, name)
  passed += 1
  console.log('PASS ' + name)
}

// ---- 假时钟：一次性计时器与轮询间隔都排在虚拟时间上，advance 按序触发 ----
function makeFakeClock() {
  let now = 0
  let nextId = 1
  const timers = new Map()
  const schedule = (fn, ms, intervalMs) => {
    const id = nextId++
    timers.set(id, { fn, fireAt: now + ms, intervalMs })
    return id
  }
  return {
    now: () => now,
    setTimer: (fn, ms) => schedule(fn, ms, null),
    clearTimer: (id) => {
      timers.delete(id)
    },
    setIntervalFn: (fn, ms) => schedule(fn, ms, ms),
    clearIntervalFn: (id) => {
      timers.delete(id)
    },
    advance(ms) {
      const end = now + ms
      for (;;) {
        let dueId = null
        let due = null
        for (const [id, t] of timers) {
          if (t.fireAt > end) continue
          if (!due || t.fireAt < due.fireAt || (t.fireAt === due.fireAt && id < dueId)) {
            due = t
            dueId = id
          }
        }
        if (!due) break
        now = due.fireAt
        if (due.intervalMs === null) timers.delete(dueId)
        else due.fireAt = now + due.intervalMs
        due.fn()
      }
      now = end
    },
  }
}

// ---- 假 video：currentTime 可控；jumpOnSeek 建模「设值即落点」与「设值不动」两种元素行为 ----
function makeFakeVideo({ startAt = 183, paused = false, jumpOnSeek = true } = {}) {
  let position = startAt
  const sets = []
  const video = {
    paused,
    seeking: false,
    sets,
    get currentTime() {
      return position
    },
    set currentTime(v) {
      sets.push(v)
      if (jumpOnSeek) position = v
      video.seeking = true
    },
    // 测试手动推进播放头（模拟正常播放出帧）：位置前进并发 timeupdate
    advance(driver, seconds) {
      position += seconds
      driver.notifyTimeUpdate()
    },
    // 测试补发 seeked（模拟元素明告定位完成）
    emitSeeked(driver) {
      video.seeking = false
      driver.notifySeeked()
    },
  }
  return video
}

function makeDriver(video, clock, hooksExtra = {}) {
  const calls = { seeking: [], failed: [], states: [] }
  const hooks = {
    onSeekingChange: (v) => calls.seeking.push(v),
    onSeekFailed: (m, actual) => calls.failed.push([m, actual]),
    onGuardState: (s) => calls.states.push(s),
    ...hooksExtra,
  }
  const driver = createNativeSeekDriver(video, hooks, {
    seekTimeoutMs: 15000,
    seekResumeMs: 5000,
    seekResumeSustainMs: 1000,
    seekPollMs: 500,
    now: clock.now,
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
    setIntervalFn: clock.setIntervalFn,
    clearIntervalFn: clock.clearIntervalFn,
  })
  return { driver, calls }
}

// --- 落点冻结：位置跳到 46:03、seeked/timeupdate 永不来（06:49 生产原样） ---
{
  const clock = makeFakeClock()
  const video = makeFakeVideo({ startAt: 183 })
  const { driver, calls } = makeDriver(video, clock)
  driver.seekTo(2763)
  clock.advance(500) // 轮询首采样：位置已到目标 → 转入确认阶段
  clock.advance(5000) // 确认窗口内时钟零前进 → 重试一次
  clock.advance(500) // 重试后轮询再采样：又到达 → 再入确认阶段
  clock.advance(5000) // 仍零前进 → 判失败
  check('frozen at target: retried once then failed', [video.sets.length, calls.failed.length], [2, 1])
  check('frozen at target: seeking pill visible until failure', calls.seeking, [true, false])
  check(
    'frozen at target: guard states walk begin/confirm/retry/confirm/fail',
    calls.states,
    [
      'seeking target=2763.00s advanced=0.00s retries=0',
      'confirming target=2763.00s advanced=0.00s retries=0',
      'seeking target=2763.00s advanced=0.00s retries=1',
      'confirming target=2763.00s advanced=0.00s retries=1',
      'failed target=2763.00s advanced=0.00s retries=1',
    ],
  )
  check('frozen at target: failure carries element position', calls.failed[0][1], 2763)
  driver.dispose()
}

// --- 位置不动：设值后元素位置根本没动（定位没生效）→ 显示时间 snap 回真实值 ---
{
  const clock = makeFakeClock()
  const video = makeFakeVideo({ startAt: 183, jumpOnSeek: false })
  // 模拟视图：提交时显示时间先乐观跳到目标，判失败时按回调的真实值回正
  const view = { display: 0 }
  const { driver, calls } = makeDriver(video, clock, {
    onSeekFailed: (m, actual) => {
      calls.failed.push([m, actual])
      view.display = actual
    },
  })
  view.display = 2763 // onSeekCommit 的乐观显示值
  driver.seekTo(2763)
  clock.advance(31000) // 找目标 15s 超时 → 重试 → 再 15s → 判失败
  check('no movement: retried once then failed', [video.sets.length, calls.failed.length], [2, 1])
  check('no movement: never arrived (no confirming state)', calls.states.some((s) => s.startsWith('confirming')), false)
  check('no movement: display snaps back to real position', view.display, 183)
  check('no movement: seeking pill visible until failure', calls.seeking, [true, false])
  driver.dispose()
}

// --- 健康 seek（对照组）：seeked + timeupdate 持续推进 → 落定 ---
{
  const clock = makeFakeClock()
  const video = makeFakeVideo({ startAt: 183 })
  const { driver, calls } = makeDriver(video, clock)
  driver.seekTo(2763)
  clock.advance(500) // 轮询采样到达 → 确认阶段
  video.emitSeeked(driver)
  video.advance(driver, 0.6) // 缓冲头先走 0.6 秒（单次 tick 不算恢复）
  clock.advance(1100) // 跨过持续前进要求的 1 秒跨度
  video.advance(driver, 0.6) // 第二次采样仍在前进 → 落定
  clock.advance(20000) // 落定后不应再有任何重试/判失败
  check('healthy seek: completes without failure or retry', [calls.failed.length, video.sets.length], [0, 1])
  check('healthy seek: seeking pill clears', calls.seeking, [true, false])
  check(
    'healthy seek: final guard state is complete with advance',
    calls.states[calls.states.length - 1],
    'complete target=2763.00s advanced=1.20s retries=0',
  )
  driver.dispose()
}

// --- 暂停态 seek（对照组）：暂停中 seek 到达即按暂停落定 ---
{
  const clock = makeFakeClock()
  const video = makeFakeVideo({ startAt: 183, paused: true })
  const { driver, calls } = makeDriver(video, clock)
  driver.seekTo(2763)
  clock.advance(500) // 轮询采样：已到达且暂停早于下发时刻 → 落定
  clock.advance(40000) // 落定后不应再有任何重试/判失败
  check('paused seek: settles on arrival, no failure or retry', [calls.failed.length, video.sets.length], [0, 1])
  check('paused seek: seeking pill clears', calls.seeking, [true, false])
  driver.dispose()
}

console.log(passed + ' passed')
