/**
 * 解析服务端下发的时间串（PA-050；语义与 user 端 #1707 的 serverTime.ts 一致）。
 *
 * 后端以 datetime.now() + isoformat() 下发 naive UTC 串（无时区后缀），
 * 直接 new Date(串) 会按浏览器本地时区解释，在 Asia/Shanghai 下早 8 小时。
 * - 带时区后缀（Z / ±HH:MM / ±HHMM / ±HH）的串：原样解析；
 * - 无后缀的日期时间串：按 UTC 解析（补 Z 后缀）；
 * - 纯日期串（YYYY-MM-DD）：保持原样（new Date 的本地午夜语义）；
 * - 空串 / null / undefined：返回 Invalid Date，调用方按原有空态处理。
 */
export function parseServerTime(v: string | null | undefined): Date {
  if (v == null) return new Date(NaN)
  const s = v.trim()
  if (!s) return new Date(NaN)
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return new Date(s)
  if (/([Zz]|[+-]\d{2}:?\d{2}|[+-]\d{2})$/.test(s)) return new Date(s)
  return new Date(`${s.replace(' ', 'T')}Z`)
}

const _pad2 = (n: number) => String(n).padStart(2, '0')

/** 服务端时间串 → 本地 'YYYY-MM-DD HH:mm'（withSeconds 时带秒）；无效/空返回 ''。 */
export function formatServerTime(v: string | null | undefined, withSeconds = false): string {
  const d = parseServerTime(v)
  if (Number.isNaN(d.getTime())) return ''
  const base = `${d.getFullYear()}-${_pad2(d.getMonth() + 1)}-${_pad2(d.getDate())} ${_pad2(d.getHours())}:${_pad2(d.getMinutes())}`
  return withSeconds ? `${base}:${_pad2(d.getSeconds())}` : base
}

/** 服务端时间串 → 本地日期 'YYYY-MM-DD'；无效/空返回 ''。 */
export function formatServerDate(v: string | null | undefined): string {
  const d = parseServerTime(v)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${_pad2(d.getMonth() + 1)}-${_pad2(d.getDate())}`
}

/** 已有 Date 对象 → 本地日期 'YYYY-MM-DD'（不要用 toISOString，那是 UTC 日期）。 */
export function formatLocalDate(d: Date): string {
  return `${d.getFullYear()}-${_pad2(d.getMonth() + 1)}-${_pad2(d.getDate())}`
}
