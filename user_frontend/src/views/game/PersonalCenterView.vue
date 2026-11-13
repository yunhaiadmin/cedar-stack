<template>
  <div class="ov-page" :class="{ 'ov-dark': themeResolved === 'dark' }">
    <div class="ov-inner">
      <!-- 页头行：品牌字标＋站名，右「网页观影」彩虹边框胶囊（→片库面 /library） -->
      <header class="ov-topbar">
        <div class="ov-brand">
          <img v-if="branding.logo_url" class="ov-brand__logo" :src="branding.logo_url" :alt="branding.site_name" />
          <span v-else class="ov-brand__mark" aria-hidden="true">{{ brandInitial }}</span>
          <span class="ov-brand__name">{{ branding.site_name }}</span>
        </div>
        <RouterLink to="/library" class="watch-pill">
          <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
            <defs>
              <linearGradient id="wp-g-ov" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0" stop-color="#2f8fe0" />
                <stop offset="0.5" stop-color="#e8766f" />
                <stop offset="1" stop-color="#ecd04f" />
              </linearGradient>
            </defs>
            <path d="M7 4.2v15.6c0 .8.9 1.3 1.6.9l11.6-7.8c.6-.4.6-1.3 0-1.7L8.6 3.3C7.9 2.9 7 3.4 7 4.2Z" fill="url(#wp-g-ov)" />
          </svg>
          网页观影
        </RouterLink>
      </header>

      <!-- 会员卡：白底（深色深炭）＋右上斜向粉彩渐变与细斜纹（纯 CSS 自绘） -->
      <section class="ov-card" aria-label="会员卡">
        <div class="ov-card__shine" aria-hidden="true"></div>
        <div class="ov-card__head">
          <span class="ov-card__brand">{{ branding.site_name }}</span>
          <svg class="ov-card__play" width="31" height="31" viewBox="0 0 24 24" aria-hidden="true">
            <defs>
              <linearGradient id="card-play-g" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0" stop-color="#2f8fe0" />
                <stop offset="0.28" stop-color="#6f7ad8" />
                <stop offset="0.48" stop-color="#c46c90" />
                <stop offset="0.62" stop-color="#e8766f" />
                <stop offset="0.76" stop-color="#f2a45a" />
                <stop offset="0.88" stop-color="#ecd04f" />
                <stop offset="1" stop-color="#39b3ec" />
              </linearGradient>
            </defs>
            <path d="M7 4.2v15.6c0 .8.9 1.3 1.6.9l11.6-7.8c.6-.4.6-1.3 0-1.7L8.6 3.3C7.9 2.9 7 3.4 7 4.2Z" fill="url(#card-play-g)" />
          </svg>
        </div>
        <div class="ov-card__foot">
          <h1 class="ov-card__plan">{{ memberPlanName }}</h1>
          <p class="ov-card__user">{{ memberUserLine }}</p>
        </div>
      </section>

      <!-- 卡下信息行：左有效期（真实到期日），右端为身份徽记（自动续费本站无此业务，省略） -->
      <div class="ov-expiry">
        <span v-if="expiryText">有效期至 {{ expiryText }}</span>
        <span v-else>{{ memberStatusText }}</span>
        <span v-if="whitelistIdentity" class="ov-expiry__note">{{ whitelistIdentity.title }} · {{ whitelistIdentity.label }}</span>
        <span v-else-if="memberNote" class="ov-expiry__note">{{ memberNote }}</span>
      </div>

      <!-- 状态预警（活力不足/临期；数据复用本页已加载状态，不触发时整块不渲染） -->
      <StatusAlertBar
        :is-free-realm="isFreeRealm"
        :vitality="vitality"
        :welfare="welfare"
        :active-sub="activeSub"
      />

      <!-- 大数字区：剩余天数（真实口径映射原站的剩余流量位） -->
      <section v-if="quota" class="ov-quota">
        <p class="ov-quota__label">{{ quota.label }}</p>
        <p class="ov-quota__big">{{ quota.days }}<span class="ov-quota__unit">天</span></p>
        <template v-if="quota.hasPeriod">
          <p class="ov-quota__remain">剩余 {{ quota.remainPct }}%</p>
          <div class="ov-bar" role="img" :aria-label="`本周期已用 ${quota.usedPct}%`">
            <div class="ov-bar__fill" :style="{ width: `${quota.remainPct}%` }"></div>
          </div>
          <div class="ov-quota__meta">
            <span>已用 {{ quota.usedDays }} / 共 {{ quota.totalDays }} 天</span>
            <span>{{ quota.endText }}</span>
          </div>
        </template>
        <p v-else class="ov-quota__meta-line">{{ quota.endText }}</p>
      </section>
      <section v-else class="ov-quota">
        <p class="ov-quota__label">{{ isFreeRealm ? '公益资格' : '订阅' }}</p>
        <p class="ov-quota__big ov-quota__big--empty">未开通</p>
        <p class="ov-quota__meta-line">{{ isFreeRealm ? '开通公益资格后即可观影' : '开通订阅后即可观影' }}</p>
      </section>

      <!-- 流量区加载骨架：数据回来前先占位（app 口径，不许空白干等、
           不许内容闪入顶动布局）；未启用时骨架消失、整段不渲染 -->
      <section v-if="trafficLoading" class="ov-quota" aria-hidden="true">
        <div class="ov-skel ov-skel--label"></div>
        <div class="ov-skel ov-skel--big"></div>
        <div class="ov-skel ov-skel--remain"></div>
        <div class="ov-skel ov-skel--bar"></div>
        <div class="ov-skel ov-skel--meta"></div>
      </section>

      <!-- 流量区：套餐含每月流量额度时才有（后端 enabled=false 或
           接口拿不到数据，整段不渲染，绝不拿 0 额度吓人） -->
      <section v-else-if="traffic?.enabled" class="ov-quota">
        <p class="ov-quota__label">本月剩余流量</p>
        <p class="ov-quota__big">{{ trafficRemainGb }}<span class="ov-quota__unit">GB</span></p>
        <p class="ov-quota__remain">剩余 {{ trafficRemainPct }}%</p>
        <div class="ov-bar" role="img" :aria-label="`本月流量已用 ${traffic.used_pct}%`">
          <div class="ov-bar__fill" :style="{ width: `${trafficRemainPct}%` }"></div>
        </div>
        <div class="ov-quota__meta">
          <span>已用 {{ trafficUsedText }} / 共 {{ trafficQuotaText }}</span>
          <span>{{ trafficResetText }}</span>
        </div>
        <p v-if="traffic.exhausted" class="ov-quota__meta-line">
          本月流量已用完，可以购买流量继续用（本期不影响播放）。
        </p>
        <div class="ov-actions ov-actions--traffic">
          <button type="button" class="ov-btn" @click="trafficModalOpen = true">购买流量</button>
        </div>
      </section>

      <!-- 双按钮：主钮彩虹边框粉彩胶囊，次钮白胶囊描边 -->
      <div class="ov-actions">
        <RouterLink class="ov-btn ov-btn--primary" to="/game/account/subscribe">续费订阅</RouterLink>
        <RouterLink class="ov-btn" to="/game/account/member">我的会员</RouterLink>
      </div>

      <!-- Emby 段：图标＋两行＋chevron（弹幕 API 本站无此业务，整项省略） -->
      <section class="ov-sec">
        <div class="ov-sec__head">
          <h2 class="ov-sec__title">Emby</h2>
        </div>
        <button type="button" class="ov-irow" @click="linesSheetOpen = true">
          <MonitorPlay :size="25" class="ov-irow__icon" aria-hidden="true" />
          <span class="ov-irow__main">
            <span class="ov-irow__name">服务器地址</span>
            <span class="ov-irow__sub">{{ serverAddressSub }}</span>
          </span>
          <ChevronRight :size="18" class="ov-row__chev" aria-hidden="true" />
        </button>
        <RouterLink
          v-if="driveRowVisible"
          class="ov-irow"
          :to="{ path: '/game/account/settings', hash: '#my-drive' }"
        >
          <HardDrive :size="25" class="ov-irow__icon" aria-hidden="true" />
          <span class="ov-irow__main">
            <span class="ov-irow__name">我的网盘</span>
            <span class="ov-irow__sub">{{ driveMeta }}</span>
          </span>
          <ChevronRight :size="18" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
        <button
          v-if="myLines.length"
          type="button"
          class="ov-irow ov-irow--btn"
          :aria-expanded="linesOpen"
          @click="linesOpen = !linesOpen"
        >
          <Route :size="25" class="ov-irow__icon" aria-hidden="true" />
          <span class="ov-irow__main">
            <span class="ov-irow__name">我的专属线路</span>
            <span class="ov-irow__sub">{{ myLines.length }} 条可用 · 点开查看与复制地址</span>
          </span>
          <ChevronRight :size="18" class="ov-row__chev" :class="{ 'ov-row__chev--open': linesOpen }" aria-hidden="true" />
        </button>
        <div v-if="myLines.length && linesOpen" class="ov-lines">
          <div v-for="line in myLines" :key="line.entry_url" class="ov-line-row">
            <span class="ov-line-main">
              <span class="ov-line-name">{{ line.name }}</span>
              <span class="ov-line-url">{{ line.entry_url }}</span>
            </span>
            <button type="button" class="ov-copy-btn" @click="copyLineUrl(line.entry_url)">复制地址</button>
          </div>
          <p class="ov-line-note">在播放器里添加服务器时填这些地址，就走你的专属线路进服。</p>
        </div>
      </section>

      <!-- 设备段：数据源＝用户侧设备接口（deviceApi.mine），拿不到整段不渲染 -->
      <section v-if="devicesData" class="ov-sec">
        <div class="ov-sec__head">
          <h2 class="ov-sec__title">设备</h2>
          <RouterLink class="ov-sec__more" to="/game/account/settings">
            {{ devicesData.count }} / {{ devicesData.limit }} 台 · 管理
          </RouterLink>
        </div>
        <div v-for="d in previewDevices" :key="d.device_id" class="ov-drow">
          <component :is="deviceIcon(d)" :size="24" class="ov-drow__icon" aria-hidden="true" />
          <span class="ov-drow__name">{{ deviceLabel(d) }}</span>
          <span class="ov-drow__status" :class="{ 'ov-drow__status--on': d.is_online_recent }">
            {{ d.is_online_recent ? '在线' : relTime(d.last_seen_at) }}
          </span>
        </div>
      </section>

      <!-- 常用段：纯文字行＋右值/chevron -->
      <section class="ov-sec">
        <div class="ov-sec__head">
          <h2 class="ov-sec__title">常用</h2>
        </div>
        <!-- 能量余额行已随商店删除＋能量货币下线摘除；邀请行先占位：
             邀请页未建（等钱包落地），暂不挂任何链接，落地后再接 -->
        <div class="ov-trow">
          <span class="ov-trow__name">邀请好友</span>
        </div>
      </section>

    </div>

    <!-- 购流量弹窗（时间换流量真成交；余额购买页签等钱包上线） -->
    <TrafficPurchaseModal
      v-model:open="trafficModalOpen"
      :traffic="traffic"
      :options="traffic?.exchange_options ?? []"
      @exchanged="onTrafficExchanged"
    />

    <!-- Emby 服务线路弹层：服务器地址行点开（2026-11-13 形态对齐，不再跳播放账号页） -->
    <ServerLinesSheet
      :open="linesSheetOpen"
      :card="accountCard"
      :dark="themeResolved === 'dark'"
      @close="linesSheetOpen = false"
    />
  </div>
</template>

<script setup lang="ts">
/**
 * 概览（/game/account）：2026-11-13 按 UHDNow 原站用户中心首页 1:1 返工——
 * 页头（品牌＋网页观影胶囊）＋会员卡（斜向粉彩渐变＋细斜纹）＋有效期行
 * ＋大数字区（剩余天数映射）＋双按钮＋Emby/设备/常用平面段，页面到
 * 常用段结束；「我的权益」「账号」两段与退出登录按钮已按用户
 * 2026-11-13 令摘除（友站概览没有这三段）——我的会员/账号设置/代理
 * 中心入口在「我的」页与桌面侧栏，退出登录在「我的」页、顶栏账户
 * 菜单与账号设置页均有；「成长」整段（等级/成就/馆灵）已按用户
 * 2026-11-13 圈删令摘除；自创的「我的片子」归组（想看/我的求片/我的
 * 收藏/追剧订阅）同日按用户令摘除——友站概览无此组：我的收藏归底坞
 * 收藏页签、求片归求片页、想看与追剧订阅归条目详情页动作区，追剧订阅
 * 管理面板归账号设置页。
 * 页面跟随用户外观（浅/深本地变量切换，不再经纸色布局强制浅色）。
 * 所有数字/条目只用现有接口数据：订阅（subscriptionApi）、福利
 * （welfareApi）、账号卡（embyApi.getAccountCard）、设备
 * （deviceApi.mine，用户侧设备接口，含在线状态与最后活跃时间）。
 * 流量额度（2026-11-13 一期）：套餐声明月额度时才出现流量区＋
 * 购流量弹窗（trafficApi）；没声明整段不渲染。原站有而本站没有
 * 的（自动续费开关、弹幕 API）整项省略。
 * 「服务器地址」行 2026-11-13 起改开线路弹层（ServerLinesSheet，友站
 * 同行点开即弹层；用户 23:15 亲点），不再跳 /game/operator 整页——
 * 播放账号页本体与菜单/侧栏入口不变。
 * 旧深链 #my-drive 仍直达设置页绑定组。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import {
  ChevronRight, HardDrive, Monitor, MonitorPlay, Route, Smartphone, Tv,
  type LucideIcon,
} from 'lucide-vue-next'
import StatusAlertBar from '@/components/profile/StatusAlertBar.vue'
import TrafficPurchaseModal from '@/components/profile/TrafficPurchaseModal.vue'
import {
  formatBytes as formatTrafficBytes,
  formatGbValue,
  formatResetDate,
  getTraffic,
  type TrafficOverview,
  type TrafficSummary,
} from '@/api/traffic'
import ServerLinesSheet from '@/components/profile/ServerLinesSheet.vue'
import {
  embyApi, subscriptionApi,
  type AccountCard, type MySubscription,
} from '@/api'
import {
  deviceApi, storageBindingApi, vitalityApi, welfareApi,
  type MyDevice, type MyDevicesResponse, type VitalityStatus,
} from '@/api/economy'
import { getMyExclusiveLines, type MyExclusiveLine } from '@/api/user'
import { useUserStore } from '@/stores/user'
import { branding } from '@/composables/useBranding'
import { useTheme } from '@/composables/useTheme'
import { useToast } from '@/composables/useToast'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const userStore = useUserStore()
// 本页已从路由守卫 V4_PATHS 摘除（router/index.ts），html[data-theme] 由守卫按
// 用户外观恢复；页面深浅以用户外观解析值为准，.ov-dark 回滚块保留作主题竞态兜底
const { resolved: themeResolved } = useTheme()

const displayName = computed(() => userStore.user?.username || '我的')
const brandInitial = computed(() => (branding.site_name.trim().charAt(0) || 'A').toUpperCase())
const isFreeRealm = computed(() => userStore.isFreeRealm)
const whitelistIdentity = computed(() => userStore.user?.whitelist_identity ?? null)

// ── 总览数据 ──
const subscriptions = ref<MySubscription[]>([])
const welfare = ref<{ is_welfare: boolean; expires_at: string | null } | null>(null)
const accountCard = ref<AccountCard | null>(null)
const vitality = ref<VitalityStatus | null>(null)

const activeSub = computed(
  () => subscriptions.value.find(s => s.status === 'active' && s.days_left > 0) || null,
)
/** 会员卡文案：套餐名 / 用户名·ID / 状态（全部真实数据） */
const memberPlanName = computed(() => {
  if (isFreeRealm.value) return '公益资格'
  return activeSub.value?.plan_name ?? '未开通'
})
const memberUserLine = computed(
  () => `${displayName.value} · ID ${userStore.user?.id ?? ''}`,
)
const memberStatusText = computed(() => {
  if (whitelistIdentity.value) return '白名单'
  if (isFreeRealm.value) return welfare.value?.is_welfare ? '生效中' : '未开通'
  return activeSub.value ? '生效中' : '未开通'
})
const memberNote = computed(() => {
  const parts: string[] = []
  if (whitelistIdentity.value) {
    parts.push(`${whitelistIdentity.value.title} · ${whitelistIdentity.value.label}`)
  }
  if (isFreeRealm.value) {
    if (welfare.value?.is_welfare && welfare.value.expires_at) {
      parts.push(`到期 ${welfare.value.expires_at.slice(0, 10)}`)
    }
  } else if (activeSub.value) {
    parts.push(`${activeSub.value.plan_name} · ${activeSub.value.end_date.slice(0, 10)} 到期`)
  }
  return parts.join(' · ')
})

/** 有效期文案（YYYY.MM.DD）：付费取订阅到期，公益取资格到期 */
const expiryText = computed(() => {
  if (isFreeRealm.value) {
    return welfare.value?.is_welfare && welfare.value.expires_at
      ? welfare.value.expires_at.slice(0, 10).replace(/-/g, '.')
      : ''
  }
  return activeSub.value ? activeSub.value.end_date.slice(0, 10).replace(/-/g, '.') : ''
})

/** 大数字区：剩余天数＋本周期已过比例（按真实起止日期算；公益只有到期日，不画进度条） */
const quota = computed(() => {
  if (isFreeRealm.value) {
    if (welfare.value?.is_welfare && welfare.value.expires_at) {
      const days = Math.max(
        0,
        Math.ceil((new Date(welfare.value.expires_at).getTime() - Date.now()) / 86_400_000),
      )
      return {
        label: '公益资格剩余',
        days,
        hasPeriod: false,
        usedDays: 0,
        totalDays: 0,
        usedPct: 0,
        remainPct: 0,
        endText: `有效期至 ${welfare.value.expires_at.slice(0, 10).replace(/-/g, '.')}`,
      }
    }
    return null
  }
  const s = activeSub.value
  if (!s) return null
  const start = new Date(s.start_date).getTime()
  const end = new Date(s.end_date).getTime()
  const totalMs = end - start
  const totalDays = Math.max(1, Math.round(totalMs / 86_400_000))
  const usedPctRaw = totalMs > 0 ? ((Date.now() - start) / totalMs) * 100 : 0
  const usedPct = Math.min(100, Math.max(0, Math.round(usedPctRaw)))
  return {
    label: '订阅剩余',
    days: s.days_left,
    hasPeriod: totalMs > 0,
    usedDays: Math.min(totalDays, Math.max(0, Math.floor((Date.now() - start) / 86_400_000))),
    totalDays,
    usedPct,
    remainPct: 100 - usedPct,
    endText: `${s.end_date.slice(0, 10).replace(/-/g, '.')} 到期`,
  }
})

/** 服务器地址行副文案：线路数＝账号卡下发的服地址数（真实数据） */
const serverAddressSub = computed(() => {
  const n = (accountCard.value?.realms ?? []).filter(r => r.base_url).length
  return n > 0 ? `${n} 条线路 · 点开查看与复制地址` : '点开查看与复制地址'
})

// ── 流量段（trafficApi：套餐声明月额度才 enabled；拿不到整段不渲染） ──
const traffic = ref<TrafficOverview | null>(null)
/** 首屏加载态：骨架占位用，请求落定（成功/失败/未启用）即收 */
const trafficLoading = ref(true)
const trafficModalOpen = ref(false)
const trafficRemainGb = computed(() => formatGbValue(traffic.value?.remaining_bytes))
const trafficRemainPct = computed(() => {
  const pct = 100 - (traffic.value?.used_pct ?? 0)
  return Math.min(100, Math.max(0, Math.round(pct)))
})
const trafficUsedText = computed(() => formatTrafficBytes(traffic.value?.used_bytes))
const trafficQuotaText = computed(() => formatTrafficBytes(traffic.value?.quota_bytes))
const trafficResetText = computed(() =>
  traffic.value ? formatResetDate(traffic.value.reset_date) : '',
)
async function loadTraffic() {
  try {
    const res = await getTraffic()
    if (res) traffic.value = res
  } finally {
    trafficLoading.value = false
  }
}
function onTrafficExchanged(summary: TrafficSummary) {
  // 成交后后端回了最新汇总：就地合并（保留 options 等概览字段）
  if (traffic.value) traffic.value = { ...traffic.value, ...summary }
  else void loadTraffic()
}

// ── 设备段（deviceApi.mine：用户侧设备接口） ──
const devicesData = ref<MyDevicesResponse | null>(null)
/** 概览只预览最近 3 台（接口已按最后活跃倒序）；全量在账号设置页管理 */
const previewDevices = computed(() => (devicesData.value?.devices ?? []).slice(0, 3))
function deviceIcon(d: MyDevice): LucideIcon {
  const tag = `${d.client ?? ''} ${d.name ?? ''}`.toLowerCase()
  if (/tv|television/.test(tag)) return Tv
  if (/ios|iphone|ipad|android|phone|mobile|safari|infuse/.test(tag)) return Smartphone
  return Monitor
}
function deviceLabel(d: MyDevice): string {
  return d.name || d.client || '未知设备'
}
/** 最后活跃相对时间（原站「37 分钟前」口径） */
function relTime(iso: string | null): string {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  if (Number.isNaN(diff) || diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  return `${Math.floor(diff / 86_400_000)} 天前`
}
async function loadDevices() {
  try {
    const res = await deviceApi.mine()
    if (res && Array.isArray(res.devices) && res.devices.length) devicesData.value = res
  } catch { /* 设备接口失败整段不渲染，不假造设备行 */ }
}


// ── 我的服务 ──
const myLines = ref<MyExclusiveLine[]>([])
const linesOpen = ref(false)
const linesSheetOpen = ref(false)
async function copyLineUrl(url: string) {
  try {
    await navigator.clipboard.writeText(url)
    toast.success('线路地址已复制，去播放器里添加服务器时粘贴它')
  } catch {
    toast.error('复制失败，请长按地址手动复制')
  }
}
const driveRowVisible = ref(false)
const driveMeta = ref('')
async function loadDriveMeta() {
  try {
    const res = await storageBindingApi.providers()
    if (!(res.providers || []).includes('drive')) return
    driveRowVisible.value = true
    const list = await storageBindingApi.list()
    const drive = (list.bindings || []).filter(b => b.provider === 'drive')
    if (!drive.length) driveMeta.value = '谷歌盘 · 未绑定'
    else if (drive.some(b => b.status === 'active')) driveMeta.value = '谷歌盘 · 已绑定'
    else driveMeta.value = '谷歌盘 · 不可用'
  } catch { /* 名单拿不到整行不渲染（与设置页门控一致） */ }
}

// ── 总览数据 ──
async function loadOverview() {
  try {
    subscriptions.value = await subscriptionApi.getMine()
  } catch { /* 摘要拿不到就留空 */ }
  try {
    const st = await welfareApi.status()
    welfare.value = { is_welfare: st.is_welfare, expires_at: st.welfare_expires_at }
    if (userStore.user) userStore.user.is_welfare = st.is_welfare
  } catch { /* 状态口失败不阻断页面 */ }
  try {
    accountCard.value = await embyApi.getAccountCard()
  } catch { /* 账号卡失败不阻断页面 */ }
  try {
    vitality.value = await vitalityApi.status()
  } catch { /* 非公益服用户注定失败，格子不渲染 */ }
}

onMounted(() => {
  // 旧深链（播放器方案弹窗的「去绑盘」）直达设置页绑定组
  if (route.hash === '#my-drive') {
    void router.replace({ path: '/game/account/settings', hash: '#my-drive' })
    return
  }
  void loadOverview()
  void loadTraffic()
  void loadDevices()

  void loadDriveMeta()
  void getMyExclusiveLines().then(lines => { myLines.value = lines }).catch(() => {})
})
</script>

<style scoped>
/* 概览：跟随用户外观的平面结构。本地主题变量：缺省深色，
   html[data-theme='light'] 时整套换浅（与 useTheme 的 data-theme 口径一致）。
   另给 StatusAlertBar 备齐它依赖的纸色变量（原由布局 .v4-scope 提供）。 */
.ov-page {
  --line: rgba(255, 255, 255, 0.1);
  --paper-card: #17181c;
  --paper-danger-soft: rgba(220, 38, 38, 0.14);
  --red: #f87171;
  --paper-warning-soft: rgba(217, 119, 6, 0.16);
  --paper-warning: #fbbf24;
  --paper-r-lg: 16px;
  min-height: 100dvh;
  background: #050505;
  color: #ffffff;
  font-family: var(--uhd-font-sans);
  -webkit-font-smoothing: antialiased;
}
.ov-inner {
  max-width: 760px;
  margin: 0 auto;
  padding: calc(14px + env(safe-area-inset-top)) 20px 150px;
  box-sizing: border-box;
}

/* 页头行 */
.ov-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 18px;
}
.ov-brand {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}
.ov-brand__logo {
  width: 28px;
  height: 28px;
  object-fit: contain;
}
.ov-brand__mark {
  font-size: 26px;
  font-weight: var(--uhd-fw-black);
  line-height: 1;
}
.ov-brand__name {
  font-size: 21px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 网页观影入口：实测大颗软胶囊（221×62、圆角 31＝半高全胶囊）；
   浅色近白底带粉色水洗（中心 #f9f3f3）、深色 #312d2e，无彩虹描边。
   本端顶栏比例下按同形态缩放高度，底色/胶囊/无描边逐项对齐实测。 */
.watch-pill {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  height: 54px;
  padding: 0 24px;
  border: none;
  border-radius: 999px;
  background: #312d2e;
  color: #ffffff;
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  text-decoration: none;
  white-space: nowrap;
}

/* 会员卡：斜向粉彩渐变＋细斜纹，圆角约 28、柔影极淡无描边
   渐变停点＝UHDNow 实测像素采样串（实测清单第 1/1b 节，逐停点照抄） */
.ov-card {
  position: relative;
  overflow: hidden;
  min-height: 218px;
  border-radius: 28px;
  padding: 24px 26px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  background:
    linear-gradient(115deg, #2b2d2f 0%, #252629 6%, #2e2e34 12%, #35333d 18%, #3e3e45 24%, #433e4d 29%, #484255 34%, #4f4a5e 39%, #605a76 43%, #655f7d 46%, #716b8e 49%, #79769c 52%, #74759a 57%, #6d7599 62%, #697498 67%, #677598 72%, #5f7898 77%, #4e6781 82%, #445a70 86%, #3f5367 90%, #354556 93%, #313e4c 96%, #1e2328 98%, #161522 100%);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.32);
}
html:not([data-theme='light']) .ov-card {
  background:
    linear-gradient(115deg, #2b2d2f 0%, #252629 6%, #2e2e34 12%, #35333d 18%, #3e3e45 24%, #433e4d 29%, #484255 34%, #4f4a5e 39%, #605a76 43%, #655f7d 46%, #716b8e 49%, #79769c 52%, #74759a 57%, #6d7599 62%, #697498 67%, #677598 72%, #5f7898 77%, #4e6781 82%, #445a70 86%, #3f5367 90%, #354556 93%, #313e4c 96%, #1e2328 98%, #161522 100%);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.32);
}
/* 细斜线纹理层：实测垂直周期约 4.2px、方向约 68°（115° 轴向）；低对比雾面
   （采样亮度带 184–254、白区近不可见），alpha 压淡＋mask 只落在渐变区 */
.ov-card__shine {
  position: absolute;
  inset: 0;
  background: repeating-linear-gradient(115deg, rgba(255, 255, 255, 0.1) 0 1px, transparent 1px 4.2px);
  -webkit-mask-image: linear-gradient(115deg, transparent 30%, #000 58%);
  mask-image: linear-gradient(115deg, transparent 30%, #000 58%);
  pointer-events: none;
}
.ov-card__head {
  position: relative;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}
.ov-card__brand {
  font-size: 17px;
  font-weight: var(--uhd-fw-bold);
  color: #ffffff;
}
.ov-card__play {
  flex: 0 0 auto;
  filter: drop-shadow(0 3px 8px rgba(80, 120, 220, 0.35));
}
.ov-card__foot {
  position: relative;
  margin-top: auto;
  padding-top: 56px;
}
.ov-card__plan {
  margin: 0;
  font-family: var(--uhd-font-sans);
  font-size: 40px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
  line-height: 1.12;
  color: #ffffff;
}
.ov-card__user {
  margin: 10px 0 0;
  font-size: 15.5px;
  color: rgba(255, 255, 255, 0.55);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 卡下信息行 */
.ov-expiry {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 2px 0;
  font-size: 15px;
  color: rgba(255, 255, 255, 0.62);
}
.ov-expiry__note {
  color: rgba(255, 255, 255, 0.42);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 大数字区 */
.ov-quota {
  padding-top: 34px;
}
.ov-quota__label {
  margin: 0;
  font-size: 19px;
  font-weight: var(--uhd-fw-medium);
  color: #ffffff;
}
.ov-quota__big {
  margin: 10px 0 0;
  /* 实测大数字 56–60px/800 */
  font-size: 58px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.02em;
  line-height: 1.02;
  color: #ffffff;
  font-variant-numeric: tabular-nums;
}
.ov-quota__big--empty {
  font-size: 44px;
}
.ov-quota__unit {
  margin-left: 10px;
  /* 实测单位字约 24px（原站为 GB，本站单位为「天」，只对齐字号档） */
  font-size: 24px;
  font-weight: var(--uhd-fw-extrabold);
  color: rgba(255, 255, 255, 0.62);
}
.ov-quota__remain {
  margin: 14px 0 0;
  font-size: 20px;
  font-weight: var(--uhd-fw-bold);
  color: #ffffff;
}
.ov-bar {
  margin-top: 14px;
  /* 实测进度条高 6 */
  height: 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.14);
  overflow: hidden;
}
.ov-bar__fill {
  height: 100%;
  border-radius: 999px;
  background: #ffffff;
  transition: width 0.4s ease;
}
.ov-quota__meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
  font-size: 15px;
  color: rgba(255, 255, 255, 0.42);
}
.ov-quota__meta-line {
  margin: 12px 0 0;
  font-size: 15px;
  color: rgba(255, 255, 255, 0.42);
}

/* 双按钮 */
.ov-actions {
  display: flex;
  gap: 12px;
  padding-top: 26px;
}
.ov-actions--traffic {
  padding-top: 16px;
}
/* 购买流量钮按压反馈（app 口径：点下去要有明确反馈态） */
.ov-actions--traffic .ov-btn:active {
  transform: scale(0.96);
  opacity: 0.85;
}
.ov-actions--traffic .ov-btn {
  transition: transform 0.12s ease, opacity 0.12s ease;
}
/* 流量区骨架：与真实区同版式占位，微光扫过表示加载中 */
.ov-skel {
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.12);
  position: relative;
  overflow: hidden;
}
.ov-skel::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(
    100deg, transparent 30%, rgba(255, 255, 255, 0.14) 50%, transparent 70%);
  animation: ov-skel-sweep 1.3s ease-in-out infinite;
}
@keyframes ov-skel-sweep {
  from { transform: translateX(-100%); }
  to { transform: translateX(100%); }
}
.ov-skel--label { width: 96px; height: 15px; }
.ov-skel--big { width: 150px; height: 52px; margin-top: 12px; border-radius: 10px; }
.ov-skel--remain { width: 72px; height: 19px; margin-top: 12px; }
.ov-skel--bar { width: 100%; height: 6px; margin-top: 14px; border-radius: 999px; }
.ov-skel--meta { width: 62%; height: 13px; margin-top: 12px; }
html[data-theme='light'] .ov-skel { background: #e8e8ea; }
html[data-theme='light'] .ov-skel::after {
  background: linear-gradient(
    100deg, transparent 30%, rgba(255, 255, 255, 0.55) 50%, transparent 70%);
}
html[data-theme='light'] .ov-page.ov-dark .ov-skel { background: rgba(255, 255, 255, 0.12); }
html[data-theme='light'] .ov-page.ov-dark .ov-skel::after {
  background: linear-gradient(
    100deg, transparent 30%, rgba(255, 255, 255, 0.14) 50%, transparent 70%);
}
@media (prefers-reduced-motion: reduce) {
  .ov-skel::after { animation: none; }
  .ov-actions--traffic .ov-btn { transition: none; }
}
.ov-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  /* 实测钮高约 43、圆角约 21、字 14–15px */
  height: 43px;
  padding: 0 24px;
  border-radius: 21px;
  background: #17181c;
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #ffffff;
  font-size: 14.5px;
  font-weight: var(--uhd-fw-semibold);
  text-decoration: none;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.05);
}
/* 主钮：深色面＝深色毛玻璃底＋彩虹渐变边框＋玻璃栈（内高光＋柔影＋backdrop，2026-11-13 补齐玻璃）；
   浅色面＝淡彩粉彩底＋彩虹边框（见浅色覆盖） */
.ov-btn--primary {
  border: 1.5px solid transparent;
  background:
    linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box,
    var(--uhd-rainbow) border-box;
  color: #ffffff;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.22),
    0 12px 28px -12px rgba(0, 0, 0, 0.5);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
}
.ov-btn:active {
  opacity: 0.8;
}

/* 平面段：标题 26/700＋发丝线 */
.ov-sec {
  margin-top: 42px;
}
.ov-sec__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid #26262a;
}
.ov-sec__title {
  margin: 0;
  font-family: var(--uhd-font-sans);
  /* 实测区块标题 24–28px/700 */
  font-size: 26px;
  font-weight: var(--uhd-fw-bold);
  letter-spacing: -0.01em;
  color: #ffffff;
}
.ov-sec__more {
  font-size: 15.5px;
  color: rgba(255, 255, 255, 0.62);
  text-decoration: none;
  white-space: nowrap;
}
.ov-row__chev {
  flex: 0 0 auto;
  color: rgba(255, 255, 255, 0.42);
  transition: transform 0.2s ease;
}
.ov-row__chev--open {
  transform: rotate(90deg);
}

/* Emby 段图标两行 */
.ov-irow {
  width: 100%;
  min-height: 63px;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 7px 2px;
  border: none;
  border-bottom: 1px solid #26262a;
  background: transparent;
  color: #ffffff;
  font-family: inherit;
  text-align: left;
  text-decoration: none;
  cursor: pointer;
  box-sizing: border-box;
}
.ov-irow:active {
  opacity: 0.72;
}
.ov-irow__icon {
  flex: 0 0 auto;
  color: #ffffff;
}
.ov-irow__main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ov-irow__name {
  /* 实测行标题 15–16px、副标题 13px */
  font-size: 16px;
  font-weight: var(--uhd-fw-semibold);
  color: #ffffff;
}
.ov-irow__sub {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.42);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ov-lines {
  padding: 6px 2px 4px;
  border-bottom: 1px solid #26262a;
}
.ov-line-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 0;
}
.ov-line-row + .ov-line-row {
  border-top: 1px dashed #26262a;
}
.ov-line-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.ov-line-name {
  font-size: 14.5px;
  font-weight: var(--uhd-fw-semibold);
}
.ov-line-url {
  font-size: 12.5px;
  color: rgba(255, 255, 255, 0.42);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ov-copy-btn {
  flex: 0 0 auto;
  padding: 8px 14px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 999px;
  background: #17181c;
  color: rgba(255, 255, 255, 0.62);
  font-family: inherit;
  font-size: 12.5px;
  cursor: pointer;
}
.ov-line-note {
  margin: 0;
  padding: 8px 0 10px;
  font-size: 12.5px;
  line-height: 1.6;
  color: rgba(255, 255, 255, 0.42);
}

/* 设备行 */
.ov-drow {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 52px;
  box-sizing: border-box;
  padding: 13px 2px;
  border-bottom: 1px solid #26262a;
}
.ov-drow__icon {
  flex: 0 0 auto;
  color: #ffffff;
}
.ov-drow__name {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 16px;
  font-weight: var(--uhd-fw-semibold);
  color: #ffffff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ov-drow__status {
  flex: 0 0 auto;
  font-size: 15px;
  color: rgba(255, 255, 255, 0.42);
}
.ov-drow__status--on {
  color: #4ade80;
  font-weight: var(--uhd-fw-medium);
}

/* 纯文字行（常用及其余归段） */
.ov-trow {
  width: 100%;
  min-height: 52px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 2px;
  border: none;
  border-bottom: 1px solid #26262a;
  background: transparent;
  color: #ffffff;
  font-family: inherit;
  text-align: left;
  text-decoration: none;
  cursor: pointer;
  box-sizing: border-box;
}
.ov-trow:active {
  opacity: 0.72;
}
.ov-trow__name {
  flex: 1 1 auto;
  font-size: 16px;
  font-weight: var(--uhd-fw-medium);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ov-trow__value {
  flex: 0 1 auto;
  min-width: 0;
  font-size: 15px;
  color: rgba(255, 255, 255, 0.42);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 浅色覆盖：跟随用户外观（html[data-theme='light']） */
html[data-theme='light'] .ov-page {
  background: #f8f8f8;
  color: #111111;
  --line: #e7e7e7;
  --paper-card: #ffffff;
  --paper-danger-soft: rgba(220, 38, 38, 0.07);
  --red: #dc2626;
  --paper-warning-soft: rgba(217, 119, 6, 0.09);
  --paper-warning: #d97706;
}
html[data-theme='light'] .ov-brand__name { color: #111111; }
html[data-theme='light'] .watch-pill {
  background: linear-gradient(180deg, #fdf8f8 0%, #f9f1f1 100%);
  color: #111111;
}
html[data-theme='light'] .ov-card {
  background:
    linear-gradient(115deg, #fcfcfc 0%, #f4f0fa 14%, #ede7f7 25%, #e4dbf5 34%, #d7cdf2 42%, #c9bfef 48%, #c2b9ee 54%, #bdb6ee 59%, #b7b5ef 63%, #b0b6f0 67%, #a8b9f0 70%, #a0bbf1 73%, #d0dff9 76%, #a6c4f4 79%, #d7e6fa 82%, #b7d1f6 85%, #bed7f7 88%, #e1edfb 91%, #ccdff9 94%, #dae8fa 96%, #f2f7fd 98%, #fbfcfd 100%);
  box-shadow: 0 8px 22px rgba(90, 94, 150, 0.08);
}
html[data-theme='light'] .ov-card__brand { color: #111111; }
html[data-theme='light'] .ov-card__plan { color: #111111; }
html[data-theme='light'] .ov-card__user { color: #55575c; }
html[data-theme='light'] .ov-expiry { color: #55575c; }
html[data-theme='light'] .ov-expiry__note { color: #9a9da3; }
html[data-theme='light'] .ov-quota__label { color: #111111; }
html[data-theme='light'] .ov-quota__big { color: #111111; }
html[data-theme='light'] .ov-quota__unit { color: #55575c; }
html[data-theme='light'] .ov-quota__remain { color: #111111; }
html[data-theme='light'] .ov-bar { background: #e8e8ea; }
html[data-theme='light'] .ov-bar__fill { background: #111111; }
html[data-theme='light'] .ov-quota__meta { color: #9a9da3; }
html[data-theme='light'] .ov-quota__meta-line { color: #9a9da3; }
html[data-theme='light'] .ov-btn { background: #ffffff; border-color: #e7e7e7; color: #111111; }
html[data-theme='light'] .ov-btn--primary { background: var(--uhd-rainbow-soft) padding-box, var(--uhd-rainbow) border-box; border-color: transparent; color: #111111; box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--uhd-shadow-card-l); backdrop-filter: var(--uhd-glass-filter); -webkit-backdrop-filter: var(--uhd-glass-filter); }
html[data-theme='light'] .ov-sec__head { border-bottom-color: #e5e5e7; }
html[data-theme='light'] .ov-sec__title { color: #111111; }
html[data-theme='light'] .ov-sec__more { color: #55575c; }
html[data-theme='light'] .ov-row__chev { color: #9a9da3; }
html[data-theme='light'] .ov-irow { border-bottom-color: #e5e5e7; color: #111111; }
html[data-theme='light'] .ov-irow__icon { color: #111111; }
html[data-theme='light'] .ov-irow__name { color: #111111; }
html[data-theme='light'] .ov-irow__sub { color: #9a9da3; }
html[data-theme='light'] .ov-lines { border-bottom-color: #e5e5e7; }
html[data-theme='light'] .ov-line-row + .ov-line-row { border-top-color: #e5e5e7; }
html[data-theme='light'] .ov-line-url { color: #9a9da3; }
html[data-theme='light'] .ov-copy-btn { background: #ffffff; border-color: #e7e7e7; color: #55575c; }
html[data-theme='light'] .ov-line-note { color: #9a9da3; }
html[data-theme='light'] .ov-drow { border-bottom-color: #e5e5e7; }
html[data-theme='light'] .ov-drow__icon { color: #111111; }
html[data-theme='light'] .ov-drow__name { color: #111111; }
html[data-theme='light'] .ov-drow__status { color: #9a9da3; }
html[data-theme='light'] .ov-drow__status--on { color: #16a34a; }
html[data-theme='light'] .ov-trow { border-bottom-color: #e5e5e7; color: #111111; }
html[data-theme='light'] .ov-trow__value { color: #9a9da3; }

/* 兜底回滚块：守卫已不再把本页钉成 light（V4_PATHS 摘除 /game/account）；
   若主题解析与 data-theme 出现竞态不一致，用户选深色时由 .ov-dark 类回滚为深色 */
html[data-theme='light'] .ov-page.ov-dark .watch-pill { background: #312d2e; color: #ffffff; }
html[data-theme='light'] .ov-page.ov-dark .ov-card__brand { color: #ffffff; }
html[data-theme='light'] .ov-page.ov-dark .ov-card__plan { color: #ffffff; }
html[data-theme='light'] .ov-page.ov-dark .ov-card__user { color: rgba(255, 255, 255, 0.55); }
html[data-theme='light'] .ov-page.ov-dark .ov-expiry__note { color: rgba(255, 255, 255, 0.42); }
html[data-theme='light'] .ov-page.ov-dark .ov-quota__label { color: #ffffff; }
html[data-theme='light'] .ov-page.ov-dark .ov-quota__big { color: #ffffff; }
html[data-theme='light'] .ov-page.ov-dark .ov-quota__unit { color: rgba(255, 255, 255, 0.62); }
html[data-theme='light'] .ov-page.ov-dark .ov-quota__remain { color: #ffffff; }
html[data-theme='light'] .ov-page.ov-dark .ov-bar { background: rgba(255, 255, 255, 0.14); }
html[data-theme='light'] .ov-page.ov-dark .ov-bar__fill { background: #ffffff; }
html[data-theme='light'] .ov-page.ov-dark .ov-quota__meta { color: rgba(255, 255, 255, 0.42); }
html[data-theme='light'] .ov-page.ov-dark .ov-quota__meta-line { color: rgba(255, 255, 255, 0.42); }
html[data-theme='light'] .ov-page.ov-dark .ov-btn { background: #17181c; color: #ffffff; }
html[data-theme='light'] .ov-page.ov-dark .ov-btn--primary { background: linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box, var(--uhd-rainbow) border-box; color: #ffffff; box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.22), 0 12px 28px -12px rgba(0, 0, 0, 0.5); backdrop-filter: var(--uhd-glass-filter); -webkit-backdrop-filter: var(--uhd-glass-filter); }
html[data-theme='light'] .ov-page.ov-dark .ov-sec__title { color: #ffffff; }
html[data-theme='light'] .ov-page.ov-dark .ov-sec__more { color: rgba(255, 255, 255, 0.62); }
html[data-theme='light'] .ov-page.ov-dark .ov-row__chev { color: rgba(255, 255, 255, 0.42); }
html[data-theme='light'] .ov-page.ov-dark .ov-irow__icon { color: #ffffff; }
html[data-theme='light'] .ov-page.ov-dark .ov-irow__name { color: #ffffff; }
html[data-theme='light'] .ov-page.ov-dark .ov-irow__sub { color: rgba(255, 255, 255, 0.42); }
html[data-theme='light'] .ov-page.ov-dark .ov-line-url { color: rgba(255, 255, 255, 0.42); }
html[data-theme='light'] .ov-page.ov-dark .ov-copy-btn { background: #17181c; color: rgba(255, 255, 255, 0.62); }
html[data-theme='light'] .ov-page.ov-dark .ov-line-note { color: rgba(255, 255, 255, 0.42); }
html[data-theme='light'] .ov-page.ov-dark .ov-drow__icon { color: #ffffff; }
html[data-theme='light'] .ov-page.ov-dark .ov-drow__name { color: #ffffff; }
html[data-theme='light'] .ov-page.ov-dark .ov-drow__status { color: rgba(255, 255, 255, 0.42); }
html[data-theme='light'] .ov-page.ov-dark .ov-drow__status--on { color: #4ade80; }
html[data-theme='light'] .ov-page.ov-dark .ov-trow__value { color: rgba(255, 255, 255, 0.42); }
html[data-theme='light'] .ov-page.ov-dark :deep(.uad-pill) { background: rgba(30, 30, 33, 0.55); border-color: rgba(255, 255, 255, 0.16); box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.12), 0 16px 40px rgba(0, 0, 0, 0.5); }
html[data-theme='light'] .ov-page.ov-dark :deep(.uad-tab) { color: rgba(255, 255, 255, 0.55); }
html[data-theme='light'] .ov-page.ov-dark :deep(.uad-tab--on) { background: rgba(255, 255, 255, 0.13); color: #ffffff; }
html[data-theme='light'] .ov-page.ov-dark .ov-card { background: linear-gradient(115deg, #2b2d2f 0%, #252629 6%, #2e2e34 12%, #35333d 18%, #3e3e45 24%, #433e4d 29%, #484255 34%, #4f4a5e 39%, #605a76 43%, #655f7d 46%, #716b8e 49%, #79769c 52%, #74759a 57%, #6d7599 62%, #697498 67%, #677598 72%, #5f7898 77%, #4e6781 82%, #445a70 86%, #3f5367 90%, #354556 93%, #313e4c 96%, #1e2328 98%, #161522 100%); }
html[data-theme='light'] .ov-page.ov-dark .ov-expiry { color: rgba(255, 255, 255, 0.62); }
html[data-theme='light'] .ov-page.ov-dark .ov-irow { border-bottom-color: #26262a; color: #ffffff; }
html[data-theme='light'] .ov-page.ov-dark .ov-drow { border-bottom-color: #26262a; }
html[data-theme='light'] .ov-page.ov-dark .ov-trow { border-bottom-color: #26262a; color: #ffffff; }
</style>
