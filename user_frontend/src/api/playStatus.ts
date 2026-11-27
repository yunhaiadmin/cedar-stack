import api from '@/api/index'

/**
 * 条目起播状态（门户后端 GET /api/play/items/{id}/status，只读）。
 *
 * 后端 envelope 为 {code: 0, msg, data}（与 panel 同口径，全局拦截器
 * 只解 code===200，这里单独处理 code===0）。
 *
 * - state=ready：已就绪（source: own 本人盘 / direct 直读）
 * - state=preparing：转存中（stage: queued 排队 / working 搬运中；
 *   progress_pct 只有后端有可靠落盘进度时才给，没有就是 null，不编；
 *   route=在等谁的单（own 本人），job_id=那张单，
 *   elapsed_seconds=已等秒，eta_seconds=预计还需秒（近 7 天 done 单
 *   p50 减已等秒，下限 5、上限 120），slow=已等超过 p90、比平时慢）
 * - state=failed：最近一次准备失败（reason 是类别键，人话在组件里）
 * - state=idle：还没准备过（首播现转存，组件不渲染）
 */
export type PlayState = 'ready' | 'preparing' | 'failed' | 'idle'
export type PlaySource = 'own' | 'direct'
export type PlayStage = 'queued' | 'working'
export type PlayRoute = 'own'
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
  route: PlayRoute | null
  job_id: number | null
  elapsed_seconds: number | null
  eta_seconds: number | null
  slow: boolean
}

/** 失败人话（本人路）：reason 类别键 → 标题＋能照着做的建议。
 *  与播放器等待循环共用一份，不许各写一份。 */
export const PLAY_FAIL_COPY: Record<PlayFailReason, { title: string; sub: string }> = {
  space: {
    title: '上次没准备成：你的绑定盘空间不足',
    sub: '清一清绑定盘的空间，或者换绑一个更大的盘，再回来点播。',
  },
  auth: {
    title: '上次没准备成：绑定盘的授权失效了',
    sub: '去账号设置里重新绑定一次网盘，再回来点播。',
  },
  source_dead: {
    title: '这部片的源文件已经失效了',
    sub: '可以去求片那边求一下，或者先看看别的片。',
  },
  no_binding: {
    title: '你还没有绑定网盘',
    sub: '先绑定自己的网盘，再回来点播。',
  },
  generic: {
    title: '上次准备这部片失败了',
    sub: '再点一次播放器就是重试；反复失败就在群里喊一下管理员。',
  },
}

/** 按 reason 取失败人话，未知键回落 generic。route 保留形参
 * （现行只有本人路 own），与既有调用点签名一致。 */
export function failCopyFor(
  reason: PlayFailReason | null,
  route: PlayRoute | null,
): { title: string; sub: string } {
  const key = reason ?? 'generic'
  return PLAY_FAIL_COPY[key] ?? PLAY_FAIL_COPY.generic
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
