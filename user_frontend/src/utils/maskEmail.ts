/**
 * 邮箱脱敏展示（与后端 backend.api.emby_portal.mask_email 同口径）：
 * 本地名只露首尾、域名原样，如 a***z@example.com。只用于展示，
 * 任何接口调用都用全址。
 */
export function maskEmail(email?: string | null): string {
  const raw = (email || '').trim()
  const at = raw.indexOf('@')
  if (at < 0) return ''
  const local = raw.slice(0, at)
  const domain = raw.slice(at + 1)
  const maskedLocal = local.length <= 2 ? `${local.slice(0, 1)}***` : `${local.slice(0, 1)}***${local.slice(-1)}`
  return `${maskedLocal}@${domain}`
}
