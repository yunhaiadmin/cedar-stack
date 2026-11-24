/**
 * 播放页右下规格标签（分辨率/动态范围/编码/码率）的源解析
 * （2026-11-25 生产实测事故修复；第二轮补 Versions 形态）。
 *
 * 事故：《黑洞表面》在选版本弹层里选了 1080p WEB-DL 进播放页，右下角却
 * 显示「4K SDR HEVC / 92.6 Mbps」——那是条目级 MediaSources[0]（2160p REMUX）的规格。
 *
 * query.ms 到底是什么（第二轮查清）：弹层有两种版本形态——
 * MediaSources 形态下 ms 是 MediaSource Id；Versions 形态（多版本条目，
 * 版本是独立条目行）下 ms 是版本条目 id。两处 Id 都是条目 guid
 * （后端 _media_source 的 Id 即 item.guid），但条目详情 DTO 的
 * MediaSources 里只有主源那一条（Id = 主条目 guid），版本条目 id
 * 在里面永远命中不了。准备阶段（标签唯一显示的阶段，见 WebPlayerView
 * 模板 v-if）current 为空、PlaybackInfo 还没到，解析只能靠 ms，
 * 旧口径回落条目首源就正好显示了错的版本。
 *
 * 解析优先级（本函数是唯一口径，不许再在 computed 里各写一份）：
 * 1. current：正在播的源（PlaybackInfo 选定且已起播）；
 * 2. playbackPicked：PlaybackInfo 已到、按所选版本选定的源
 *    （调用方传 pickSource 的结果，与 start() 的实际选择同口径；
 *    PlaybackInfo 的源是按版本展开的，ms 在里面能命中版本 guid）；
 * 3. 条目源里 Id 与所选版本（query.ms）命中的那条——MediaSources
 *    形态下 ms 就是源 Id；
 * 4. 版本行里 Id 与所选版本命中的那条（Versions 形态）：版本行只有
 *    宽高/容器/大小是真字段（无编码/码率/音频），按它合成一份只有
 *    真实字段的源——标签显示正确的版本与分辨率，不编数；
 * 5. 选了版本却哪都命中不了 → 返回 null（标签直接不显示），不许
 *    静默回落条目首源：那是另一个版本的规格，显示出来就是错的；
 * 6. 没选版本 → 条目首源（旧行为兜底）。
 */
export interface SpecSourceLike {
  Id?: string
}

/** 版本行（EmbyItemVersion 的标签用子集）：只有这些字段是真字段。 */
export interface SpecVersionRow {
  Id?: string
  Name?: string
  Width?: number | null
  Height?: number | null
  Container?: string | null
  SizeBytes?: number | null
}

/** 版本行合成的标签源：只有版本行上的真实字段（宽高/容器/大小）。 */
export interface SpecSourceSynthesized {
  Id: string
  Name?: string
  Container?: string | null
  Size?: number | null
  MediaStreams?: Array<{
    Index: number
    Type: string
    Width?: number | null
    Height?: number | null
  }>
}

export function resolveSpecSource<T extends SpecSourceLike>(input: {
  current?: T | null
  playbackPicked?: T | null
  wantedId?: string
  itemSources?: readonly T[] | null
  versionRows?: readonly SpecVersionRow[] | null
}): T | SpecSourceSynthesized | null {
  if (input.current) return input.current
  if (input.playbackPicked) return input.playbackPicked
  const list = input.itemSources || []
  const wanted = (input.wantedId || '').trim()
  if (wanted) {
    const hit = list.find((s) => s?.Id === wanted)
    if (hit) return hit
    const row = (input.versionRows || []).find((v) => v?.Id === wanted)
    if (row) return versionRowToSpecSource(row)
    return null
  }
  return list[0] || null
}

function versionRowToSpecSource(row: SpecVersionRow): SpecSourceSynthesized {
  return {
    Id: row.Id || '',
    Name: row.Name,
    Container: row.Container ?? null,
    Size: row.SizeBytes ?? null,
    MediaStreams: [
      {
        Index: 0,
        Type: 'Video',
        Width: row.Width ?? null,
        Height: row.Height ?? null,
      },
    ],
  }
}
