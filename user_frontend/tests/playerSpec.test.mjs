// 播放页右下规格标签的源解析回归测试（2026-11-25 生产实测事故；
// 第二轮补 Versions 形态）：《黑洞表面》在选版本弹层里选了 1080p
// WEB-DL 进播放页，右下角却显示「4K SDR HEVC / 92.6 Mbps」——那是
// 条目级 MediaSources[0]（2160p REMUX）的规格。第一轮只修了
// current/playbackPicked/ms 命中条目源三档；复测证明准备阶段仍错：
// query.ms 在 Versions 形态下是版本条目 id（不是 MediaSource Id），
// 条目详情 DTO 的 MediaSources 里只有主源一条，按 Id 永远命中不了，
// 准备阶段 current 为空、PlaybackInfo 还没到，只能回落首源。
// 第二轮解析优先级必须是：
//   正在播的源（currentSource）
//   → PlaybackInfo 按所选版本选定的源
//   → 条目源里与所选版本 id 命中的那条（MediaSources 形态）
//   → 版本行里与所选版本 id 命中的那条（Versions 形态，按行上的
//     真实字段合成源：分辨率/容器/大小，不编编码与码率）
//   → 选了版本却哪都命中不了：返回 null（标签不显示），不许静默
//     回落条目首源（那是另一个版本的规格）
//   → 没选版本：条目首源（旧行为兜底）。
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
// 复测原样：ms 是版本条目 id（c4a84409…），条目 MediaSources 里只有
// 主源（Id = 主条目 guid），版本行才有这条 1080p。
const ver1080 = {
  Id: 'c4a84409f1abcbeedf94c416ccc05807',
  Name: '1080p WEB-DL',
  Width: 1920,
  Height: 1080,
  Container: 'mkv',
  SizeBytes: 2400000000,
}
const versionRows = [ver1080]

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
  'spec source: unknown wanted id hides the label (no silent fallback to another version)',
  resolveSpecSource({
    current: null,
    playbackPicked: null,
    wantedId: 'src-gone',
    itemSources,
  }),
  null,
)

check(
  'spec source: version row (query.ms is a version item id) resolves to the picked version',
  (() => {
    const r = resolveSpecSource({
      current: null,
      playbackPicked: null,
      wantedId: 'c4a84409f1abcbeedf94c416ccc05807',
      itemSources: [remux4k],
      versionRows,
    })
    return {
      Id: r?.Id,
      Container: r?.Container,
      Height: r?.MediaStreams?.[0]?.Height,
      Width: r?.MediaStreams?.[0]?.Width,
    }
  })(),
  { Id: 'c4a84409f1abcbeedf94c416ccc05807', Container: 'mkv', Height: 1080, Width: 1920 },
)

check(
  'spec source: version row match needs no item sources',
  (() => {
    const r = resolveSpecSource({
      current: null,
      playbackPicked: null,
      wantedId: 'c4a84409f1abcbeedf94c416ccc05807',
      itemSources: [],
      versionRows,
    })
    return r?.Id
  })(),
  'c4a84409f1abcbeedf94c416ccc05807',
)

check(
  'spec source: primary version (ms is the parent guid) still resolves via item source',
  resolveSpecSource({
    current: null,
    playbackPicked: null,
    wantedId: 'src-4k',
    itemSources,
    versionRows,
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
