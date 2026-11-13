import { createRouter, createWebHistory, type RouteLocationNormalizedLoaded } from 'vue-router'
import { useUserStore } from '@/stores/user'
// 标题里的站名来自「站点与品牌」能力（未配置时用默认值）
import { pageTitle } from '@/composables/useBranding'
// v4 面板特性开关（VITE_PANEL_V4_ENABLED=0 时回退老首页）
import { isPanelV4Enabled } from '@/composables/usePanelApi'
// chunk 加载失败恢复：判定 / 计数 / 重试横条的唯一实现在 utils/chunkRecovery，
// 路由错误、vite 预加载事件（main.ts）、视图内异步组件（RootView）三处共用。
import { handleChunkLoadError, resetChunkRecoveryState } from '@/utils/chunkRecovery'

const panelV4 = isPanelV4Enabled()

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  // R28：处理站内 hash 跳转（锚点滚动）
  scrollBehavior(to, _from, savedPosition) {
    if (savedPosition) return savedPosition
    if (to.hash) {
      const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      return { el: to.hash, behavior: reduce ? 'auto' : 'smooth' }
    }
    return undefined
  },
  routes: [
    {
      path: '/',
      name: 'home',
      // 根路径由 RootView 渲染官网 LandingView（URL 保持 '/'）；
      // 已登录用户直接打开根路径同样停在这里（友站既定设计，
      // 2026-11-13 分场景定版：进站停宣传页、不跳转；「落概览」只对
      // 登录动作与已登录访问 /login 生效，见下方守卫与 LoginView）。
      // 旧发现页 DiscoveryView 已于 2026-11-13 连根删除。
      component: () => import('@/views/RootView.vue'),
      meta: { title: '欢迎' },
    },
    // v4 面板：收藏家 / 服务器（需要登录，panel 接口全部要鉴权）
    ...(panelV4
      ? [
          {
            path: '/collector',
            name: 'collector',
            component: () => import('@/views/panel/CollectorView.vue'),
            meta: { title: '收藏家', requiresAuth: true },
          },
          // 我的收藏：收藏列表页（老 /favorites 地址复活承接；入口在账号设置，底栏不占位）
          {
            path: '/favorites',
            name: 'favorites',
            component: () => import('@/views/panel/FavoritesView.vue'),
            meta: { title: '我的收藏', requiresAuth: true },
          },
          // UHD 换皮批 3（片库面）：/library 片库首页 + 电影/剧集分类页 + 搜索结果页。
          // 2026-11-13 登录落点修复：旧 '/' 发现页（DiscoveryView）已删除；
          // 登录默认落点为用户中心概览 /game/account（同日用户改定），
          // 本页经底坞/顶栏「首页」进入。页面自带双胶囊顶栏与手机玻璃底坞，
          // App.vue 在这些路由下不渲染全局顶栏/PanelTabbar。数据全走现行
          // Emby 协议口与门户口径，不新造接口；海报卡只进详情、不挂播放入口。
          {
            path: '/library',
            name: 'library',
            component: () => import('@/views/panel/StreamHomeView.vue'),
            meta: { title: '片库', requiresAuth: true },
          },
          {
            path: '/library/movies',
            name: 'library-movies',
            component: () => import('@/views/panel/LibraryBrowseView.vue'),
            meta: { title: '电影', requiresAuth: true },
          },
          {
            path: '/library/series',
            name: 'library-series',
            component: () => import('@/views/panel/LibraryBrowseView.vue'),
            meta: { title: '剧集', requiresAuth: true },
          },
          {
            path: '/search',
            name: 'search',
            component: () => import('@/views/panel/SearchView.vue'),
            meta: { title: '搜索', requiresAuth: true },
          },
          // 合集：货架页 + 详情页（只读用户口；不在底栏占位，入口在发现页合集区段）
          {
            path: '/collections',
            name: 'collections',
            component: () => import('@/views/panel/CollectionShelfView.vue'),
            meta: { title: '合集', requiresAuth: true },
          },
          {
            path: '/collections/:id',
            name: 'collection-detail',
            component: () => import('@/views/panel/CollectionView.vue'),
            meta: { title: '合集', requiresAuth: true },
          },
          // 条目详情：只做信息 + 季集齐全度 + 缺集求补（播放仍走第三方
          // 播放器，不放播放入口）；入口在我的收藏的剧集卡片
          {
            path: '/media/:id',
            name: 'media-detail',
            component: () => import('@/views/panel/MediaDetailView.vue'),
            meta: { title: '详情', requiresAuth: true },
          },
          // 演职人员页：详情页演职横排与收藏页「演职人员」页签的去向；
          // :key 是人员键（tmdb:<id> 或 name:<归一化姓名>，后端负责解析升级）
          {
            path: '/person/:key',
            name: 'person-detail',
            component: () => import('@/views/panel/PersonView.vue'),
            meta: { title: '演职人员', requiresAuth: true },
          },
          {
            path: '/server',
            name: 'server',
            component: () => import('@/views/panel/ServerView.vue'),
            meta: { title: '服务器', requiresAuth: true },
          },
          // v4 游戏化第一批：营地设施（需要登录）
          // 求片反馈（2026-11-13 合并页）：许愿池＋求救信号合并为一页，
          // 三页签（发起请求/我的请求/请求广场）×五类型（影片求片/电影追新/剧集追新/修正信息/其他反馈）；
          // 新页跟随用户外观，不进 V4_PATHS。
          {
            path: '/game/requests',
            name: 'game-requests',
            component: () => import('@/views/game/RequestFeedbackView.vue'),
            meta: { title: '求片反馈', requiresAuth: true },
          },
          // 旧地址重定向：/game/wishlist → 新页（透传 query；带 ?q= 时新页
          // 接住预填搜索）；/game/signal → 新页「我的请求」页签。
          {
            path: '/game/wishlist',
            redirect: (to: RouteLocationNormalizedLoaded) => ({ path: '/game/requests', query: to.query }),
          },
          {
            path: '/game/signal',
            redirect: (to: RouteLocationNormalizedLoaded) => ({ path: '/game/requests', query: { ...to.query, tab: 'mine' } }),
          },
          {
            path: '/game/ledger',
            name: 'game-ledger',
            component: () => import('@/views/game/LedgerView.vue'),
            meta: { title: '能量明细', requiresAuth: true },
          },
          // 代理中心（2026-11-06 代理分销）：入口只在账号设置里、且仅代理可见，不占导航
          {
            path: '/game/agent',
            name: 'game-agent',
            component: () => import('@/views/game/AgentCenterView.vue'),
            meta: { title: '代理中心', requiresAuth: true },
          },
          {
            path: '/game/operator',
            name: 'game-operator',
            component: () => import('@/views/game/OperatorView.vue'),
            meta: { title: '播放账号', requiresAuth: true },
          },
          // v4 游戏化：「我的」个人中心（身份卡 / 状态总览 / 分组入口行）；
          // 设置类功能在子页 /game/account/settings，会员管理在 /game/account/member
          {
            path: '/game/account',
            name: 'game-account',
            component: () => import('@/views/game/PersonalCenterView.vue'),
            meta: { title: '我的', requiresAuth: true },
          },
          {
            path: '/game/account/settings',
            name: 'game-account-settings',
            component: () => import('@/views/game/AccountSettingsView.vue'),
            meta: { title: '账号设置', requiresAuth: true },
          },
          {
            path: '/game/account/member',
            name: 'game-account-member',
            component: () => import('@/views/game/MemberView.vue'),
            meta: { title: '我的会员', requiresAuth: true },
          },
          // 订阅服务：按 UHDNow 订阅逻辑重做的购买页（深色 app 单列流＋吸底结算条）
          {
            path: '/game/account/subscribe',
            name: 'game-account-subscribe',
            component: () => import('@/views/game/SubscriptionPlansView.vue'),
            meta: { title: '订阅服务', requiresAuth: true },
          },
          // 「我的」菜单页：用户中心底坞双页签之一（全部功能列表行，一个不丢）
          {
            path: '/game/account/menu',
            name: 'game-account-menu',
            component: () => import('@/views/game/AccountMenuView.vue'),
            meta: { title: '我的', requiresAuth: true },
          },
          // 安装应用整页（2026-11-13）：服务器地址卡＋6 平台客户端下载＋
          // 分步教程；入口在「我的 · 日常与帮助」组，PWA 安装动作在页内保留
          {
            path: '/game/install',
            name: 'game-install',
            component: () => import('@/views/game/InstallAppView.vue'),
            meta: { title: '安装应用', requiresAuth: true },
          },
          // 线路测速（用户中心 · 帮助组）：线路清单只读现行配置下发
          // （账号卡 realms 地址＋专属线路），浏览器直连各入口计时。
          // 不进 V4_PATHS——新页跟随用户外观三态，不钉浅色。
          {
            path: '/game/speedtest',
            name: 'game-speedtest',
            component: () => import('@/views/game/LineSpeedtestView.vue'),
            meta: { title: '线路测速', requiresAuth: true },
          },
          // 团队管理（2026-11-13 照 UHDNow 团队页）：账户组入口
          {
            path: '/game/account/team',
            name: 'game-account-team',
            component: () => import('@/views/game/TeamView.vue'),
            meta: { title: '团队管理', requiresAuth: true },
          },
        ]
      : []),
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { title: '登录' },
    },
    // TG 一键登录：后端 tg_bot/login_token.py 生成 {site_base_url}/tg-login?token=xxx，
    // 路由缺失会导致 404（2026-10-10 审计发现）
    {
      path: '/tg-login',
      name: 'tg-login',
      component: () => import('@/views/TgLoginView.vue'),
      meta: { title: 'Telegram 快捷登录' },
    },

    // 探险日志 Hub（/game）整页已删（2026-11-13 页面删减）：旧书签/外链落首页，
    // 不留死链；各设施子页保留不动，入口走头像菜单/左栏/地图针。
    {
      path: '/game',
      redirect: '/',
    },
    // 注册后 TG 绑定引导页：仅注册流程进入，未绑定且管理员开启时展示
    {
      path: '/tg-bind',
      name: 'tg-bind-guide',
      component: () => import('@/views/TgBindGuideView.vue'),
      meta: { title: '绑定 Telegram', requiresAuth: true },
    },
    // 想看：已合并进求片反馈（/game/requests）——库内片「想看」动作在
    // 求片表单的已在库候选卡上。旧地址/收藏带 query 跳过去，
    // 不留重复页（求片反馈页接住 ?q= 预填搜索）。
    {
      path: '/want',
      redirect: (to) => ({ path: '/game/requests', query: to.query }),
    },
    // 求片登记旧地址：功能已并入求片反馈，旧链接/收藏跳过去（带上 query），
    // 不留重复旧页。
    {
      path: '/store-requests',
      redirect: (to) => ({ path: '/game/requests', query: to.query }),
    },
    // 隐私政策：公开页（Google OAuth 验证要求挂在本站域名下），
    // 不带 requiresAuth，路由守卫只拦 requiresAuth 路由，未登录也能直接打开。
    {
      path: '/privacy',
      name: 'privacy',
      component: () => import('@/views/PrivacyView.vue'),
      meta: { title: '隐私政策' },
    },
    // UHD 换皮批 1（营销面）：官网定价，公开页不带 requiresAuth，自带悬浮胶囊导航，
    // App.vue 按路由名（pricing）不渲染全局顶栏/底坞。
    // 官网首页已按 2026-11-13 定版绑到根路径 '/'（RootView 未登录分支渲染
    // LandingView），/welcome 只留旧链接/书签重定向，不再单独渲染。
    {
      path: '/welcome',
      redirect: '/',
    },
    {
      path: '/pricing',
      name: 'pricing',
      component: () => import('@/views/marketing/PricingView.vue'),
      meta: { title: '定价' },
    },
    // ==================== 媒体详情与播放（v2.42.5：媒体库/搜索浏览页已删；
    // 2026-10-13 旧页面清理：/profile、/media/:id 两路由已删） ====================
    // 老地址 /profile、/media、/history 的书签与外部链接：
    // 这些入口不再有对应页面，全部落到兜底 404 页（页面里会给「回首页」的出口）。
    // 例外：/media/:id 已按新口径复活为条目详情页（见上 panelV4 路由：
    // 只做信息 + 季集 + 缺集求补，不恢复浏览/播放）；
    // /library 与 /search 已由 UHD 换皮批 3 按片库面口径复活为真实页面
    // （见上 panelV4 路由：海报墙 + 顶栏搜索，只浏览、不做网页播放）。
    // 注意：/profile 的承接入口是 /game/account（账号设置设施）。
    // 网页播放器（2026-11-13 用户改规矩立项）：全屏播放页，自带覆盖层，
    // 不挂 panel 布局。契约：/play/:id?ms=<MediaSourceId>&at=<起始秒数>，
    // ms/at 均可选，批 3 播放前弹层的「开始播放」按此契约跳转。
    {
      path: '/play/:id',
      name: 'web-player',
      component: () => import('@/views/WebPlayerView.vue'),
      meta: { title: '播放', requiresAuth: true },
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/NotFoundView.vue'),
      meta: { title: '页面未找到' },
    },
  ],
})

// 路由守卫
let initialized = false

router.beforeEach(async (to) => {
  const userStore = useUserStore()

  if (!initialized) {
    userStore.init()
    initialized = true
  }

  // 落点分场景（2026-11-13 21:22 用户亲定最终版，照友站 UHDNow）：
  // ① 登录动作成功 → 概览 /game/account（由 LoginView 的 postAuthTarget
  //    在提交成功时直落，不经本守卫转发）；
  // ② 已登录直接打开 '/'（含冷启动）→ 停官网宣传页，本守卫不跳转
  //    （RootView 无条件渲染 LandingView，页内「进入后台」钮进概览）；
  // ③ 已登录访问 /login → 下方守卫直落概览。
  // 根路径只剩官网一面，标题恒为「欢迎」
  const metaTitle = to.name === 'home' ? '欢迎' : to.meta.title
  if (metaTitle) {
    document.title = pageTitle(metaTitle as string | undefined)
  }

  // 未登录访问受保护页面 → 跳转登录页并记录回跳地址
  if (to.meta.requiresAuth) {
    const hasToken = localStorage.getItem('access_token')
    if (!hasToken && !userStore.isLoggedIn) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }
    // 有 token 但没有用户信息（刷新页面），拉取一次
    if (!userStore.user) {
      try {
        await userStore.fetchUser()
      } catch {
        return { name: 'login', query: { redirect: to.fullPath } }
      }
    }
  }

  // 已登录访问登录页 → 直落概览 /game/account（不绕根路径转发：
  // 根路径已按分场景口径停宣传页，不再承接落点中转）。
  // panelV4 关闭时 /game/account 路由不存在，回退 home 降级。
  if (to.name === 'login' && userStore.isLoggedIn) {
    return panelV4 ? { path: '/game/account', replace: true } : { name: 'home' }
  }

  return true
})

// v4 面板第一版只做纸色浅色：在 v4 路由下强制 light，离开时恢复
// （v4 视图自带 .v4-scope 纸色变量，这里只处理 html data-theme 与浏览器工具栏色）
// 例外（2026-11-13 UHD 1:1 跟进）：/game/account（概览）已自带主题跟随，
// 不再钉 light——本集合摘除该路径后守卫对它走 restoreUserTheme；
// /game/account/menu（我的）本就不在集合内，同样跟随用户外观。
// /game/account/settings、/game/account/member 等其余账号页仍是 v4 纸面，继续钉 light。
// 例外（2026-11-13 二级页重做组 A）：/server 与 /game/operator 已换 UHD 布局，
// 一并摘除——/server 页内自带三态主题跟随；/game/operator 在 UhdAccountLayout
// 办事面壳内（壳为浅色办事面，与同壳的订阅页未钉 light 先例一致），不再由守卫钉 light。
export const V4_PATHS = new Set(['/', '/collector', '/favorites', '/collections', '/want', '/store-requests', '/game/ledger', '/game/account/settings', '/game/account/member'])
// 参数路由前缀：合集详情是 /collections/:id，afterEach 拿到的是实例路径
// （如 /collections/123），静态集合永远匹配不上，须按前缀另判；
// /collections/ 下目前只有详情这一条路由，前缀不会误伤非 v4 页。
// /media/ 同理：条目详情 /media/:id 也是 v4 纸色页，必须强制 light，
// 否则深色偏好下 --au-* 令牌是暗值，纸面页上会出现黑条/黑行。
// /person/ 同理：演职人员页 /person/:key 与详情、收藏同属纸面页。
const V4_PATH_PREFIXES = ['/collections/', '/media/', '/person/']
// 底部坞由 App.vue 全局常驻渲染（PanelTabbar，≤768px 唯一一套，
// 桌面 ≥769px 由 CSS 隐藏）；各视图模板不再自带 <PanelTabbar/>。
function restoreUserTheme() {
  // 从 useTheme.ts 同 key 读用户偏好（默认 dark），恢复 data-theme
  let pref = 'dark'
  try {
    const s = localStorage.getItem('aetrix-theme')
    if (s === 'light' || s === 'dark' || s === 'system') pref = s
  } catch { /* ignore */ }
  const html = document.documentElement
  const light =
    pref === 'light' ||
    (pref === 'system' &&
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-color-scheme: light)').matches)
  if (light) html.setAttribute('data-theme', 'light')
  else html.removeAttribute('data-theme')
  // 浏览器工具栏色跟回用户主题（与 useTheme.ts applyTheme 一致）
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', light ? '#f5f1ea' : '#0c0a09')
}
router.afterEach((to) => {
  if (!panelV4) return
  // 尾斜杠规范化：/game/ 与 /game 是同一页；根路径 '/' 去掉斜杠会变空串，要兜回 '/'
  const v4Path = to.path.replace(/\/+$/, '') || '/'
  // 根路径完成导航的恒为官网宣传页（未登录与已登录进站都停这里，
  // 分场景定版后不再有「已登录被送走」的前提），跟随用户主题，不钉纸色。
  const rootIsLanding = v4Path === '/'
  if ((V4_PATHS.has(v4Path) && !rootIsLanding) || V4_PATH_PREFIXES.some((p) => v4Path.startsWith(p))) {
    document.documentElement.setAttribute('data-theme', 'light')
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', '#f5f1ea')
  } else {
    restoreUserTheme()
  }
})

// SPA 部署竞态兜底：动态 import 失败（chunk 已被新部署替换）时交给
// utils/chunkRecovery 统一恢复（预算内自动刷新、超预算出重试横条）；
// 同一轮失败可能与 main.ts 的 vite:preloadError 重复到达，防重复计数
// 在恢复实现内用闩锁统一处理，这里不另写判定与计数。
router.onError((err: unknown) => {
  handleChunkLoadError(err)
})
router.afterEach((_to, _from, failure) => {
  // 只认成功导航：vue-router 对失败导航同样触发 afterEach，失败时清计数
  // 会把刚累积的刷新预算误清掉（见 utils/chunkRecovery 文件头）。
  if (!failure) resetChunkRecoveryState()
})

// 关键路由预取：用户登录后大概率会去我的（/game/account），空闲时预加载对应 chunk
//（2026-10-13 旧页面清理：ProfileView 已删；2026-11-06 个人中心重做：/game/account 改指 PersonalCenterView）
if (typeof requestIdleCallback !== 'undefined') {
  requestIdleCallback(() => {
    import('@/views/game/PersonalCenterView.vue')
  }, { timeout: 3000 })
}

export default router
