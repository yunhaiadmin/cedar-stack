// 【UI 统一 U0】主题 CSS 引入已冻结：新增全量引入必须经设计审查，并同步更新
// scripts/check_frontend_tokens.py 的 MAIN_CSS_ALLOWLIST，否则 CI 直接失败。
import './styles/aurora.css'
import './styles/neo-noir-tokens.css'
import './styles/index.css'
import './styles/mobile.css'
// v4「探险家日志」面板纸色设计系统（PR-2）
import './styles/v4-paper.css'
// UHD 换皮令牌（批 0 地基，《Aetrix × UHDNow 设计说明书 v1》）：只定义 --uhd-* 新变量，
// 不改现有变量的值、未被任何现有页面引用；已按 U0 冻结流程同步登记
// scripts/check_frontend_tokens.py 的 MAIN_CSS_ALLOWLIST。
import './styles/uhd-tokens.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
// 站点品牌（能力：站点与品牌）：站名 / Logo / 主题色 / SEO 由后台配置，启动时读一次。
// 先挂载再拉取，不让一次额外的请求把首屏拖住；拉到之后主题色与标题会自己更新。
import { initBranding } from './composables/useBranding'
// 官网社区渠道（能力：官网社区渠道）：文档外链 / 社区渠道卡片由后台配置，启动时读一次
import { initCommunityLinks } from './composables/useCommunityLinks'
// 「保存到桌面＝一个 app」：beforeinstallprompt 可能赶在挂载前触发，入口处先注册暂存
import { captureInstallPrompt } from './composables/useInstallPrompt'
// chunk 加载失败恢复（与 router.onError 共用唯一实现 utils/chunkRecovery）
import { handleChunkLoadError } from './utils/chunkRecovery'

// iOS Safari 要求页面有 touchstart 监听器才会触发 :active 伪类
// 空函数即可，passive 避免滚动性能问题
document.addEventListener('touchstart', () => {}, { passive: true })

captureInstallPrompt()

// 路由组件的子 chunk 预加载失败不走 router.onError，只走 vite:preloadError：
// 在这里 preventDefault 后交给同一套恢复（计数/闩锁在 chunkRecovery 内统一，
// 与 router.onError 先后到达不会双计预算）。
window.addEventListener('vite:preloadError', (event: Event) => {
  // vite 把失败原因挂在事件的 payload 扩展属性上（非标准类型，源码实锤）；
  // preventDefault 不做的话 vite 会把原错误再抛出来。
  event.preventDefault()
  handleChunkLoadError((event as Event & { payload?: unknown }).payload)
})

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)

app.mount('#app')
void initBranding()
void initCommunityLinks()
