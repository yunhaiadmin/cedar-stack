/**
 * 弹幕 API 专职接口（第三方播放器弹幕 Token，2026-11-18 友站形态对齐单）。
 *
 * 与站内自发弹幕（@/api/danmaku，走播放器内 JWT）是两套功能：本模块是
 * 「每账号一个专属 Token＋选线路拼地址」给外部播放器填的弹幕 API。
 *
 * 契约（后端单并行施工，本文件按约定口径先行，联调差异见 PR 回报）：
 * - GET    /api/user/danmu/token            查状态（有无＋到期时刻）
 * - POST   /api/user/danmu/token            生成（每账号限一个，有效期一年）
 * - DELETE /api/user/danmu/token            删除
 * - GET    /api/user/danmu/lines            线路清单（名称＋副文案）
 * - GET    /api/user/danmu/address?line=<id> 按所选线路取完整弹幕地址
 */
import api from '@/api'

export interface DanmuTokenStatus {
  has_token: boolean
  /** ISO 时刻字符串；无 Token 时为 null */
  expires_at: string | null
}

export interface DanmuLine {
  id: string | number
  name: string
  description: string
}

/** 状态回包归一：主形态 {has_token, expires_at}，缺 has_token 时按到期时刻推断 */
function normalizeStatus(raw: any): DanmuTokenStatus {
  if (!raw || typeof raw !== 'object') return { has_token: false, expires_at: null }
  const expires = raw.expires_at ?? raw.expire_at ?? raw.token?.expires_at ?? null
  const has =
    typeof raw.has_token === 'boolean' ? raw.has_token : Boolean(expires || raw.token)
  return { has_token: has, expires_at: has ? (expires ?? null) : null }
}

export async function fetchDanmuTokenStatus(): Promise<DanmuTokenStatus> {
  const raw = await api.get<never, any>('/api/user/danmu/token')
  return normalizeStatus(raw)
}

export async function createDanmuToken(): Promise<DanmuTokenStatus> {
  const raw = await api.post<never, any>('/api/user/danmu/token')
  return normalizeStatus(raw)
}

export async function deleteDanmuToken(): Promise<void> {
  await api.delete<never, unknown>('/api/user/danmu/token')
}

export async function fetchDanmuLines(): Promise<DanmuLine[]> {
  const raw = await api.get<never, any>('/api/user/danmu/lines')
  const list: any[] = Array.isArray(raw) ? raw : (raw?.lines ?? raw?.items ?? [])
  return list
    .filter((l) => l && (typeof l.id === 'string' || typeof l.id === 'number'))
    .map((l) => ({
      id: l.id,
      name: String(l.name ?? ''),
      description: String(l.description ?? l.sub ?? ''),
    }))
}

export async function fetchDanmuAddress(lineId: string | number): Promise<string> {
  const raw = await api.get<never, any>('/api/user/danmu/address', {
    params: { line: lineId },
  })
  return String(raw?.address ?? raw?.url ?? '')
}
