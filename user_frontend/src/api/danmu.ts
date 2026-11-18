/**
 * 弹幕 API 专职接口（第三方播放器弹幕 Token，2026-11-18 友站形态对齐单）。
 *
 * 与站内自发弹幕（@/api/danmaku，走播放器内 JWT）是两套功能：本模块是
 * 「每账号一个专属 Token＋选线路拼地址」给外部播放器填的弹幕 API。
 *
 * 契约（后端 #350 实测回包，backend/api/danmu_api.py＋backend/danmu_api.py）：
 * - GET    /api/user/danmu/token            {exists, expires_at}（不回明文）
 * - POST   /api/user/danmu/token            {exists, expires_at, created}
 * - DELETE /api/user/danmu/token            {exists: false, expires_at: null}
 * - GET    /api/user/danmu/lines            {lines: [{id, name, note, host}]}
 * - GET    /api/user/danmu/address?line=id  {line, address}；未生成 409
 * 归一以 exists 为主形态（exists→has_token）；旧别名（has_token 等）仅作
 * 兜底，主形态出现时一律以主形态为准，不许冲突。
 */
import api from '@/api'

export interface DanmuTokenStatus {
  has_token: boolean
  /** ISO 时刻字符串；无 Token 时为 null */
  expires_at: string | null
}

export interface DanmuTokenCreateResult extends DanmuTokenStatus {
  /** 本次是否新建：已有有效 Token 时后端回 created=false（不重复生成） */
  created: boolean
}

export interface DanmuLine {
  id: string
  name: string
  /** 线路副文案（后端字段 note） */
  note: string
  /** 裸域名；地址由后端按线路拼好，前端不自拼 */
  host: string
}

/** 状态回包归一：主形态 {exists, expires_at}；exists 缺失时才看旧别名兜底 */
function normalizeStatus(raw: any): DanmuTokenStatus {
  if (!raw || typeof raw !== 'object') return { has_token: false, expires_at: null }
  const expires = raw.expires_at ?? raw.expire_at ?? null
  const has =
    typeof raw.exists === 'boolean'
      ? raw.exists
      : typeof raw.has_token === 'boolean'
        ? raw.has_token
        : Boolean(expires)
  return { has_token: has, expires_at: has ? (expires ?? null) : null }
}

export async function fetchDanmuTokenStatus(): Promise<DanmuTokenStatus> {
  const raw = await api.get<never, any>('/api/user/danmu/token')
  return normalizeStatus(raw)
}

export async function createDanmuToken(): Promise<DanmuTokenCreateResult> {
  const raw = await api.post<never, any>('/api/user/danmu/token')
  return { ...normalizeStatus(raw), created: raw?.created === true }
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
      id: String(l.id),
      name: String(l.name ?? ''),
      note: String(l.note ?? l.description ?? l.sub ?? ''),
      host: String(l.host ?? ''),
    }))
}

export async function fetchDanmuAddress(lineId: string): Promise<string> {
  const raw = await api.get<never, any>('/api/user/danmu/address', {
    params: { line: lineId },
  })
  return String(raw?.address ?? raw?.url ?? '')
}
