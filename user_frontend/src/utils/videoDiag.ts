// 播放页可见诊断区（seek 取证轮，2026-11-25，诊断报告 §4.1）。
//
// 纯函数：把 <video> 元素状态与服务端 Range 记录格式化成诊断区
// 可截图读到的文本行。诊断只读、不改任何播放行为。
// 运行：cd user_frontend && node tests/videoDiag.test.mjs

export interface StreamRangeEntry {
  range: string | null
  start: number | null
  end: number | null
  suffix: boolean
  outcome: string
  content_range: string | null
  ts: number
}

export interface StreamSign {
  sign: string
  exp: string
  /** 流地址里的 MediaSourceId（版本条目 guid，签名即按它签发） */
  itemId: string
}

/** 从流地址取播放签名（?sign=/?exp=/?MediaSourceId=）：缺任一回 null。 */
export function streamSignFromUrl(url: string): StreamSign | null {
  if (!url) return null
  try {
    const qs = new URL(url, 'http://localhost').searchParams
    const sign = (qs.get('sign') || '').trim()
    const exp = (qs.get('exp') || '').trim()
    const itemId = (qs.get('MediaSourceId') || '').trim()
    if (!sign || !exp || !itemId) return null
    return { sign, exp, itemId }
  } catch {
    return null
  }
}

export interface TimeRangesLike {
  readonly length: number
  start(index: number): number
  end(index: number): number
}

/** buffered/seekable 区间列表 → `0.0s–2895.0s, 3000.0s–3100.0s`，空为 `—`。 */
export function formatTimeRanges(ranges: TimeRangesLike | null | undefined): string {
  if (!ranges || ranges.length === 0) return '—'
  const parts: string[] = []
  for (let i = 0; i < ranges.length; i += 1) {
    try {
      parts.push(`${ranges.start(i).toFixed(1)}s–${ranges.end(i).toFixed(1)}s`)
    } catch {
      // 个别浏览器对无效索引抛错：跳过该段，不让诊断区炸掉
    }
  }
  return parts.length ? parts.join(', ') : '—'
}

export function networkStateLabel(n: number): string {
  switch (n) {
    case 0:
      return '0 空闲'
    case 1:
      return '1 待机'
    case 2:
      return '2 加载中'
    case 3:
      return '3 无源'
    default:
      return `${n} 未知`
  }
}

export function readyStateLabel(n: number): string {
  switch (n) {
    case 0:
      return '0 无数据'
    case 1:
      return '1 有元数据'
    case 2:
      return '2 有当前帧'
    case 3:
      return '3 能播'
    case 4:
      return '4 能流畅播'
    default:
      return `${n} 未知`
  }
}

export function videoErrorLabel(
  err: Pick<MediaError, 'code' | 'message'> | null | undefined,
): string {
  if (!err || err.code === null || err.code === undefined) return '无'
  const kind =
    err.code === 1
      ? '用户中止'
      : err.code === 2
        ? '网络错误'
        : err.code === 3
          ? '解码失败'
          : err.code === 4
            ? '源不支持'
            : '未知错误'
  const msg = (err.message || '').trim()
  return `code=${err.code} ${kind}${msg ? `：${msg}` : ''}`
}

/** 一笔流请求 Range → `bytes=… → miss · bytes …/…`（无 Range 即整文件）。 */
export function formatRangeEntry(e: StreamRangeEntry): string {
  const asked = e.range || '整文件'
  const got = e.content_range || '—'
  return `${asked} → ${e.outcome} · ${got}`
}
