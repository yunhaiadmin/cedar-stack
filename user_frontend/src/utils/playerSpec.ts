/**
 * 播放页右下规格标签（分辨率/动态范围/编码/码率）的源解析
 * （2026-11-25 生产实测事故修复）。
 *
 * 事故：《黑洞表面》在选版本弹层里选了 1080p WEB-DL 进播放页，
 * 右下角却显示「4K SDR HEVC / 92.6 Mbps」——那是条目级
 * MediaSources[0]（2160p REMUX）的规格。旧口径是三个 computed
 * 各自写 `currentSource || item.MediaSources[0]`：currentSource
 * 只在 start() 取完 PlaybackInfo 后才定，idle/准备初期它为空，
 * 标签就回落条目首源，完全不看用户选定的版本。
 *
 * 解析优先级（本函数是唯一口径，不许再在 computed 里各写一份）：
 * 1. current：正在播的源（PlaybackInfo 选定且已起播）；
 * 2. playbackPicked：PlaybackInfo 已到、按所选版本选定的源
 *    （调用方传 pickSource 的结果，与 start() 的实际选择同口径）；
 * 3. 条目源里 Id 与所选版本（query.ms）命中的那条——PlaybackInfo
 *    还没到时，用户选了哪个版本就先按哪个版本显示；
 * 4. 条目首源：都没选版本、PlaybackInfo 也没到时的旧行为兜底。
 */
export interface SpecSourceLike {
  Id?: string
}

export function resolveSpecSource<T extends SpecSourceLike>(input: {
  current?: T | null
  playbackPicked?: T | null
  wantedId?: string
  itemSources?: readonly T[] | null
}): T | null {
  if (input.current) return input.current
  if (input.playbackPicked) return input.playbackPicked
  const list = input.itemSources || []
  const wanted = (input.wantedId || '').trim()
  if (wanted) {
    const hit = list.find((s) => s?.Id === wanted)
    if (hit) return hit
  }
  return list[0] || null
}
