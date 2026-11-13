/**
 * 网页播放器专用接口（UHD 批 3 · 网页播放器单）。
 *
 * 取流口径与已下线的 EA /watch 轻量播放器、旧 WatchView 逐字一致，不新造一套：
 * - 鉴权：EA 只认 Emby token（门户 JWT 回退已删），本模块每个 EA 请求
 *   都必须经 emby.ts 的 embyAuthConfig() 带上账号卡下发的网页端专用
 *   token（X-Emby-Token）；漏带必 401「Invalid access token」
 *   （2026-11-13 网页播放全坏事故，契约 scripts/check_web_player_emby_auth.py 钉住）。
 * - 版本/线路：POST {EA}/emby/Items/{id}/PlaybackInfo（body {}）
 *   → { MediaSources, PlaySessionId }。每个 MediaSource 即一个版本/线路，
 *   DirectStreamUrl 是本站唯一构造点下发的带签名地址
 *   （/emby/Videos/{guid}/stream{后缀}?static=true&MediaSourceId=&api_key=&play_sign），
 *   门禁/quota/副本选路全在服务端取流时现场裁决，前端只消费、不判定。
 * - 字幕：MediaSource.MediaStreams 里的字幕轨（Type=Subtitle、
 *   IsTextSubtitleStream=true）带 DeliveryUrl（Stream.vtt，PlaybackInfo 层
 *   已拼播放签名授权串），直接喂 <track>。
 * - 进度上报：POST {EA}/emby/Sessions/Playing | /Playing/Progress |
 *   /Playing/Stopped，body 与旧 wrapper 逐字一致
 *   { ItemId, PositionTicks, IsPaused?, PlayMethod }，另附 PlaySessionId /
 *   MediaSourceId（Emby 标准字段，服务端按确定性会话键落同一行）。
 * - 弹幕：本站没有弹幕数据源（服务端只有 Danmu 插件约定的空弹幕兼容
 *   端点），本模块不提供弹幕数据，播放器面板只做设置与缺口提示。
 */
import api from '@/api'
import { embyAuthConfig, ensureEmbyBase, secondsToTicks, type EmbyMediaSource } from '@/api/emby'

export interface PlaybackInfoResult {
  MediaSources: EmbyMediaSource[]
  PlaySessionId: string
}

async function embyBase(override?: string): Promise<string> {
  const raw = (override || '').trim()
  if (raw) return raw.replace(/\/+$/, '')
  return (await ensureEmbyBase()).replace(/\/+$/, '')
}

/**
 * 取版本/线路清单（会过服务端播放门禁，未就绪/无权限时按服务端错误抛出）。
 * overrideBase：弹层选定的播放线路入口（已经过账号下发清单校验）——
 * 给了就本次播放的 EA 请求都走该入口；不给走账号默认入口。
 * 流地址仍由服务端在 PlaybackInfo 里按现行策略下发，前端不改。
 */
export async function fetchPlaybackInfo(itemId: string, overrideBase?: string): Promise<PlaybackInfoResult> {
  const base = await embyBase(overrideBase)
  return api.post<never, PlaybackInfoResult>(
    `${base}/emby/Items/${encodeURIComponent(itemId)}/PlaybackInfo`,
    {},
    embyAuthConfig(),
  )
}

export interface SessionReport {
  itemId: string
  positionSeconds: number
  playMethod: string
  mediaSourceId?: string
  playSessionId?: string
}

function reportBody(r: SessionReport, paused?: boolean): Record<string, unknown> {
  const body: Record<string, unknown> = {
    ItemId: r.itemId,
    PositionTicks: secondsToTicks(Math.max(0, Math.floor(r.positionSeconds))),
    PlayMethod: r.playMethod,
  }
  if (paused !== undefined) body.IsPaused = paused
  if (r.mediaSourceId) body.MediaSourceId = r.mediaSourceId
  if (r.playSessionId) body.PlaySessionId = r.playSessionId
  return body
}

export async function reportPlaying(r: SessionReport, overrideBase?: string): Promise<void> {
  const base = await embyBase(overrideBase)
  await api.post(`${base}/emby/Sessions/Playing`, reportBody(r), embyAuthConfig())
}

export async function reportProgress(r: SessionReport, paused: boolean, overrideBase?: string): Promise<void> {
  const base = await embyBase(overrideBase)
  await api.post(`${base}/emby/Sessions/Playing/Progress`, reportBody(r, paused), embyAuthConfig())
}

export async function reportStopped(r: SessionReport, overrideBase?: string): Promise<void> {
  const base = await embyBase(overrideBase)
  await api.post(`${base}/emby/Sessions/Playing/Stopped`, reportBody(r), embyAuthConfig())
}

/** 退出页面时尽力上报停止（页面卸载场景用 sendBeacon 兜底，失败不抛） */
export function reportStoppedBeacon(r: SessionReport, overrideBase?: string): void {
  try {
    const raw = (overrideBase || '').trim() || localStorage.getItem('emby_base_url') || ''
    const base = raw.startsWith('{')
      ? (JSON.parse(raw) as { u?: string }).u || ''
      : raw
    const token = localStorage.getItem('access_token') || ''
    if (!base || !token) return
    const blob = new Blob([JSON.stringify(reportBody(r))], { type: 'application/json' })
    // sendBeacon 不能带 Authorization 头；EA 同时认 api_key 查询串（账号卡图片 key 同口径）
    const key = localStorage.getItem('emby_api_key') || ''
    const params = new URLSearchParams()
    if (key) params.set('api_key', key)
    const qs = params.toString()
    const url = `${base.replace(/\/+$/, '')}/emby/Sessions/Playing/Stopped${qs ? `?${qs}` : ''}`
    navigator.sendBeacon(url, blob)
  } catch {
    /* 卸载兜底，失败无声 */
  }
}
