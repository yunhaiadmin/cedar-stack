/**
 * 线路测速页的规则模板与线路分类（单一真源，页面只许从这里取）。
 *
 * 代理 / 直连规则按所选线路的真实域名生成，语法是 Surge 与
 * Clash.Meta 通用的 DOMAIN 规则：同一条规则行两边都能用，只是
 * Clash.Meta 在配置里以 YAML 列表项出现（行首带「- 」）。
 * 提示文案按线路类型二选一：国际线路优先给代理分流提示，
 * 国内优化线路优先给直连提示。
 */

export type LineKind = 'intl' | 'domestic'

/** 测速文件档位（MiB），与 EA 的 /speedtest/bin 档位一一对应。 */
export const SPEEDTEST_SIZES_MB = [32, 64, 100] as const
export type SpeedtestSizeMb = (typeof SPEEDTEST_SIZES_MB)[number]

/**
 * 线路类型判定（展示口径）：名称/域名带国内指向词的算国内优化，
 * 其余算国际。只影响提示文案与规则的推荐顺序，不影响测速本身
 * （测速永远打所选线路的真实入口）。
 */
const DOMESTIC_RE = /国内|大陆|直连|优化|china|cn\b/i

export function lineKindOf(name: string, host: string): LineKind {
  return DOMESTIC_RE.test(`${name} ${host}`) ? 'domestic' : 'intl'
}

/** 代理规则提示条文案（按线路类型切换）。 */
export function proxyHintFor(kind: LineKind): string {
  return kind === 'intl'
    ? '当前选择的是国际相关线路：请按下方代理 / 直连规则分流，测速结果才更接近实际观看体验。'
    : '当前选择的是国内优化线路：建议按下方直连规则分流，测速结果才更接近实际观看体验。'
}

/** 代理规则：该域名走代理（PROXY 为占位策略名，导入后按自己的策略组改名）。 */
export function buildProxyRules(host: string): string {
  return [
    '# Surge',
    `DOMAIN,${host},PROXY`,
    '# Clash.Meta',
    `- DOMAIN,${host},PROXY`,
  ].join('\n')
}

/** 直连规则：该域名走直连。 */
export function buildDirectRules(host: string): string {
  return [
    '# Surge',
    `DOMAIN,${host},DIRECT`,
    '# Clash.Meta',
    `- DOMAIN,${host},DIRECT`,
  ].join('\n')
}

/** 已下载字节展示（友站口径：0 Bytes / 12.3 MB / 1.2 GB）。 */
export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0 Bytes'
  if (n < 1024) return `${Math.floor(n)} Bytes`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${(n / 1024 ** 3).toFixed(2)} GB`
}
