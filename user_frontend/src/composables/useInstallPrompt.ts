import { ref, type Ref } from 'vue'

/**
 * 「保存到桌面＝一个 app」的安装能力：
 * - Android / 桌面 Chrome：beforeinstallprompt 事件暂存，入口点击时调起原生安装提示；
 * - iOS Safari：没有程序化安装 API，只能引导用户走分享菜单「添加到主屏幕」
 *   （UhdInstallRow 负责弹层说明）；
 * - 已以独立窗口（standalone）打开时，安装入口整体隐藏。
 *
 * beforeinstallprompt 可能在应用挂载前就触发，所以监听注册放在
 * captureInstallPrompt() 里，由 main.ts 在入口处调一次（幂等）。
 */

// TS DOM lib 未收录 beforeinstallprompt 的事件形状，这里声明最小子集。
interface BeforeInstallPromptEvent extends Event {
  readonly platforms: string[]
  readonly userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>
  prompt: () => Promise<void>
}

let deferredPrompt: BeforeInstallPromptEvent | null = null
let captured = false

/** 是否拿到了可调起的原生安装提示（响应式，供入口组件决定点击行为） */
const canInstall: Ref<boolean> = ref(false)

/** 在 main.ts 入口调一次：注册 beforeinstallprompt / appinstalled 监听。 */
export function captureInstallPrompt(): void {
  if (captured || typeof window === 'undefined') return
  captured = true
  window.addEventListener('beforeinstallprompt', (event) => {
    // 拦下浏览器默认的迷你信息栏，改由站内「安装应用」入口在用户点击时调起
    event.preventDefault()
    deferredPrompt = event as BeforeInstallPromptEvent
    canInstall.value = true
  })
  window.addEventListener('appinstalled', () => {
    deferredPrompt = null
    canInstall.value = false
  })
}

/** 调起暂存的原生安装提示；没有暂存事件时回 'unavailable'。 */
export async function promptInstall(): Promise<'accepted' | 'dismissed' | 'unavailable'> {
  if (!deferredPrompt) return 'unavailable'
  const event = deferredPrompt
  // 一个暂存事件只能用一次，无论用户选什么都先清空
  deferredPrompt = null
  canInstall.value = false
  try {
    await event.prompt()
    const choice = await event.userChoice
    return choice.outcome
  } catch {
    return 'unavailable'
  }
}

/** 当前是否已经以独立 app 窗口运行（桌面图标启动 / 已安装）。 */
export function isStandalone(): boolean {
  if (typeof window === 'undefined') return false
  if (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) return true
  // iOS Safari 的独立窗口标记（非标准属性）
  return (window.navigator as Navigator & { standalone?: boolean }).standalone === true
}

/** iOS / iPadOS（含桌面 UA 的 iPad）判定：只能走手动添加到主屏幕。 */
export function isIOS(): boolean {
  if (typeof window === 'undefined') return false
  const ua = window.navigator.userAgent || ''
  if (/iphone|ipad|ipod/i.test(ua)) return true
  return window.navigator.platform === 'MacIntel' && window.navigator.maxTouchPoints > 1
}

export function useInstallPrompt() {
  return { canInstall, captureInstallPrompt, promptInstall, isStandalone, isIOS }
}
