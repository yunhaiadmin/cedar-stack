/**
 * 播放线路清单（弹层选线 / 播放器校验共用，与线路测速页 #126 同一真源）：
 *   - 播放线路：账号卡 GET /api/user/emby/server 的 realms[]（每项
 *     name/base_url；没查看权限的服 base_url 为空，自动滤掉）；
 *     顶层 base_url 是当前服（默认线路），realms 没带时用它补一行。
 *   - 专属线路：GET /api/user/exclusive-lines（只有够格用户才下发，
 *     无资格回空列表）。
 * 两组按规范化 URL 去重（去尾斜杠）。线路只是「入口地址」：选线后
 * 本次播放的 EA 请求（PlaybackInfo / 会话上报）改走该入口，流地址
 * 仍由服务端按现行策略下发，前端不拼、不改签名。
 */
import { embyApi } from '@/api'
import { getMyExclusiveLines } from '@/api/user'

export interface PlayLine {
  key: string
  name: string
  url: string
  kind: 'realm' | 'exclusive'
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
    const push = (kind: PlayLine['kind'], name: string, rawUrl: string) => {
      const url = normalizeLineUrl(rawUrl)
      if (!url || seen.has(url)) return
      seen.add(url)
      lines.push({ key: `${kind}:${url}`, kind, name: name || hostOf(url), url })
    }
    try {
      const card = await embyApi.getAccountCard()
      defaultUrl = normalizeLineUrl(card.base_url ?? '')
      for (const r of card.realms ?? []) {
        push('realm', r.name, r.base_url ?? '')
      }
      if (defaultUrl && !seen.has(defaultUrl)) {
        push('realm', card.realm_name || card.server_name || '主线路', defaultUrl)
      }
    } catch { /* 账号卡失败：专属线路还能单独成列；都空则调用方隐藏选线 */ }
    const mine = await getMyExclusiveLines()
    for (const l of mine) {
      push('exclusive', l.name, l.entry_url ?? '')
    }
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
 */
export async function validatePlayLine(rawUrl: string): Promise<string> {
  const url = normalizeLineUrl(rawUrl)
  if (!url) return ''
  const { lines, defaultUrl } = await loadPlayLines()
  if (url === defaultUrl) return ''
  return lines.some((l) => l.url === url) ? url : ''
}
