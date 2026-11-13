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
            <div class="ov-bar__fill" :style="{ width: `${quota.usedPct}%` }"></div>
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
        <RouterLink class="ov-irow" to="/game/operator">
          <MonitorPlay :size="25" class="ov-irow__icon" aria-hidden="true" />
          <span class="ov-irow__main">
            <span class="ov-irow__name">服务器地址</span>
            <span class="ov-irow__sub">{{ serverAddressSub }}</span>
          </span>
          <ChevronRight :size="18" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
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
        <div v-for="d in devicesData.devices" :key="d.device_id" class="ov-drow">
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
        <RouterLink class="ov-trow" to="/shop">
          <span class="ov-trow__name">能量余额</span>
          <span v-if="points != null" class="ov-trow__value">{{ points.toLocaleString() }}</span>
          <ChevronRight :size="16" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
        <RouterLink class="ov-trow" to="/shop">
          <span class="ov-trow__name">邀请好友</span>
          <ChevronRight :size="16" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
        <RouterLink class="ov-trow" to="/game/guide">
          <span class="ov-trow__name">使用说明</span>
          <ChevronRight :size="16" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
      </section>

      <!-- 我的片子（现有真实条目同式归段，不丢功能） -->
      <section class="ov-sec">
        <div class="ov-sec__head">
          <h2 class="ov-sec__title">我的片子</h2>
        </div>
        <RouterLink class="ov-trow" to="/game/wishlist">
          <span class="ov-trow__name">想看</span>
          <span v-if="wantMeta" class="ov-trow__value">{{ wantMeta }}</span>
          <ChevronRight :size="16" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
        <RouterLink class="ov-trow" to="/game/wishlist">
          <span class="ov-trow__name">我的求片</span>
          <span v-if="requestMeta" class="ov-trow__value">{{ requestMeta }}</span>
          <ChevronRight :size="16" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
        <RouterLink class="ov-trow" to="/favorites">
          <span class="ov-trow__name">我的收藏</span>
          <ChevronRight :size="16" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
        <button
          v-if="followVisible"
          type="button"
          class="ov-trow ov-trow--btn"
          :aria-expanded="followOpen"
          @click="followOpen = !followOpen"
        >
          <span class="ov-trow__name">追剧订阅</span>
          <span v-if="followMeta" class="ov-trow__value">{{ followMeta }}</span>
          <ChevronRight :size="16" class="ov-row__chev" :class="{ 'ov-row__chev--open': followOpen }" aria-hidden="true" />
        </button>
        <!-- 面板常驻挂载（v-show 只控显隐）：挂载即加载并回报 summary，
             接口 404 时父级把整行藏掉 -->
        <div v-show="followVisible && followOpen" class="ov-follow-panel">
          <SeriesFollowsPanel @summary="onFollowSummary" />
        </div>
      </section>

      <!-- 我的权益 -->
      <section class="ov-sec">
        <div class="ov-sec__head">
          <h2 class="ov-sec__title">我的权益</h2>
        </div>
        <RouterLink class="ov-trow" to="/game/account/member">
          <span class="ov-trow__name">我的会员</span>
          <span v-if="memberMeta" class="ov-trow__value">{{ memberMeta }}</span>
          <ChevronRight :size="16" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
        <RouterLink v-if="viewCell" class="ov-trow" to="/game/account/member">
          <span class="ov-trow__name">观看权限</span>
          <span class="ov-trow__value">{{ viewCell.value }} · {{ viewCell.label }}</span>
          <ChevronRight :size="16" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
        <RouterLink class="ov-trow" to="/game/ledger">
          <span class="ov-trow__name">能量账本</span>
          <ChevronRight :size="16" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
        <RouterLink v-if="vitality" class="ov-trow" to="/game/checkin">
          <span class="ov-trow__name">活力 · 今日</span>
          <span class="ov-trow__value">{{ vitality.vitality }}/{{ vitality.max }}</span>
          <ChevronRight :size="16" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
      </section>

      <!-- 成长 -->
      <section class="ov-sec">
        <div class="ov-sec__head">
          <h2 class="ov-sec__title">成长</h2>
        </div>
        <RouterLink class="ov-trow" to="/shop">
          <span class="ov-trow__name">等级</span>
          <span v-if="entryLevelText" class="ov-trow__value">{{ entryLevelText }}</span>
          <ChevronRight :size="16" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
        <RouterLink class="ov-trow" to="/collector">
          <span class="ov-trow__name">成就</span>
          <span v-if="entryAchvText" class="ov-trow__value">{{ entryAchvText }}</span>
          <ChevronRight :size="16" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
        <RouterLink class="ov-trow" to="/collector">
          <span class="ov-trow__name">馆灵</span>
          <span v-if="entryPetText" class="ov-trow__value">{{ entryPetText }}</span>
          <ChevronRight :size="16" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
        <RouterLink class="ov-trow" to="/game/checkin">
          <span class="ov-trow__name">连签</span>
          <span v-if="entryStreakText" class="ov-trow__value">{{ entryStreakText }}</span>
          <ChevronRight :size="16" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
        <RouterLink class="ov-trow" to="/game/pigeon">
          <span class="ov-trow__name">信鸽</span>
          <span class="ov-trow__value">站内消息</span>
          <ChevronRight :size="16" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
      </section>

      <!-- 账号 -->
      <section class="ov-sec">
        <div class="ov-sec__head">
          <h2 class="ov-sec__title">账号</h2>
        </div>
        <RouterLink class="ov-trow" to="/game/account/settings">
          <span class="ov-trow__name">账号设置</span>
          <span class="ov-trow__value">密码 · 设备 · 绑定</span>
          <ChevronRight :size="16" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
        <RouterLink v-if="agentProfile" class="ov-trow" to="/game/agent">
          <span class="ov-trow__name">代理中心</span>
          <span class="ov-trow__value">进货余额 ¥{{ agentProfile.balance.toFixed(2) }}</span>
          <ChevronRight :size="16" class="ov-row__chev" aria-hidden="true" />
        </RouterLink>
      </section>

      <button type="button" class="ov-logout" @click="handleLogout">退出登录</button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 概览（/game/account）：2026-11-13 按 UHDNow 原站用户中心首页 1:1 返工——
 * 页头（品牌＋网页观影胶囊）＋会员卡（斜向粉彩渐变＋细斜纹）＋有效期行
 * ＋大数字区（剩余天数映射）＋双按钮＋Emby/设备/常用平面段；
 * 原页其余真实入口（片子/权益/成长/账号）按同一行式归段，一个不丢。
 * 页面跟随用户外观（浅/深本地变量切换，不再经纸色布局强制浅色）。
 * 所有数字/条目只用现有接口数据：订阅（subscriptionApi）、福利
 * （welfareApi）、账号卡（embyApi.getAccountCard）、设备
 * （deviceApi.mine，用户侧设备接口，含在线状态与最后活跃时间）。
 * 原站有而本站没有的（自动续费开关、流量额度、弹幕 API）整项省略。
 * 旧深链 #my-drive 仍直达设置页绑定组。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import {
  ChevronRight, HardDrive, Monitor, MonitorPlay, Route, Smartphone, Tv,
  type LucideIcon,
} from 'lucide-vue-next'
import SeriesFollowsPanel from '@/components/profile/SeriesFollowsPanel.vue'
import StatusAlertBar from '@/components/profile/StatusAlertBar.vue'
import {
  embyApi, storeApi, storeRequestsApi, subscriptionApi,
  type AccountCard, type MySubscription,
} from '@/api'
import {
  checkinApi, deviceApi, memberApi, storageBindingApi, vitalityApi, welfareApi,
  type MyDevice, type MyDevicesResponse, type VitalityStatus,
} from '@/api/economy'
import { panelApi } from '@/composables/usePanelApi'
import { getMyExclusiveLines, type MyExclusiveLine } from '@/api/user'
import { getAgentMe, type AgentProfile } from '@/api/agent'
import { useUserStore } from '@/stores/user'
import { branding } from '@/composables/useBranding'
import { useTheme } from '@/composables/useTheme'
import { useToast } from '@/composables/useToast'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const userStore = useUserStore()
// 路由守卫会把本页 html[data-theme] 钉成 light；页面深浅以用户外观解析值为准（.ov-dark 回滚块）
const { resolved: themeResolved } = useTheme()

const displayName = computed(() => userStore.user?.username || '我的')
const brandInitial = computed(() => (branding.site_name.trim().charAt(0) || 'A').toUpperCase())
const isFreeRealm = computed(() => userStore.isFreeRealm)
const whitelistIdentity = computed(() => userStore.user?.whitelist_identity ?? null)

// ── 总览数据 ──
const subscriptions = ref<MySubscription[]>([])
const subsLoaded = ref(false)
const welfare = ref<{ is_welfare: boolean; expires_at: string | null } | null>(null)
const accountCard = ref<AccountCard | null>(null)
const points = ref<number | null>(null)
const vitality = ref<VitalityStatus | null>(null)

const activeSub = computed(
  () => subscriptions.value.find(s => s.status === 'active' && s.days_left > 0) || null,
)
const viewCell = computed(() => {
  const vp = accountCard.value?.view_permission
  if (!vp) return null
  if (vp.source === 'welfare') return { value: '已开通', label: '随资格开通' }
  if (!vp.granted) return { value: '未开通', label: '续上公益资格即可看' }
  if (!vp.expires_at) return { value: '已解锁', label: '永久' }
  const expired = new Date(vp.expires_at).getTime() <= Date.now()
  return {
    value: expired ? '已到期' : '已解锁',
    label: `到期 ${vp.expires_at.slice(0, 10)}`,
  }
})
const memberMeta = computed(() => {
  if (isFreeRealm.value) return '公益服 · 免费开放'
  if (!subsLoaded.value) return ''
  return activeSub.value
    ? `${activeSub.value.plan_name} · 剩余 ${activeSub.value.days_left} 天`
    : '还没有会员'
})
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

// ── 设备段（deviceApi.mine：用户侧设备接口） ──
const devicesData = ref<MyDevicesResponse | null>(null)
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

// ── 我的片子摘要 ──
const wantMeta = ref('')
const requestMeta = ref('')
async function loadWantMeta() {
  try {
    const res = await storeApi.getWants()
    const items = res.items || []
    if (!items.length) return
    const preparing = items.filter(i => ['wanted', 'resolving', 'transferring'].includes(i.state)).length
    wantMeta.value = `${items.length} 部` + (preparing ? ` · ${preparing} 部准备中` : '')
  } catch { /* 商店未开启/未登录会员时不显示摘要 */ }
}
async function loadRequestMeta() {
  try {
    const res = await storeRequestsApi.getMine()
    const active = (res.items || []).filter(i => i.state === 'registered' || i.state === 'seeking').length
    if (active) requestMeta.value = `${active} 条进行中`
  } catch { /* 拿不到就不显示 */ }
}

// ── 追剧订阅（面板回报可见性与订阅数） ──
const followVisible = ref(false)
const followMeta = ref('')
const followOpen = ref(false)
function onFollowSummary(payload: { visible: boolean; activeCount: number }) {
  followVisible.value = payload.visible
  followMeta.value = payload.activeCount > 0 ? `订阅 ${payload.activeCount} 部` : ''
}

// ── 成长摘要（与旧页 loadEntrySummaries 同源同口径） ──
const entryLevelText = ref('')
const entryAchvText = ref('')
const entryPetText = ref('')
const entryStreakText = ref('')
async function loadEntrySummaries() {
  try {
    const m = await memberApi.info()
    if (m && typeof m.level === 'number') {
      entryLevelText.value = `Lv.${m.level}${m.level_name ? ` · ${m.level_name}` : ''}`
    }
  } catch { /* 摘要拿不到就不显示 */ }
  try {
    const c = await panelApi.collector()
    if (typeof c?.achievement_count === 'number') {
      entryAchvText.value = `已点亮 ${c.achievement_count} 枚`
    }
    const p = c?.pet
    if (p && Array.isArray(p.stages) && p.stages.length) {
      const stageName = p.stages[p.stage]?.name
      entryPetText.value = stageName ? `${p.name} · ${stageName}` : p.name
    }
  } catch { /* 摘要拿不到就不显示 */ }
}

// ── 我的服务 ──
const myLines = ref<MyExclusiveLine[]>([])
const linesOpen = ref(false)
async function copyLineUrl(url: string) {
  try {
    await navigator.clipboard.writeText(url)
    toast.success('线路地址已复制，去播放器里添加服务器时粘贴它')
  } catch {
    toast.error('复制失败，请长按地址手动复制')
  }
}
const agentProfile = ref<AgentProfile | null>(null)
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
    subsLoaded.value = true
  } catch { /* 摘要拿不到就留空 */ }
  try {
    const st = await welfareApi.status()
    welfare.value = { is_welfare: st.is_welfare, expires_at: st.welfare_expires_at }
    if (userStore.user) userStore.user.is_welfare = st.is_welfare
  } catch { /* 状态口失败不阻断页面 */ }
  try {
    accountCard.value = await embyApi.getAccountCard()
    if (points.value == null) {
      const bal = accountCard.value?.view_permission?.points_balance
      if (typeof bal === 'number') points.value = bal
    }
  } catch { /* 账号卡失败不阻断页面 */ }
  try {
    const st = await checkinApi.status()
    if (st && typeof st.points === 'number') points.value = st.points
    if (st && st.streak > 0) entryStreakText.value = `已连报 ${st.streak} 天`
  } catch { /* 摘要拿不到就不显示 */ }
  try {
    vitality.value = await vitalityApi.status()
  } catch { /* 非公益服用户注定失败，格子不渲染 */ }
}

/** 退出登录：与设置页同款动作 */
async function handleLogout() {
  await userStore.logout()
  router.push('/login')
}

onMounted(() => {
  // 旧深链（播放器方案弹窗的「去绑盘」）直达设置页绑定组
  if (route.hash === '#my-drive') {
    void router.replace({ path: '/game/account/settings', hash: '#my-drive' })
    return
  }
  void loadOverview()
  void loadDevices()
  void loadEntrySummaries()
  void loadWantMeta()
  void loadRequestMeta()
  void loadDriveMeta()
  void getMyExclusiveLines().then(lines => { myLines.value = lines }).catch(() => {})
  void getAgentMe().then(me => { agentProfile.value = me.is_agent ? me.agent : null }).catch(() => {})
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
.watch-pill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 40px;
  padding: 0 16px;
  border: 1.5px solid transparent;
  border-radius: 999px;
  background:
    linear-gradient(rgba(22, 22, 25, 0.72), rgba(22, 22, 25, 0.72)) padding-box,
    var(--uhd-rainbow) border-box;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  color: #ffffff;
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  text-decoration: none;
  white-space: nowrap;
}

/* 会员卡：斜向粉彩渐变＋细斜纹，圆角 36 */
.ov-card {
  position: relative;
  overflow: hidden;
  min-height: 218px;
  border-radius: 36px;
  padding: 24px 26px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  background:
    linear-gradient(115deg, #17181c 22%, rgba(196, 178, 246, 0.5) 52%, rgba(140, 180, 240, 0.52) 76%, rgba(170, 220, 245, 0.48) 100%);
  box-shadow: 0 18px 44px rgba(31, 38, 135, 0.1);
}
html:not([data-theme='light']) .ov-card {
  background:
    linear-gradient(115deg, #17181c 22%, rgba(124, 106, 220, 0.42) 52%, rgba(70, 130, 220, 0.4) 76%, rgba(80, 170, 220, 0.36) 100%);
  box-shadow: 0 18px 44px rgba(0, 0, 0, 0.42);
}
/* 细斜线纹理层：只落在渐变区（mask 沿同角度渐入） */
.ov-card__shine {
  position: absolute;
  inset: 0;
  background: repeating-linear-gradient(115deg, rgba(255, 255, 255, 0.22) 0 1px, transparent 1px 5px);
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
  font-size: 62px;
  font-weight: var(--uhd-fw-black);
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
  font-size: 30px;
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
  height: 7px;
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
.ov-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 50px;
  padding: 0 28px;
  border-radius: 999px;
  background: #17181c;
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #ffffff;
  font-size: 16.5px;
  font-weight: var(--uhd-fw-semibold);
  text-decoration: none;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.05);
}
.ov-btn--primary {
  border: 1.5px solid transparent;
  background:
    var(--uhd-rainbow-soft) padding-box,
    var(--uhd-rainbow) border-box;
  color: #111111;
  box-shadow: 0 8px 20px rgba(190, 140, 190, 0.16);
}
.ov-btn:active {
  opacity: 0.8;
}

/* 平面段：标题 28/800＋发丝线 */
.ov-sec {
  margin-top: 42px;
}
.ov-sec__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}
.ov-sec__title {
  margin: 0;
  font-family: var(--uhd-font-sans);
  font-size: 28px;
  font-weight: var(--uhd-fw-extrabold);
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
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 13px 2px;
  border: none;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
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
  font-size: 18px;
  font-weight: var(--uhd-fw-semibold);
  color: #ffffff;
}
.ov-irow__sub {
  font-size: 14.5px;
  color: rgba(255, 255, 255, 0.42);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ov-lines {
  padding: 6px 2px 4px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}
.ov-line-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 0;
}
.ov-line-row + .ov-line-row {
  border-top: 1px dashed rgba(255, 255, 255, 0.1);
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
  padding: 13px 2px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}
.ov-drow__icon {
  flex: 0 0 auto;
  color: #ffffff;
}
.ov-drow__name {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 17.5px;
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
  min-height: 56px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 2px;
  border: none;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
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
  font-size: 17.5px;
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
.ov-follow-panel {
  padding-top: 10px;
}

/* 退出登录：独立整宽胶囊（与「我的」页同款） */
.ov-logout {
  width: 100%;
  height: 54px;
  margin-top: 40px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 999px;
  background: #17181c;
  color: var(--uhd-danger);
  font-family: inherit;
  font-size: 17px;
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
}
.ov-logout:active {
  opacity: 0.78;
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
  background:
    linear-gradient(#ffffff, #ffffff) padding-box,
    var(--uhd-rainbow) border-box;
  color: #111111;
}
html[data-theme='light'] .ov-card {
  background:
    linear-gradient(115deg, #ffffff 22%, rgba(186, 164, 244, 0.62) 52%, rgba(122, 166, 238, 0.66) 76%, rgba(158, 214, 243, 0.6) 100%);
  box-shadow: 0 18px 44px rgba(31, 38, 135, 0.1);
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
html[data-theme='light'] .ov-sec__head { border-bottom-color: #e7e7e7; }
html[data-theme='light'] .ov-sec__title { color: #111111; }
html[data-theme='light'] .ov-sec__more { color: #55575c; }
html[data-theme='light'] .ov-row__chev { color: #9a9da3; }
html[data-theme='light'] .ov-irow { border-bottom-color: #e7e7e7; color: #111111; }
html[data-theme='light'] .ov-irow__icon { color: #111111; }
html[data-theme='light'] .ov-irow__name { color: #111111; }
html[data-theme='light'] .ov-irow__sub { color: #9a9da3; }
html[data-theme='light'] .ov-lines { border-bottom-color: #e7e7e7; }
html[data-theme='light'] .ov-line-row + .ov-line-row { border-top-color: #e7e7e7; }
html[data-theme='light'] .ov-line-url { color: #9a9da3; }
html[data-theme='light'] .ov-copy-btn { background: #ffffff; border-color: #e7e7e7; color: #55575c; }
html[data-theme='light'] .ov-line-note { color: #9a9da3; }
html[data-theme='light'] .ov-drow { border-bottom-color: #e7e7e7; }
html[data-theme='light'] .ov-drow__icon { color: #111111; }
html[data-theme='light'] .ov-drow__name { color: #111111; }
html[data-theme='light'] .ov-drow__status { color: #9a9da3; }
html[data-theme='light'] .ov-drow__status--on { color: #16a34a; }
html[data-theme='light'] .ov-trow { border-bottom-color: #e7e7e7; color: #111111; }
html[data-theme='light'] .ov-trow__value { color: #9a9da3; }
html[data-theme='light'] .ov-logout { background: #ffffff; border-color: #e7e7e7; }

/* 路由守卫会把本页 html 钉成 light：用户选深色时由 .ov-dark 类以更高优先级回滚为深色 */
html[data-theme='light'] .ov-page.ov-dark .watch-pill { background: linear-gradient(rgba(22, 22, 25, 0.72), rgba(22, 22, 25, 0.72)) padding-box,
    var(--uhd-rainbow) border-box; color: #ffffff; }
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
html[data-theme='light'] .ov-page.ov-dark .ov-card { background: linear-gradient(115deg, #17181c 22%, rgba(196, 178, 246, 0.5) 52%, rgba(140, 180, 240, 0.52) 76%, rgba(170, 220, 245, 0.48) 100%); }
html[data-theme='light'] .ov-page.ov-dark .ov-expiry { color: rgba(255, 255, 255, 0.62); }
html[data-theme='light'] .ov-page.ov-dark .ov-irow { border-bottom-color: rgba(255, 255, 255, 0.1); color: #ffffff; }
html[data-theme='light'] .ov-page.ov-dark .ov-drow { border-bottom-color: rgba(255, 255, 255, 0.1); }
html[data-theme='light'] .ov-page.ov-dark .ov-trow { border-bottom-color: rgba(255, 255, 255, 0.1); color: #ffffff; }
html[data-theme='light'] .ov-page.ov-dark .ov-logout { background: #17181c; border-color: rgba(255, 255, 255, 0.1); }
</style>
