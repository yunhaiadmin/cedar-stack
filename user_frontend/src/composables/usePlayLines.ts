/**
 * 播放线路清单（弹层选线 / 播放器校验共用，与线路测速页同一真源）：
 * 形态——服 = 账号卡 GET /api/user/emby/server 的 realms[]，
 * 每条线路带自己的大类（realms[].lines[].category，如「国际
 * 线路」，主线路在前），选线面按大类分组（group 取 category）。
 * 账号卡下发时已按用户套餐大类在服务端过滤（买哪个大类的套餐
 * 就只下发哪个大类的线路），前端不再二次过滤。
 * 没下发线路的服（旧数据/地址靠推断）用它的 base_url 补一条；
 * 顶层 base_url 是当前服主线路，清单里没有时用它补一行。两组按
 * 规范化 URL 去重（去尾斜杠）。线路只是「入口地址」：选线后本次
 * 播放的 EA 请求（PlaybackInfo / 会话上报）改走该入口，流地址
 * 仍由服务端按现行策略下发，前端不拼、不改签名。
 *
 * 线路流量配额（2026-11-19）：账号卡给每条线路带 quota_exhausted
 * （后端已做全线用尽回落：全用尽时恒 false）。用尽的线保留在清单
 * 里供选线面显示「暂停服务」，但不可选；校验时若目标线已用尽且
 * 还有未用尽的线，自动改落到第一条未用尽的线（分流），全用尽
 * 时按现行口径原样放行——配额不许把播放弄断。
 */
import { embyApi } from '@/api'

export interface PlayLine {
  key: string
  /** 线路名（主线路 / 副线路 / 管理员自定义名） */
  name: string
  url: string
  kind: 'realm'
  /** 所属大类名（线路自己的分类），选线面按它分组 */
  group: string
  /** 主线路（同组第一条）；默认选中主线路 */
  primary: boolean
  /** 线路流量配额已用尽：显示「暂停服务」、不可选、校验时避开 */
  exhausted: boolean
}

export interface PlayLinesResult {
  lines: PlayLine[]
  /** 当前默认线路（账号卡顶层 base_url，规范化后）；拿不到时为空串 */
  defaultUrl: string
}

export function normalizeLineUrl(url: string): string {
  return (url || '').trim().replace(/\/+$/, '')
}

function hostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

let inflight: Promise<PlayLinesResult> | null = null

/** 拉线路清单。并发去重；失败返回空清单（调用方据此隐藏选线，不阻塞播放）。 */
export function loadPlayLines(): Promise<PlayLinesResult> {
  if (inflight) return inflight
  inflight = (async (): Promise<PlayLinesResult> => {
    const lines: PlayLine[] = []
    const seen = new Set<string>()
    let defaultUrl = ''
    const push = (
      key: string, name: string, rawUrl: string,
      group: string, primary: boolean, exhausted = false,
    ) => {
      const url = normalizeLineUrl(rawUrl)
      if (!url || seen.has(url)) return
      seen.add(url)
      lines.push({
        key, kind: 'realm',
        name: name || hostOf(url), url, group, primary, exhausted,
      })
    }
    try {
      const card = await embyApi.getAccountCard()
      defaultUrl = normalizeLineUrl(card.base_url ?? '')
      for (const r of card.realms ?? []) {
        const realmLines = r.lines ?? []
        if (realmLines.length) {
          for (const l of realmLines) {
            // 分组 = 线路的大类（category）；老下发没带大类时回退服名
            const group = (l.category || '').trim() || r.name || ''
            push(`line:${l.id}`, l.name, l.url ?? '', group,
                 !!l.is_primary, !!l.quota_exhausted)
          }
        } else if (r.base_url) {
          // 没下发线路的服：用它的地址补一条（主线路形态，不受大类过滤影响）
          push(`realm:${r.id}`, r.name, r.base_url, r.name || '', true,
               !!r.quota_exhausted)
        }
      }
      if (defaultUrl && !seen.has(defaultUrl)) {
        push('realm:top', card.realm_name || '主线路', defaultUrl,
             card.realm_name || '', true, !!card.quota_exhausted)
      }
    } catch { /* 账号卡失败：清单为空，调用方据此隐藏选线 */ }
    return { lines, defaultUrl }
  })().finally(() => {
    inflight = null
  })
  return inflight
}

/**
 * 校验一个线路 URL 是否在账号下发清单内（播放器据此认弹层带来的
 * 选线，手改地址一律不认、回落默认线路）。命中返回规范化 URL，
 * 未命中 / 清单为空返回空串。
 *
 * 配额分流：目标线已用尽且还有未用尽的线时，改落到第一条未用尽
 * 的线（是默认线则返回空串 = 走默认入口）；全部用尽（后端回落后
 * 标记已清零）或目标线未用尽时按原口径。
 */
export async function validatePlayLine(rawUrl: string): Promise<string> {
  const url = normalizeLineUrl(rawUrl)
  if (!url) return ''
  const { lines, defaultUrl } = await loadPlayLines()
  const target = lines.find((l) => l.url === url)
  if (!target) return ''
  if (target.exhausted) {
    const alt = lines.find((l) => !l.exhausted)
    if (alt) return alt.url === defaultUrl ? '' : alt.url
    // 没有未用尽的线：回落原口径放行，不断播
  }
  if (url === defaultUrl) return ''
  return url
}
