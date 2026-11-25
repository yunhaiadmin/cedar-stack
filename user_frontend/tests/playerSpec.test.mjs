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

const { resolveSpecSource, specChipsOf, specHeadlineOf, resolutionFromName } = await import('../src/utils/playerSpec.ts')

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

// --- 第三轮：分辨率胶囊回落（版本行宽高为 0 时读版本名） ---
// 生产原样：《黑洞表面》1080p 版库里 width/height 都是 0，数值口径
// 读不到数，胶囊没有分辨率；版本名本身就是真实标签文本（后端
// _version_label），从里面解析 token 做回落。只读文本、不编数。
check('resolution from name: 1080p WEB-DL', resolutionFromName('1080p WEB-DL'), '1080p')
check('resolution from name: 2160p maps to 4K like the numeric path', resolutionFromName('2160p REMUX'), '4K')
check('resolution from name: bare 4K', resolutionFromName('4K'), '4K')
check('resolution from name: 720p', resolutionFromName('720p BluRay'), '720p')
check('resolution from name: 480p', resolutionFromName('480p'), '480p')
check('resolution from name: no token hides, never guesses', resolutionFromName('导演剪辑版'), '')
check('resolution from name: empty hides', resolutionFromName(''), '')
check('resolution from name: null hides', resolutionFromName(null), '')

const video1080 = { Index: 0, Type: 'Video', Width: 1920, Height: 1080, Codec: 'hevc' }
const videoZero = { Index: 0, Type: 'Video', Width: 0, Height: 0 }
check(
  'spec chips: numeric height wins',
  specChipsOf({ Id: 'x', Name: '2160p REMUX', Container: 'mkv', MediaStreams: [{ Index: 0, Type: 'Video', Width: 3840, Height: 2160, Codec: 'hevc' }] }),
  ['4K', 'HEVC', 'MKV'],
)
check(
  'spec chips: zero height falls back to version name (production case)',
  specChipsOf({ Id: 'c4a84409f1abcbeedf94c416ccc05807', Name: '1080p', Container: 'mkv', MediaStreams: [videoZero] }),
  ['1080p', 'MKV'],
)
check(
  'spec chips: no height and no token in name => no resolution chip',
  specChipsOf({ Id: 'x', Name: '导演剪辑版', Container: 'mkv', MediaStreams: [videoZero] }),
  ['MKV'],
)
check(
  'spec chips: end-to-end via resolveSpecSource on a zero-height version row',
  (() => {
    const r = resolveSpecSource({
      current: null,
      playbackPicked: null,
      wantedId: 'c4a84409f1abcbeedf94c416ccc05807',
      itemSources: [remux4k],
      versionRows: [{ Id: 'c4a84409f1abcbeedf94c416ccc05807', Name: '1080p', Width: 0, Height: 0, Container: 'mkv', SizeBytes: 2400000000 }],
    })
    return specChipsOf(r)
  })(),
  ['1080p', 'MKV'],
)
check(
  'spec chips: numeric height on the row still wins over the name',
  specChipsOf({ Id: 'x', Name: '1080p WEB-DL', Container: 'mkv', MediaStreams: [video1080] }),
  ['1080p', 'HEVC', 'MKV'],
)

// --- 第六轮：规格主行与 chips 同一套回落 ---
// 生产原样：准备阶段 specSource 会在 PlaybackInfo 落地前后切换——落地
// 前是版本行合成源（宽高 0 → 主行回落 'SDR'），落地后 currentSource
// 是 PlaybackInfo 源（MediaStreams 为空 → 旧主行直接返回 ''），主行
// 从「SDR」闪成空（第五轮复测「再试一次」后 SDR 不见的一半原因）。
check(
  'spec headline: zero-height version row keeps resolution from name + SDR',
  specHeadlineOf({ Id: 'c4a84409f1abcbeedf94c416ccc05807', Name: '1080p WEB-DL', Container: 'mkv', MediaStreams: [videoZero] }),
  '1080p SDR',
)
check(
  'spec headline: PlaybackInfo source with no streams still shows resolution from name',
  specHeadlineOf({ Id: 'c4a84409f1abcbeedf94c416ccc05807', Name: '1080p WEB-DL', Container: 'mkv', MediaStreams: [] }),
  '1080p SDR',
)
check(
  'spec headline: numeric stream wins, HDR token from DisplayTitle',
  specHeadlineOf({ Id: 'x', Name: '2160p REMUX', Container: 'mkv', MediaStreams: [{ Index: 0, Type: 'Video', Width: 3840, Height: 2160, Codec: 'hevc', DisplayTitle: '2160p HDR10 HEVC' }] }),
  '4K HDR10 HEVC',
)
check(
  'spec headline: 1080p stream with codec',
  specHeadlineOf({ Id: 'x', Name: '1080p WEB-DL', Container: 'mkv', MediaStreams: [video1080] }),
  '1080p SDR HEVC',
)
check('spec headline: null source hides', specHeadlineOf(null), '')
check(
  'spec headline: no stream and no resolution token hides (never a lone SDR)',
  specHeadlineOf({ Id: 'x', Name: '导演剪辑版', Container: 'mkv', MediaStreams: [] }),
  '',
)

console.log(`\n${passed} passed`)
