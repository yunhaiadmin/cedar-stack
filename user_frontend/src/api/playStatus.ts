import api from '@/api/index'

/**
 * 条目起播状态（门户后端 GET /api/play/items/{id}/status，只读）。
 *
 * 后端 envelope 为 {code: 0, msg, data}（与 panel 同口径，全局拦截器
 * 只解 code===200，这里单独处理 code===0）。
 *
 * - state=ready：已就绪（source: own 本人盘 / pool 公共池 / direct 直读）
 * - state=preparing：转存中（stage: queued 排队 / working 搬运中；
 *   progress_pct 只有后端有可靠落盘进度时才给，没有就是 null，不编；
 *   route=在等谁的单（own 本人 / pool 公共池），job_id=那张单，
 *   elapsed_seconds=已等秒，eta_seconds=预计还需秒（近 7 天 done 单
 *   p50 减已等秒，下限 5、上限 120），slow=已等超过 p90、比平时慢）
 * - state=failed：最近一次准备失败（reason 是类别键，人话在组件里；
 *   route=pool 时是池失败单，文案走池版）
 * - state=idle：还没准备过（首播现转存，组件不渲染）
 */
export type PlayState = 'ready' | 'preparing' | 'failed' | 'idle'
export type PlaySource = 'own' | 'pool' | 'direct'
export type PlayStage = 'queued' | 'working'
export type PlayRoute = 'own' | 'pool'
export type PlayFailReason =
  | 'space'
  | 'auth'
  | 'source_dead'
  | 'no_binding'
  | 'generic'
  | 'pool_source'
  | 'pool_no_member'

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
  // 池路不会返回这两个键（后端池失败只映 pool_source /
  // pool_no_member / space / generic），这里只是占位兜底。
  pool_source: {
    title: '公共池暂时备不了这部片',
    sub: '源文件在成员盘上读不到，换过几家盘都不行。可以先看看别的片，或去求片。',
  },
  pool_no_member: {
    title: '公共池暂时备不了这部片',
    sub: '公共池现在没有可用的成员盘，稍后再来看看。',
  },
}

/** 失败人话（池路）：与本人路同键不同文案——池空间满了不是
 *  用户自己的盘满了，不许把本人路的 space 文案端给没绑盘的用户
 *  （那是设计稿抓到的旧 bug：space 文案只许 route=own 时出现）。 */
export const PLAY_FAIL_COPY_POOL: Partial<
  Record<PlayFailReason, { title: string; sub: string }>
> = {
  pool_source: {
    title: '公共池暂时备不了这部片',
    sub: '源文件在成员盘上读不到，换过几家盘都不行。可以先看看别的片，或去求片。',
  },
  pool_no_member: {
    title: '公共池暂时备不了这部片',
    sub: '公共池现在没有可用的成员盘，稍后再来看看。',
  },
  space: {
    title: '公共池暂时备不了这部片',
    sub: '贡献池的空间满了，管理员清理后会自动再备。',
  },
}

/** 按 (reason, route) 取失败人话：池路先查池版，未覆盖的键
 *  （如 generic）回落到本人路同键。 */
export function failCopyFor(
  reason: PlayFailReason | null,
  route: PlayRoute | null,
): { title: string; sub: string } {
  const key = reason ?? 'generic'
  if (route === 'pool') {
    const poolCopy = PLAY_FAIL_COPY_POOL[key]
    if (poolCopy) return poolCopy
  }
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
