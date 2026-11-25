// 软解字幕映射回归测试（2026-11-26）：
// 运行：cd user_frontend && node tests/softDecodeSubtitles.test.mjs
// 惯例与 tests/softDecode.test.mjs 一致：node 直接运行、不引入
// vitest/jest；被测的是 softDecode.ts 里字幕选轨与外挂字幕载荷的
// 纯映射逻辑，不触 DOM、不 import 播放器包（真实 AVPlayer 调用在
// createSoftPlayer 内部，靠产物类型与实现查证，不在本测范围）。
import { register } from 'node:module'
import assert from 'node:assert'

register('./softDecode.hooks.mjs', import.meta.url)

const {
  effectiveSoftSubtitleIndex,
  pickSoftSubtitleTrack,
  softExternalSubtitleOf,
} = await import('../src/utils/softDecode.ts')

let passed = 0
function check(name, actual, expected) {
  assert.deepStrictEqual(actual, expected, name)
  passed += 1
  console.log(`PASS ${name}`)
}

const tracks = [
  { index: 5, url: 'https://x/Subtitles/5/Stream.vtt?api_key=a', label: '中文', language: 'zh' },
  { index: 7, url: 'https://x/Subtitles/7/Stream.vtt?api_key=a', label: 'English', language: 'en' },
  { index: 9, url: 'https://x/Subtitles/9/Stream.vtt?api_key=a' },
]

// 实际渲染轨（AVPlayer 同时只渲染一条）：主轨优先
check('主轨优先于副轨', effectiveSoftSubtitleIndex(5, 7), 5)
// 副轨在主轨关闭时接管渲染
check('主轨关闭时副轨接管', effectiveSoftSubtitleIndex(null, 7), 7)
// 主副都关 = 无字幕
check('主副都关为 null', effectiveSoftSubtitleIndex(null, null), null)

// 选轨 → 轨对象
check('选主轨返回主轨对象', pickSoftSubtitleTrack(tracks, 5, 7), tracks[0])
check('主关副开返回副轨对象', pickSoftSubtitleTrack(tracks, null, 7), tracks[1])
check('都关返回 null', pickSoftSubtitleTrack(tracks, null, null), null)
check('选中的 Index 不在轨列表里返回 null', pickSoftSubtitleTrack(tracks, 42, null), null)
check('空轨列表返回 null', pickSoftSubtitleTrack([], 5, null), null)

// Emby 字幕轨 → AVPlayer 外挂字幕载荷
check('外挂载荷映射', softExternalSubtitleOf(tracks[0]), {
  source: 'https://x/Subtitles/5/Stream.vtt?api_key=a',
  lang: 'zh',
  title: '中文',
})
check('无语言/标题时只给 source', softExternalSubtitleOf(tracks[2]), {
  source: 'https://x/Subtitles/9/Stream.vtt?api_key=a',
})

console.log(`softDecodeSubtitles: ${passed} passed`)
