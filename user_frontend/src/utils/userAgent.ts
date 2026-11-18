/**
 * 活跃会话行纯函数（G-60/G-63/G-64，2026-11-18）：
 * - parseUserAgent：UA → 系统 / 浏览器 / 设备形态（行头行展示用）；
 * - formatSessionTime：ISO 时间 → 绝对格式 YYYY-MM-DD HH:mm（友站口径，
 *   最近活动与到期均用此格式，不用「x 分钟前」）；
 * - sessionFilterCounts：三筛选计数（媒体客户端只进「全部」）。
 * 纯函数无副作用，供 tests/activeSessions.test.mjs 真执行。
 */
import { parseServerTime } from './serverTime.ts'

export type SessionKind = 'web' | 'device' | 'danmaku'

export interface ParsedUserAgent {
  os: string
  browser: string
  kind: 'desktop' | 'mobile'
}

export function parseUserAgent(ua: string | null | undefined): ParsedUserAgent {
  const s = ua || ''
  let os = '未知系统'
  if (/Windows NT/.test(s)) os = 'Windows'
  else if (/iPhone|iPad|iPod/.test(s)) os = 'iOS'
  else if (/Android/.test(s)) os = 'Android'
  else if (/Mac OS X/.test(s)) os = 'macOS'
  else if (/Linux/.test(s)) os = 'Linux'
  let browser = '未知浏览器'
  // 顺序有讲究：Edge 的 UA 含 Chrome、Chrome 的 UA 含 Safari，先判 Edge 再判 Chrome
  if (/Edg\//.test(s)) browser = 'Edge'
  else if (/Chrome\//.test(s)) browser = 'Chrome'
  else if (/Firefox\//.test(s)) browser = 'Firefox'
  else if (/Safari\//.test(s)) browser = 'Safari'
  const kind: 'desktop' | 'mobile' = /Mobile|Android|iPhone|iPad|iPod/.test(s)
    ? 'mobile'
    : 'desktop'
  return { os, browser, kind }
}

export function formatSessionTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = parseServerTime(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export interface SessionFilterCounts {
  all: number
  web: number
  danmaku: number
}

export function sessionFilterCounts(
  items: Array<{ kind: SessionKind }>,
): SessionFilterCounts {
  return {
    all: items.length,
    web: items.filter((it) => it.kind === 'web').length,
    danmaku: items.filter((it) => it.kind === 'danmaku').length,
  }
}
