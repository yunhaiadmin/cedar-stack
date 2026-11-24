// seek 看门狗第二轮：到达后必须确认时间真的在往前走（2026-11-25 复测事故）。
// 第一轮 #512 的看门狗只看「位置到了没有」：复测里 seek 把定位器推到
// 目标 56:59、时间却永远停在那里，轮询第一次就判到达撤防，「正在定位」
// 一闪而过、重试与错误页永不触发。修法：到达后转入「确认恢复」阶段，
// 播放态下时间必须在窗口内再前进才算落定，否则按卡住走重试→判失败。
//
// 模型（假播放器 fakeAvPlayer.mjs，不触 DOM、不拉流）：
//   冻结：seek 落到目标后 currentTime 永远停在目标值、不发 seeked、
//     time 事件不再推进 → 必须走到 onSeekFailed（重试一次后判失败）；
//   推进：seek 后 time 事件继续走 → 必须落定、不判失败（对照组）；
//   暂停：暂停态 seek 到达即落定（时间本来就不走），不判失败（对照组）；
//   seeked 后冻结：libmedia 明告已定位、但时间仍不走 → 同样必须判失败。
//   瞬时暂停（第三轮）：seek 发出后解码器短暂发 'paused'、随后恢复
//     'playing'、时间永久冻在目标值 → 必须走到 onSeekFailed。旧代码
//     在到达瞬间见暂停就撤防，「正在定位」一次都不出现；
//   暂停后落定：seek 后暂停且一直不恢复 → 按暂停落定，不重试、不判
//     失败（对照组，暂停持续满复核窗口即算真停住；暂停始于 seek 下
//     发之后时，看门狗为此多发一次同目标重 seek，随后落定）；
//   确认中暂停：到达时在播、确认阶段里暂停且一直不恢复 → 同样按暂
//     停落定，不判失败（对照组）。
// 运行：cd user_frontend && node tests/softSeekResume.test.mjs
// 惯例与 tests/softDecode.test.mjs 一致：node 直接运行；计时用真实
// 定时器但全部取小值（seekTimeoutMs/seekResumeMs/seekPollMs），单文件
// 实测 3 秒内跑完。
import { register } from 'node:module'
import assert from 'node:assert'

register('./softSeekResume.hooks.mjs', import.meta.url)

const {
  createSoftPlayer,
  SOFT_SEEK_TIMEOUT_MS,
  SOFT_SEEK_RESUME_MS,
  SOFT_SEEK_RESUME_ADVANCE_S,
} = await import('../src/utils/softDecode.ts')
const { instances: fakePlayers } = await import('./fakeAvPlayer.mjs')

let passed = 0
function check(name, actual, expected) {
  assert.deepStrictEqual(actual, expected, name)
  passed += 1
  console.log('PASS ' + name)
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
// 变体已打标的地址：resolveSoftDecodeUrl 直接放行，不发探测请求。
const PLAY_URL =
  'https://cdn.test/emby/Videos/0123456789abcdef0123456789abcdef/stream.mkv?MediaSourceId=0123456789abcdef0123456789abcdef:mount_relay'

function makeHooks() {
  const calls = { seeking: [], seekFailed: [], time: [], error: [] }
  return {
    calls,
    hooks: {
      onFirstVideo: () => {},
      onFirstAudio: () => {},
      onPlaying: () => {},
      onPaused: () => {},
      onTime: (s) => calls.time.push(s),
      onEnded: () => {},
      onError: (m) => calls.error.push(m),
      onSeekingChange: (v) => calls.seeking.push(v),
      onSeekFailed: (m) => calls.seekFailed.push(m),
    },
  }
}

async function makeHandle() {
  const { calls, hooks } = makeHooks()
  const before = fakePlayers.length
  const handle = await createSoftPlayer({}, PLAY_URL, hooks, {
    ext: 'mkv',
    startTimeoutMs: 5000,
    seekTimeoutMs: 5000,
    seekResumeMs: 200,
    seekPollMs: 20,
  })
  const player = fakePlayers[before]
  assert.ok(player, 'fake player instance created')
  return { handle, player, calls }
}

// --- 冻结：seek 落到目标、时间永远停在目标值（复测 56:59 原样） ---
{
  const { handle, player, calls } = await makeHandle()
  try {
    await handle.seek(3419)
    await sleep(1500)
    check('frozen seek: retried once then failed', [player.seekCalls.length, calls.seekFailed.length], [2, 1])
    check('frozen seek: seeking pill visible until failure', calls.seeking, [true, false])
    check('frozen seek: no decode error mixed in', calls.error, [])
  } finally {
    await handle.destroy()
  }
}

// --- 推进：seek 后时间继续走（对照组，旧代码也该过） ---
{
  const { handle, player, calls } = await makeHandle()
  try {
    const p = handle.seek(3419)
    await sleep(50)
    player.tick(0.6)
    await sleep(50)
    player.tick(0.6)
    await p
    await sleep(900)
    check('advancing seek: completes without failure', [calls.seekFailed.length, player.seekCalls.length], [0, 1])
    check('advancing seek: seeking pill clears', calls.seeking, [true, false])
  } finally {
    await handle.destroy()
  }
}

// --- 暂停：暂停态 seek 到达即落定（对照组，旧代码也该过） ---
{
  const { handle, player, calls } = await makeHandle()
  try {
    await handle.pause()
    await handle.seek(3419)
    await sleep(900)
    check('paused seek: completes on arrival, no failure', [calls.seekFailed.length, player.seekCalls.length], [0, 1])
    check('paused seek: seeking pill clears', calls.seeking, [true, false])
  } finally {
    await handle.destroy()
  }
}

// --- seeked 后冻结：libmedia 明告已定位、但时间仍不走 ---
{
  const { handle, player, calls } = await makeHandle()
  try {
    await handle.seek(3419)
    player.emit('seeked')
    await sleep(1500)
    check('seeked-then-frozen: still fails after retry', [player.seekCalls.length, calls.seekFailed.length], [2, 1])
    check('seeked-then-frozen: seeking pill visible until failure', calls.seeking, [true, false])
  } finally {
    await handle.destroy()
  }
}

// --- 瞬时暂停：seek 后解码器短暂 paused、随即恢复 playing、时间永冻 ---
{
  const { handle, player, calls } = await makeHandle()
  try {
    const p = handle.seek(3419)
    // 停 10ms 再发 paused：保证暂停时刻严格晚于 seek 下发时刻（否则
    // pausedAt <= seekIssuedAt 会被当成「用户本来就停着」而立即落定，
    // 测的就不是瞬时暂停这条路了）。
    await sleep(10)
    player.emit('paused')
    await sleep(50)
    player.emit('playing')
    await p
    await sleep(1500)
    check('transient pause then frozen: retried once then failed', [player.seekCalls.length, calls.seekFailed.length], [2, 1])
    check('transient pause then frozen: seeking pill visible until failure', calls.seeking, [true, false])
    check('transient pause then frozen: no decode error mixed in', calls.error, [])
  } finally {
    await handle.destroy()
  }
}

// --- 暂停后落定：seek 后暂停且一直不恢复 → 按暂停落定，不重试不判失败 ---
{
  const { handle, player, calls } = await makeHandle()
  try {
    const p = handle.seek(3419)
    await sleep(50)
    player.emit('paused')
    await p
    await sleep(900)
    // 暂停始于 seek 下发之后：确认计时先于复核窗口到期而触发一次
    // 同目标重 seek（重发刷新下发时刻，暂停随即早于下发时刻而落定），
    // 随后落定、不判失败——多发的一次重 seek 是设计行为，不是抖动。
    check('pause after seek settles: one retry then settles, no failure', [player.seekCalls.length, calls.seekFailed.length], [2, 0])
    check('pause after seek settles: seeking pill clears', calls.seeking, [true, false])
  } finally {
    await handle.destroy()
  }
}

// --- 确认中暂停：到达时在播、确认阶段里暂停且一直不恢复 → 按暂停落定 ---
{
  const { handle, player, calls } = await makeHandle()
  try {
    const p = handle.seek(3419)
    await sleep(100)
    player.emit('paused')
    await p
    await sleep(900)
    // 同上：暂停始于 seek 下发之后，看门狗先走一次同目标重试，随后
    // 按暂停落定，不判失败。
    check('pause during confirm settles: one retry then settles, no failure', [player.seekCalls.length, calls.seekFailed.length], [2, 0])
    check('pause during confirm settles: seeking pill clears', calls.seeking, [true, false])
  } finally {
    await handle.destroy()
  }
}

// --- 常量口径：确认窗口 5 秒、至少再前进 0.5 秒 ---
check('seek resume: default confirm window is 5s', SOFT_SEEK_RESUME_MS, 5000)
check('seek resume: default advance is 0.5s', SOFT_SEEK_RESUME_ADVANCE_S, 0.5)
check('seek guard: default timeout still 15s', SOFT_SEEK_TIMEOUT_MS, 15000)

console.log(passed + ' passed')
