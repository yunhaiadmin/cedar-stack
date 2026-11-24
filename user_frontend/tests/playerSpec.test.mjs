// 播放页右下规格标签的源解析回归测试（2026-11-25 生产实测事故）：
// 《黑洞表面》在选版本弹层里选了 1080p WEB-DL 进播放页，右下角却
// 显示「4K SDR HEVC / 92.6 Mbps」——那是条目级 MediaSources[0]
// （2160p REMUX）的规格。旧口径：currentSource 未定时一律回落
// 条目首源，完全不看用户经 query.ms 选定的版本、也不看
// PlaybackInfo 选定的源。解析优先级必须是：
//   正在播的源（currentSource）
//   → PlaybackInfo 按所选版本选定的源
//   → 条目源里与所选版本 id 命中的那条
//   → 条目首源（PlaybackInfo 还没到、又没选版本时的旧行为）。
// 运行：cd user_frontend && node tests/playerSpec.test.mjs
import assert from 'node:assert'

const { resolveSpecSource } = await import('../src/utils/playerSpec.ts')

let passed = 0
function check(name, actual, expected) {
  assert.deepStrictEqual(actual, expected, name)
  passed += 1
  console.log(`PASS ${name}`)
}

const remux4k = { Id: 'src-4k', Name: '2160p REMUX' }
const webdl1080 = { Id: 'src-1080', Name: '1080p WEB-DL' }
const itemSources = [remux4k, webdl1080]

check(
  'spec source: current (actually playing) wins over everything',
  resolveSpecSource({
    current: webdl1080,
    playbackPicked: remux4k,
    wantedId: 'src-4k',
    itemSources,
  }),
  webdl1080,
)

check(
  'spec source: PlaybackInfo-picked source when nothing playing yet',
  resolveSpecSource({
    current: null,
    playbackPicked: webdl1080,
    wantedId: 'src-1080',
    itemSources,
  }),
  webdl1080,
)

check(
  'spec source: user-picked version (query.ms) beats item first source',
  resolveSpecSource({
    current: null,
    playbackPicked: null,
    wantedId: 'src-1080',
    itemSources,
  }),
  webdl1080,
)

check(
  'spec source: unknown wanted id falls back to item first source',
  resolveSpecSource({
    current: null,
    playbackPicked: null,
    wantedId: 'src-gone',
    itemSources,
  }),
  remux4k,
)

check(
  'spec source: no wanted id keeps legacy item-first fallback',
  resolveSpecSource({ current: null, playbackPicked: null, wantedId: '', itemSources }),
  remux4k,
)

check(
  'spec source: nothing available => null',
  resolveSpecSource({ current: null, playbackPicked: null, wantedId: 'src-1080', itemSources: [] }),
  null,
)

check(
  'spec source: all inputs missing => null',
  resolveSpecSource({}),
  null,
)

console.log(`\n${passed} passed`)
