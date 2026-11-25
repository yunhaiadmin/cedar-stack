// 播放页可见诊断区（seek 取证轮，2026-11-25，诊断报告 §4.1）。
//
// 纯函数 + fetchStreamRanges 的取数口径：
//   streamSignFromUrl：带签地址 → { sign, exp, itemId }；无签/残缺 → null；
//   formatTimeRanges：区间列表 → 可读文本，空为 —；
//   networkStateLabel / readyStateLabel / videoErrorLabel：状态码 → 文本；
//   formatRangeEntry：一笔 Range 记录 → 可读行；
//   fetchStreamRanges：GET /api/user/emby/stream-ranges，带 item_id/exp/sign，
//     回 ranges，空回 []。
// 运行：cd user_frontend && node tests/videoDiag.test.mjs
import { register } from 'node:module'
import assert from 'node:assert'

const {
  streamSignFromUrl,
  formatTimeRanges,
  networkStateLabel,
  readyStateLabel,
  videoErrorLabel,
  formatRangeEntry,
} = await import('../src/utils/videoDiag.ts')

let passed = 0
function check(name, actual, expected) {
  assert.deepStrictEqual(actual, expected, name)
  passed += 1
  console.log(`PASS ${name}`)
}

const SIGNED =
  'https://cdn.aetrix.love/emby/Videos/c4a84409f1abcbeedf94c416ccc05807/stream.mkv'
  + '?static=true&MediaSourceId=c4a84409f1abcbeedf94c416ccc05807&api_key=x'
  + '&uid=172&exp=1795574091&sign=0123456789abcdef0123456789abcdef'

check('sign: 带签地址解析出 sign/exp/itemId', streamSignFromUrl(SIGNED), {
  sign: '0123456789abcdef0123456789abcdef',
  exp: '1795574091',
  itemId: 'c4a84409f1abcbeedf94c416ccc05807',
})
check(
  'sign: 无签地址回 null',
  streamSignFromUrl(
    'https://cdn.aetrix.love/emby/Videos/abc/stream.mkv?static=true',
  ),
  null,
)
check(
  'sign: 缺 exp 回 null',
  streamSignFromUrl('https://x/emby/Videos/a/stream.mkv?MediaSourceId=a&sign=s'),
  null,
)
check('sign: 空串回 null', streamSignFromUrl(''), null)
check('sign: 残缺地址回 null', streamSignFromUrl('http://['), null)

function fakeRanges(pairs) {
  return {
    length: pairs.length,
    start: (i) => pairs[i][0],
    end: (i) => pairs[i][1],
  }
}
check(
  'ranges: 两段区间',
  formatTimeRanges(fakeRanges([[0, 2895], [3000, 3100.5]])),
  '0.0s–2895.0s, 3000.0s–3100.5s',
)
check(
  'ranges: 空回 —',
  formatTimeRanges({ length: 0, start: () => 0, end: () => 0 }),
  '—',
)
check('ranges: null 回 —', formatTimeRanges(null), '—')
check(
  'ranges: 抛错段跳过',
  formatTimeRanges({
    length: 2,
    start: (i) => {
      if (i === 0) throw new Error('bad')
      return 5
    },
    end: (i) => (i === 0 ? 9 : 7),
  }),
  '5.0s–7.0s',
)

check('network: 2 加载中', networkStateLabel(2), '2 加载中')
check('network: 3 无源', networkStateLabel(3), '3 无源')
check('network: 未知码', networkStateLabel(9), '9 未知')
check('ready: 0 无数据', readyStateLabel(0), '0 无数据')
check('ready: 4 能流畅播', readyStateLabel(4), '4 能流畅播')
check('error: 无', videoErrorLabel(null), '无')
check(
  'error: 解码失败带 message',
  videoErrorLabel({ code: 3, message: 'CHUNK_DEMUXER_ERROR' }),
  'code=3 解码失败：CHUNK_DEMUXER_ERROR',
)
check(
  'error: 无 message 不带冒号',
  videoErrorLabel({ code: 2, message: '' }),
  'code=2 网络错误',
)

check(
  'range entry: 后缀 miss',
  formatRangeEntry({
    range: 'bytes=-1219380021',
    start: 1141964800,
    end: 2361344820,
    suffix: true,
    outcome: 'miss',
    content_range: 'bytes 1141964800-2361344820/2361344821',
    ts: 1,
  }),
  'bytes=-1219380021 → miss · bytes 1141964800-2361344820/2361344821',
)
check(
  'range entry: 整文件 hit',
  formatRangeEntry({
    range: null,
    start: 0,
    end: 999,
    suffix: false,
    outcome: 'hit',
    content_range: null,
    ts: 1,
  }),
  '整文件 → hit · —',
)

// fetchStreamRanges：桩 @/api，断言口径与回值
register('./videoDiag.hooks.mjs', import.meta.url)
const { fetchStreamRanges } = await import('../src/api/webPlayer.ts')
const { getCalls } = await import('./stubs/api-diag.mjs')

const ranges = await fetchStreamRanges(
  'c4a84409f1abcbeedf94c416ccc05807',
  '1795574091',
  '0123456789abcdef0123456789abcdef',
)
check('fetch: 回 ranges', ranges.length, 1)
check('fetch: 命中形态', ranges[0].outcome, 'miss')
const call = getCalls[0]
check('fetch: 路径', call.url, '/api/user/emby/stream-ranges')
check('fetch: 参数', call.config.params, {
  item_id: 'c4a84409f1abcbeedf94c416ccc05807',
  exp: '1795574091',
  sign: '0123456789abcdef0123456789abcdef',
})

console.log(`\n${passed} passed`)
