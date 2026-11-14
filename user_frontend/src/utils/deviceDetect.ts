/**
 * 当前设备与浏览器识别（安装应用页用）：只读 navigator.userAgent，
 * 识别不了就回通用口径（platform/browser 为 'other'），调用方据此
 * 显示「这台设备 / 当前浏览器」，不许在页面里写死某个机型或浏览器。
 *
 * 判定顺序要点：
 * - 浏览器先认 Edge（UA 里同时带 Chrome/Safari 字样）、再认 Chrome
 *   （含 iOS 的 CriOS）、最后才是 Safari；
 * - iPad 在桌面模式下 UA 是 Macintosh，只能靠触摸点数补判为 iOS。
 */

export type DevicePlatform = 'ios' | 'android' | 'windows' | 'mac' | 'other'
export type BrowserKind = 'safari' | 'chrome' | 'edge' | 'other'

export interface DeviceInfo {
  platform: DevicePlatform
  /** 「当前：＜设备＞ · ＜浏览器＞」里的设备名 */
  deviceLabel: string
  browser: BrowserKind
  browserLabel: string
}

const DEVICE_LABELS: Record<DevicePlatform, string> = {
  ios: 'iPhone / iPad',
  android: 'Android 手机',
  windows: 'Windows 电脑',
  mac: 'Mac',
  other: '这台设备',
}

const BROWSER_LABELS: Record<BrowserKind, string> = {
  safari: 'Safari',
  chrome: 'Chrome',
  edge: 'Edge',
  other: '当前浏览器',
}

export function detectDevice(ua?: string, maxTouchPoints?: number): DeviceInfo {
  const agent = ua ?? (typeof navigator !== 'undefined' ? navigator.userAgent || '' : '')
  const touch = maxTouchPoints ?? (typeof navigator !== 'undefined' ? navigator.maxTouchPoints : 0)

  let platform: DevicePlatform = 'other'
  if (/iphone|ipad|ipod/i.test(agent)) platform = 'ios'
  else if (/android/i.test(agent)) platform = 'android'
  else if (/windows nt/i.test(agent)) platform = 'windows'
  else if (/macintosh|mac os x/i.test(agent)) platform = touch > 1 ? 'ios' : 'mac'

  let browser: BrowserKind = 'other'
  if (/edg(a|ios)?\//i.test(agent)) browser = 'edge'
  else if (/chrome\//i.test(agent) || /crios\//i.test(agent)) browser = 'chrome'
  else if (/safari\//i.test(agent)) browser = 'safari'

  return {
    platform,
    deviceLabel: DEVICE_LABELS[platform],
    browser,
    browserLabel: BROWSER_LABELS[browser],
  }
}

/** 各平台上可用的浏览器分段（安装步骤按浏览器分套）。 */
export function browsersForPlatform(platform: DevicePlatform): BrowserKind[] {
  switch (platform) {
    case 'ios':
    case 'mac':
      return ['safari', 'chrome', 'edge']
    case 'android':
    case 'windows':
      return ['chrome', 'edge']
    default:
      return ['safari', 'chrome', 'edge']
  }
}

export { BROWSER_LABELS }
