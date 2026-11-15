/**
 * 站内自发弹幕接口（网页播放器，2026-11-15）。
 *
 * 走门户 JWT 口（@/api），与第三方客户端的 Danmu 空弹幕兼容端点无关：
 * - GET  /api/user/danmaku/{guid}?start=&end= 拉时间窗内弹幕（时间轴升序），
 *   同时下发字数上限与最小发送间隔；
 * - POST /api/user/danmaku/{guid} 发一条（敏感词/超长/频繁由服务端拒绝，
 *   错误文案经播放器的 errorText 透出）。
 * 站点开关（danmaku_enabled，后台「弹幕设置」）关时两口 404，
 * 播放器据此隐藏弹幕入口。
 */
import api from '@/api'

export interface DanmakuItem {
  id: number
  position_seconds: number
  text: string
  username: string
  mine: boolean
  created_at: string | null
}

export interface DanmakuWindow {
  enabled: boolean
  max_text_length: number
  min_interval_seconds: number
  items: DanmakuItem[]
}

export async function fetchDanmakuWindow(
  itemGuid: string,
  start: number,
  end: number,
): Promise<DanmakuWindow> {
  return api.get<never, DanmakuWindow>(
    `/api/user/danmaku/${encodeURIComponent(itemGuid)}`,
    { params: { start, end } },
  )
}

export async function postDanmaku(
  itemGuid: string,
  positionSeconds: number,
  text: string,
): Promise<{ ok: boolean; item: DanmakuItem }> {
  return api.post<never, { ok: boolean; item: DanmakuItem }>(
    `/api/user/danmaku/${encodeURIComponent(itemGuid)}`,
    { position_seconds: positionSeconds, text },
  )
}
