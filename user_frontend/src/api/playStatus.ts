import api from '@/api/index'

/**
 * 条目起播状态（门户后端 GET /api/play/items/{id}/status，只读）。
 *
 * 后端 envelope 为 {code: 0, msg, data}（与 panel 同口径，全局拦截器
 * 只解 code===200，这里单独处理 code===0）。
 *
 * - state=ready：已就绪（source: own 本人盘 / pool 公共池 / direct 直读）
 * - state=preparing：转存中（stage: queued 排队 / working 搬运中；
 *   progress_pct 只有后端有可靠落盘进度时才给，没有就是 null，不编）
 * - state=failed：最近一次准备失败（reason 是类别键，人话在组件里）
 * - state=idle：还没准备过（首播现转存，组件不渲染）
 */
export type PlayState = 'ready' | 'preparing' | 'failed' | 'idle'
export type PlaySource = 'own' | 'pool' | 'direct'
export type PlayStage = 'queued' | 'working'
export type PlayFailReason =
  | 'space'
  | 'auth'
  | 'source_dead'
  | 'no_binding'
  | 'generic'

export interface PlayStatus {
  item_id: number
  state: PlayState
  source: PlaySource | null
  stage: PlayStage | null
  progress_pct: number | null
  reason: PlayFailReason | null
}

interface Envelope<T> {
  code: number
  msg: string
  data: T
}

export async function fetchPlayStatus(
  itemId: string | number,
): Promise<PlayStatus> {
  const res = (await api.get(
    `/api/play/items/${encodeURIComponent(String(itemId))}/status`,
  )) as Envelope<PlayStatus> | PlayStatus
  if (res && typeof res === 'object' && 'code' in res) {
    if ((res as Envelope<PlayStatus>).code !== 0) {
      throw new Error((res as Envelope<PlayStatus>).msg || 'play status error')
    }
    return (res as Envelope<PlayStatus>).data
  }
  return res as PlayStatus
}
