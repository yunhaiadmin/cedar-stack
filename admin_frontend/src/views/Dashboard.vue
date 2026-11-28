<script setup lang="ts">
/**
 * 仪表盘 — 经营驾驶舱
 *
 * v2.25.0：顶部换成**交付链数据卡**（用户总数 / 当前播放 / 待处理求片 / 待处理工单 /
 * 扫描状态 / 存储健康）：先看「现在能不能用、有没有要处理的」，再往下才是经营与排行。
 * 每张卡都直接点进对应页面，不用先想「这个数字在哪一页」。
 *
 * v2.42.10（Phase 5·KPI 深链）：**每一个数字都能点**，而且点进去**已经带上筛选**——
 * 「待处理工单 3」进的是 `/tickets?status=open`（只看待处理的那几条），不是全部工单；
 * 「待支付订单」进 `/orders?status=pending`。落地页用 `useQueryFilter` 接这个参数
 * （见 composables/useQueryFilter.ts），所以从仪表盘、待办条、命令面板点过去口径一致。
 *
 * - 顶部「待办」条：待处理工单 / 待审求片 / 待支付订单，一点直达对应页面（带筛选）
 * - 交易概览：今日营收、累计营收、兑换码核销、邀请人数（均可点）
 * - 趋势图：近 7/14/30 天的新增用户 / 播放 / 营收（纯 SVG，无额外依赖）
 * - 播放榜：用户榜 + 热门内容榜
 *
 * v2.54（ax 口径）：整页改用 components/ui 的共享原语（PageHeader / SectionCard /
 * StatTile / EmptyState），是其它页面改造的参考实现——见 components/ui/README.md。
 *
 * v2.55（方便优先重排）：页首新增「全站状态」一眼区（站点健康 / 队列积压 /
 * 起播近况 / 今日数据）与「快捷操作」直达条；新接起播统计口
 * （GET /api/admin/playback/play-start-stats）成卡。原有信息区全部保留、
 * 顺序不动，只在前面加「先看状态、先到地方」的两层。
 *
 * v3（2026-11-19 后台大改造批 A）：13 区块精简为 5——一眼区 / KPI 3 卡 /
 * 待办条 / 趋势 / 起播统计。删去的 8 区（快捷操作、服务接入 tiles、各服概况、
 * 额度驾驶舱、交易概览、媒体服务、排行榜、数据明细）在各自功能页都有全量版，
 * 去向见 ~/workspace/reports/admin-overhaul-plan-20261119.md §1。首屏骨架屏
 * 一并删除（与用户端同口径：无全页骨架/转圈），改为内容直接渲染、各卡沿用
 * 既有空值占位。数据请求与口径不变：队列积压卡仍消费聚合 detail 的刮削
 * pending 与额度口的转存计数。
 *
 * v4（2026-11-19 用户亲点：仪表盘只展示运营数据）：5 区再精简为 4 块——
 * 今日经营（今日营收 / 今日订单 / 今日新增用户 / 今日播放）/ 用户与订阅（用户总数 /
 * 有效订阅数）/ 待办条 / 趋势。运维数据全部拿掉：站点健康、队列积压、
 * 扫描状态、存储健康、起播近况、起播统计表六处删除，随之无人消费的
 * 请求一并摘除（面板健康 / 起播统计 / 额度口，以及回退路径里的媒体库
 * 列表）。今日营收与今日新增用户取趋势序列末点（与趋势图同源同口径：
 * 营收 = 当日已支付订阅订单金额、按 paid_at 上海日界），今日播放沿用
 * 播放统计的 today 口径；今日订单 = economy_stats 的 today_paid_count
 * （今日已支付订单笔数，与今日营收同一 paid_at 上海日界口径，后端计数）；
 * 有效订阅数取自聚合的 realms part 汇总
 * （summary.active_subscriptions，与服管理页同口径）。
 *
 * v5（2026-11-27 用户拍板四块重排）：块1 生意（今日经营 4 卡 + 用户总数 /
 * 有效订阅 / 媒体库条目 3 卡，有效订阅改走聚合新 ops part、hint 带 7 天内
 * 到期数；媒体库条目 hint 带今日新增入库）/ 块2 待处理与异常（待办条 +
 * 新增状态条：健康一句话、探测/刮削积压、磁盘余量，数据走 ops part）/
 * 块3 大家在看什么（当前在播 + 近 7 天观影榜前 5 + 求片最多前 5）/
 * 块4 趋势（指标 tab 新增「新增订阅」）。ops 子项为 null 时各自走占位。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import {
  MessageSquareDashed, Ticket, Users, Wallet,
  ArrowRight, TrendingUp,
  Activity, AlertTriangle, Crown, Library, Play, Receipt, UserPlus,
} from 'lucide-vue-next'
import { PageHeader, SectionCard, StatTile } from '@/components/ui'
import {
  fetchOverview, fetchPlaybackStats,
  fetchStatsTrend, fetchDashboardAggregate, fetchRealmOverview,
  type DashboardAggregate, type DashboardOps, type DashboardQueueStat } from '@/api/admin'
import { fetchEconomyStats, type EconomyStats } from '@/api/economy'
import type {
  OverviewStats, PlaybackStats, RealmOverview, TrendStats,
} from '@/types'

const overview = ref<OverviewStats | null>(null)
const playback = ref<PlaybackStats | null>(null)
const economy = ref<EconomyStats | null>(null)
const trend = ref<TrendStats | null>(null)
/** 聚合 realms part：本页只消费 summary 的有效订阅数（用户与订阅区） */
const realms = ref<RealmOverview | null>(null)
/** 聚合 ops part（v5）：订阅到期 / 健康 / 磁盘 / 积压 / 求片热榜 */
const ops = ref<DashboardOps | null>(null)
const loading = ref(true)
const trendLoading = ref(false)
/** 核心三项（概览 / 播放 / 经营）里拿不到的那几项：页面照常渲染其余部分，顶部给一条可重试的提示 */
const failedParts = ref<string[]>([])

const days = ref(14)
type Metric = 'new_users' | 'plays' | 'revenue' | 'new_subscriptions'
const metric = ref<Metric>('new_users')
// 趋势图指标色（ax 口径）：走 --au-* 令牌，随深浅主题与品牌色切换。
// SVG 的 stroke / stop-color **属性**不认 var()，所以模板里一律写进 style（CSS 属性认）。
const metricTabs: { key: Metric; label: string; color: string }[] = [
  { key: 'new_users', label: '新增用户', color: 'var(--au-primary)' },
  { key: 'plays', label: '播放次数', color: 'var(--au-info)' },
  { key: 'revenue', label: '营收 ($)', color: 'var(--au-success)' },
  { key: 'new_subscriptions', label: '新增订阅', color: 'var(--au-warning)' },
]

async function loadTrend() {
  trendLoading.value = true
  try {
    trend.value = await fetchStatsTrend(days.value)
  } catch {
    // GET 静默：趋势拿不到就显示空图，不让切 7/14/30 天抛出未处理的 rejection
    trend.value = null
  } finally {
    trendLoading.value = false
  }
}

/**
 * 聚合优先：一次拿回首屏 8 路数据（省 7 次跨洋往返）。
 * 聚合接口本身失败（网络/5xx/鉴权等）时返回 false，调用方回退逐个调用。
 * parts 里为 null 的项按原 soft() 语义点名并给重试。
 */
async function loadAggregate(): Promise<boolean> {
  const d = days.value
  let agg: DashboardAggregate
  try {
    agg = await fetchDashboardAggregate(days.value)
  } catch {
    return false
  }
  const parts = agg?.parts
  if (!parts) return false
  const failed: string[] = []
  const take = <T,>(label: string, v: T | null): T | null => {
    if (v == null) failed.push(label)
    return v
  }
  overview.value = take('概览', parts.overview)
  playback.value = take('播放统计', parts.playback)
  economy.value = take('经营数据', parts.economy)
  // 天数在请求途中被用户切换：沿用 watch 触发的新请求数据，不覆盖
  if (days.value === d) trend.value = parts.trend
  realms.value = parts.realms ?? null
  // ops 与 realms 同口径：拿不到不点名进 failedParts，各区块自己走占位
  ops.value = parts.ops ?? null
  failedParts.value = failed
  loading.value = false
  return true
}

/**
 * 首屏：优先走聚合接口，失败则回退逐个调用（概览/播放/经营/订阅概览/趋势）。
 * （聚合只是把"逐个调用"搬到服务端一次完成，任一形态下各区块的
 *  失败兜底语义保持一致：失败的那几项在顶部点名并给「重试」。）
 */
async function loadAll() {
  loading.value = true
  if (await loadAggregate()) return  // 内部已收尾 loading/failedParts
  const failed: string[] = []
  const soft = <T,>(label: string, p: Promise<T>) => p.catch(() => { failed.push(label); return null })
  try {
    const [o, p, e, r] = await Promise.all([
      soft('概览', fetchOverview()),
      soft('播放统计', fetchPlaybackStats()),
      soft('经营数据', fetchEconomyStats()),
      // 有效订阅数拿不到时卡面走占位，不点名进 failedParts（与聚合路径 realms 为 null 同口径）
      fetchRealmOverview().catch(() => null),
      loadTrend(),
    ])
    overview.value = o
    playback.value = p
    economy.value = e
    realms.value = r
  } finally {
    failedParts.value = failed
    loading.value = false
  }
}

onMounted(() => { loadAll() })

// ==================== 今日经营 ====================

/**
 * 趋势序列末点 = 今天（后端按上海日界补零生成，序列恒以今日收尾）：
 * 今日营收 / 今日新增用户都取它，与趋势图同源同口径，不另拉接口。
 */
const todayPoint = computed(() => {
  const s = trend.value?.series ?? []
  return s.length ? s[s.length - 1] : null
})
const todayRevenue = computed(() => todayPoint.value?.revenue ?? null)
const todayNewUsers = computed(() => todayPoint.value?.new_users ?? null)

watch(days, loadTrend)

// ==================== 待办 ====================

const todos = computed(() => {
  const list: { label: string; count: number; to: string; icon: unknown; tone: string }[] = []
  if (overview.value) {
    list.push({ label: '待处理工单', count: overview.value.tickets.open, to: '/tickets?status=open', icon: Ticket, tone: 'danger' })
    list.push({ label: '待审求片', count: overview.value.media_seeks.pending, to: '/media-seek?status=pending', icon: MessageSquareDashed, tone: 'warning' })
  }
  if (economy.value) {
    list.push({ label: '待支付订单', count: economy.value.orders.pending, to: '/orders?status=pending', icon: Wallet, tone: 'info' })
  }
  return list
})

const todoTotal = computed(() => todos.value.reduce((s, t) => s + t.count, 0))

// ==================== 用户与订阅数据卡 ====================

type KpiTone = 'plain' | 'ok' | 'info' | 'warn' | 'danger'

/**
 * 三张卡：用户总数 → 有效订阅数 → 媒体库条目。
 * 有效订阅优先走 ops part（与订阅总览页 active 筛选同口径，hint 带
 * 7 天内到期数）；ops 没拿到时回退 realms 汇总（v4 口径）。
 * 今日播放只在上方今日经营卡出现，这里不再重复。
 */
const userTiles = computed<{
  key: string; label: string; value: number | string; foot: string
  to: string; icon: unknown; tone: KpiTone; title: string
}[]>(() => {
  const sub = ops.value?.subscriptions
  const items = overview.value?.emby
  return [
    {
      key: 'users', label: '用户总数', value: overview.value?.users.total ?? 0,
      foot: `活跃 ${overview.value?.users.active ?? 0}`,
      to: '/users', icon: Users, tone: 'plain', title: '用户与账号',
    },
    {
      key: 'subs', label: '有效订阅数',
      value: sub ? sub.active
        : (realms.value ? realms.value.summary.active_subscriptions : '—'),
      foot: sub
        ? `7 天内到期 ${sub.expiring_7d} 份`
        : (realms.value
            ? (realms.value.summary.subscribers != null
                ? `覆盖 ${realms.value.summary.subscribers} 位订阅用户`
                : '全站有效订阅汇总')
            : '订阅数据暂未拿到'),
      to: '/subscriptions', icon: Crown, tone: 'plain', title: '订阅总览',
    },
    {
      key: 'items', label: '媒体库条目',
      value: items ? items.total_items : '—',
      foot: items ? `今日新增入库 ${items.today_new_items}` : '条目数据暂未拿到',
      to: '/emby', icon: Library, tone: 'plain', title: '媒体库',
    },
  ]
})

// ==================== 状态条（块2：健康 / 积压 / 磁盘，ops part） ====================

const healthText = computed(() => {
  const h = ops.value?.health
  if (!h) return '暂未拿到'
  if (h.status === 'healthy') return '系统正常'
  return h.reasons.length ? `有异常：${h.reasons.join('；')}` : '有异常'
})

function fmtQueue(q: DashboardQueueStat | null): string {
  if (!q) return '—'
  const parts = [`${q.pending_ready} 条`]
  if (q.rate_per_min != null) parts.push(`${q.rate_per_min} 条/分钟`)
  if (q.eta_hours != null) parts.push(`预计 ${q.eta_hours} 小时清完`)
  if (q.paused) parts.push('已暂停')
  return parts.join(' · ')
}

const diskText = computed(() => {
  const d = ops.value?.disk
  if (!d) return '—'
  const gb = d.free_bytes != null ? `${(d.free_bytes / 2 ** 30).toFixed(1)} GB 可用` : '余量未知'
  return d.free_percent != null ? `${gb}（剩 ${d.free_percent}%）` : gb
})

// ==================== 大家在看什么（块3） ====================

const topItems = computed(() => (playback.value?.item_ranking ?? []).slice(0, 5))
const topRequests = computed(() => ops.value?.top_requests ?? [])
const nowPlaying = computed(() => playback.value?.now_playing ?? [])

type NowPlayingRow = NonNullable<typeof playback.value>['now_playing'][number]

/** 正在看行标题：《剧名》集名（episode）或片名。 */
function npTitle(np: NowPlayingRow): string {
  return np.series_name ? `《${np.series_name}》${np.name}` : np.name
}

/** 正在看行右侧：客户端/设备名 · 进度百分比 · 已暂停（缺什么省什么）。 */
function npMeta(np: NowPlayingRow): string {
  const parts: string[] = []
  const client = np.client_name || np.device_name
  if (client) parts.push(client)
  if (np.duration_ticks > 0) {
    const pct = Math.min(100, Math.max(0, Math.round((np.position_ticks / np.duration_ticks) * 100)))
    parts.push(`${pct}%`)
  }
  if (np.is_paused) parts.push('已暂停')
  return parts.join(' · ')
}

// ==================== 趋势图 ====================

const currentMetric = computed(() => metricTabs.find((t) => t.key === metric.value)!)

const series = computed(() => trend.value?.series ?? [])

const values = computed(() => series.value.map((p) => Number(p[metric.value]) || 0))

const maxValue = computed(() => Math.max(...values.value, 1))

const CHART_W = 720
const CHART_H = 150
const PAD = 14

const points = computed(() => {
  const list = values.value
  const n = list.length
  if (n === 0) return []
  const step = n > 1 ? CHART_W / (n - 1) : 0
  return list.map((v, i) => {
    const x = i * step
    const y = CHART_H - PAD - (v / maxValue.value) * (CHART_H - PAD * 2)
    return [x, y] as const
  })
})

const linePath = computed(() =>
  points.value.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' '),
)

const areaPath = computed(() =>
  points.value.length ? `${linePath.value} L${CHART_W} ${CHART_H} L0 ${CHART_H} Z` : '',
)

/** x 轴标签：首 / 中 / 末，避免拥挤 */
const axisLabels = computed(() => {
  const list = series.value
  if (list.length === 0) return []
  const idx = [0, Math.floor(list.length / 2), list.length - 1]
  return [...new Set(idx)].map((i) => list[i].date.slice(5))
})

function fmtMoney(v: number): string {
  return `$${v.toFixed(2)}`
}

</script>

<template>
  <div class="admin-page dashboard">
    <PageHeader
      eyebrow="概览"
      title="经营驾驶舱"
      description="先看今天的生意，再处理手边的待办，看看大家在看什么，最后看趋势。每个数字都能点进带好筛选的明细页。"
    >
      <template #actions>
        <RouterLink to="/health" class="au-btn au-btn-ghost au-btn-sm">
          <Activity :size="14" /> 服务健康
        </RouterLink>
      </template>
    </PageHeader>

    <!-- 无全页骨架屏（与用户端同口径）：内容直接渲染，加载中各卡走既有空值占位 -->
    <el-alert
      v-if="!loading && failedParts.length"
        type="warning"
        :closable="false"
        show-icon
        :title="`部分数据没拿到：${failedParts.join('、')}。下面对应的数字暂按 0 显示。`"
      >
        <template #default>
          <el-button size="small" :icon="AlertTriangle" @click="loadAll">重新加载</el-button>
        </template>
      </el-alert>

      <!--
        今日经营（v4 只留运营数据）：打开仪表盘先回答今天的生意怎么样——
        收了多少钱、下了多少单、来了多少新用户、播了多少次。
        营收/新增取趋势序列末点（与下方趋势图同源），播放沿用播放统计口径；
        今日订单取 economy_stats 的今日已支付笔数（与营收同一日界口径）。
        订单页只有状态筛选、无日期筛选，今日订单卡链订单页不带参。
      -->
      <section class="glance-grid" aria-label="今日经营">
        <RouterLink to="/orders?status=paid" class="glance-card">
          <div class="glance-head">
            <Wallet :size="15" />
            <span>今日营收</span>
          </div>
          <div class="glance-value">{{ todayRevenue != null ? fmtMoney(todayRevenue) : '—' }}</div>
          <div class="glance-hint">累计营收 {{ fmtMoney(economy?.orders.revenue ?? 0) }}</div>
        </RouterLink>
        <RouterLink to="/orders" class="glance-card">
          <div class="glance-head">
            <Receipt :size="15" />
            <span>今日订单</span>
          </div>
          <div class="glance-value">{{ economy?.orders.today_paid_count ?? '—' }}</div>
          <div class="glance-hint">待支付订单 {{ economy?.orders.pending ?? 0 }} 笔</div>
        </RouterLink>
        <RouterLink to="/users" class="glance-card">
          <div class="glance-head">
            <UserPlus :size="15" />
            <span>今日新增用户</span>
          </div>
          <div class="glance-value">{{ todayNewUsers != null ? todayNewUsers : '—' }}</div>
          <div class="glance-hint">近 {{ days }} 天共新增 {{ trend?.totals.new_users ?? 0 }}</div>
        </RouterLink>
        <div class="glance-card">
          <div class="glance-head">
            <Play :size="15" />
            <span>今日播放</span>
          </div>
          <div class="glance-value">{{ playback ? `${playback.today.plays} 次播放` : '—' }}</div>
          <div class="glance-hint">
            播放用户 {{ playback?.today.users ?? '—' }} · 当前会话 {{ overview?.emby.active_sessions ?? '—' }}
          </div>
        </div>
      </section>

      <!--
        生意数据卡（v5）：用户总数 → 有效订阅数（ops 口径，hint 带 7 天内
        到期数）→ 媒体库条目（hint 带今日新增入库）。
        待处理求片/工单的计数不在这里重复，由下方待办条独占。
      -->
      <section class="kpi-grid" aria-label="用户、订阅与媒体库">
        <StatTile
          v-for="k in userTiles"
          :key="k.key"
          layout="icon-left"
          :label="k.label"
          :value="k.value"
          :hint="k.foot"
          :icon="k.icon"
          :tone="k.tone"
          :to="k.to"
          :title="k.title"
        />
      </section>

      <!-- 待办 -->
      <section v-if="todos.length" class="todo-bar" :class="{ clear: todoTotal === 0 }" aria-label="待办">
        <div class="todo-lead">
          <span class="todo-lead-label">{{ todoTotal === 0 ? '全部处理完毕' : '待办事项' }}</span>
          <span v-if="todoTotal > 0" class="todo-lead-count">{{ todoTotal }}</span>
        </div>
        <RouterLink
          v-for="t in todos"
          :key="t.label"
          :to="t.to"
          class="todo-pill"
          :class="[t.tone, { zero: t.count === 0 }]"
        >
          <component :is="t.icon" :size="14" />
          <span>{{ t.label }}</span>
          <strong>{{ t.count }}</strong>
          <ArrowRight :size="13" class="todo-arrow" />
        </RouterLink>
      </section>





      <!--
        状态条（v5 块2 续）：健康一句话 + 探测/刮削积压 + 磁盘余量，
        数据来自聚合 ops part；健康点进 /health、积压点进 /tasks。
        ops 没拿到时整条不渲染（fail-soft），子项为 null 各自显示占位。
      -->
      <section v-if="ops" class="status-bar" aria-label="系统状态">
        <RouterLink to="/health" class="status-item" :title="healthText">
          <span
            class="status-dot"
            :class="ops.health ? (ops.health.status === 'healthy' ? 'ok' : 'warn') : 'none'"
          />
          <span class="status-label">健康</span>
          <span class="status-text">{{ healthText }}</span>
        </RouterLink>
        <RouterLink to="/tasks" class="status-item">
          <span class="status-label">探测积压</span>
          <span class="status-text">{{ fmtQueue(ops.probe) }}</span>
        </RouterLink>
        <RouterLink to="/tasks" class="status-item">
          <span class="status-label">刮削积压</span>
          <span class="status-text">{{ fmtQueue(ops.enrich) }}</span>
        </RouterLink>
        <RouterLink to="/health" class="status-item" :class="{ warn: ops.disk?.warn }">
          <span class="status-label">磁盘</span>
          <span class="status-text">{{ diskText }}</span>
        </RouterLink>
      </section>

      <!--
        大家在看什么（v5 块3）：近 7 天观影榜前 5（playback.item_ranking）
        + 求片最多前 5（ops.top_requests）；当前在播路数在卡头 meta。
        今日播放次数只在上方今日经营卡出现，这里不重复。
      -->
      <SectionCard title="大家在看什么" :icon="Play" :meta="`当前在播 ${overview?.emby.active_sessions ?? '—'} 路`">
        <div class="watch-col now-playing">
          <div class="watch-sub">正在看</div>
          <ol v-if="nowPlaying.length" class="rank-list">
            <li v-for="(np, i) in nowPlaying" :key="`np-${np.username}-${np.start_time}-${i}`">
              <span class="rank-name">{{ np.username }} · {{ npTitle(np) }}</span>
              <span v-if="npMeta(np)" class="rank-count">{{ npMeta(np) }}</span>
            </li>
          </ol>
          <p v-else class="rank-empty">当前没人在看</p>
        </div>
        <div class="watch-grid">
          <div class="watch-col">
            <div class="watch-sub">近 7 天观影榜</div>
            <ol v-if="topItems.length" class="rank-list">
              <li v-for="(it, i) in topItems" :key="`${it.name}-${i}`">
                <span class="rank-name">{{ it.series_name ? `《${it.series_name}》${it.name}` : it.name }}</span>
                <span class="rank-count">{{ it.plays }} 次</span>
              </li>
            </ol>
            <p v-else class="rank-empty">暂无数据</p>
          </div>
          <div class="watch-col">
            <div class="watch-sub">求片最多</div>
            <ol v-if="topRequests.length" class="rank-list">
              <li v-for="(r, i) in topRequests" :key="`${r.title}-${i}`">
                <span class="rank-name">{{ r.title }}</span>
                <span class="rank-count">{{ r.count }} 人在求</span>
              </li>
            </ol>
            <p v-else class="rank-empty">暂无数据</p>
          </div>
        </div>
      </SectionCard>

      <!-- 趋势 -->
      <SectionCard title="趋势" :icon="TrendingUp" :meta="`近 ${days} 天`">
        <template #actions>
          <div class="metric-tabs" role="tablist" aria-label="趋势指标">
            <button
              v-for="t in metricTabs"
              :key="t.key"
              class="metric-tab"
              role="tab"
              :aria-selected="metric === t.key"
              :class="{ active: metric === t.key }"
              @click="metric = t.key"
            >
              <span class="metric-dot" :style="{ background: t.color }" aria-hidden="true" />
              {{ t.label }}
            </button>
          </div>
          <el-radio-group v-model="days" size="small">
            <el-radio-button :value="7">7 天</el-radio-button>
            <el-radio-button :value="14">14 天</el-radio-button>
            <el-radio-button :value="30">30 天</el-radio-button>
          </el-radio-group>
        </template>

        <div class="trend-total">
          <span class="trend-total-label">近 {{ days }} 天合计</span>
          <span class="trend-total-value" :style="{ color: currentMetric.color }">
            {{ metric === 'revenue' ? fmtMoney(trend?.totals.revenue ?? 0) : (trend?.totals[metric] ?? 0) }}
          </span>
        </div>

        <div class="chart-wrap" v-loading="trendLoading">
          <svg class="chart" :viewBox="`0 0 ${CHART_W} ${CHART_H}`" preserveAspectRatio="none" role="img" :aria-label="`${currentMetric.label}趋势`">
            <defs>
              <linearGradient :id="`grad-${metric}`" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" :style="{ stopColor: currentMetric.color, stopOpacity: 0.28 }" />
                <stop offset="100%" :style="{ stopColor: currentMetric.color, stopOpacity: 0 }" />
              </linearGradient>
            </defs>
            <path :d="areaPath" :fill="`url(#grad-${metric})`" />
            <path
              :d="linePath"
              fill="none"
              :style="{ stroke: currentMetric.color }"
              stroke-width="2"
              vector-effect="non-scaling-stroke"
              stroke-linejoin="round"
              stroke-linecap="round"
            />
          </svg>
          <div class="chart-axis">
            <span v-for="(l, i) in axisLabels" :key="l + i">{{ l }}</span>
          </div>
        </div>
      </SectionCard>



  </div>
</template>

<style scoped>
/* 页面节奏：区块之间统一 16px（各区块自己不再写 margin） */
.dashboard { gap: 16px; }
.dashboard :deep(.au-page-header) { margin-bottom: 4px; }


/* ===== 网格 =====
 * 用户与订阅 2 张卡：宽屏 2 列一行排完；手机 2 列（见文末媒体查询）。 */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.kpi-grid.cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }

/* ===== 今日经营一眼区（v4，沿用 v2.55 一眼区卡面）===== */
.glance-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.glance-grid.cols-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.glance-card {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 13px 16px;
  border-radius: var(--au-r-lg);
  border: 1px solid var(--ax-glass-border);
  background: var(--ax-glass-d);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08), var(--ax-shadow-card-d);
  backdrop-filter: var(--ax-glass-filter);
  -webkit-backdrop-filter: var(--ax-glass-filter);
  color: inherit;
  text-decoration: none;
  min-width: 0;
}
html[data-theme='light'] .glance-card {
  background: var(--ax-glass-l);
  border-color: var(--ax-glass-border-l);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--ax-shadow-card-l);
}
a.glance-card { transition: border-color var(--au-fast) var(--au-ease), background var(--au-fast) var(--au-ease); }
a.glance-card:hover { border-color: var(--au-border-strong); }
.glance-head {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: var(--ax-fs-aux-sm);
  color: var(--au-text-3);
}
.glance-head svg { color: var(--au-text-3); flex-shrink: 0; }
.glance-value {
  font-family: var(--au-font-serif);
  font-size: var(--ax-fs-title);
  font-weight: 700;
  color: var(--au-text);
  line-height: 1.25;
  font-variant-numeric: tabular-nums;
}
.glance-hint {
  font-size: var(--ax-fs-aux-sm);
  color: var(--au-text-3);
  line-height: 1.5;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

@media (max-width: 1180px) {
  .glance-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

/* ===== 待办条（玻璃栈，同 SectionCard 配方） ===== */
.todo-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 12px 16px;
  border-radius: var(--au-r-lg);
  border: 1px solid var(--ax-glass-border);
  background: var(--ax-glass-d);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08), var(--ax-shadow-card-d);
  backdrop-filter: var(--ax-glass-filter);
  -webkit-backdrop-filter: var(--ax-glass-filter);
}
html[data-theme='light'] .todo-bar {
  background: var(--ax-glass-l);
  border-color: var(--ax-glass-border-l);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--ax-shadow-card-l);
}

.todo-bar.clear { border-color: var(--au-success-border); }

.todo-lead {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-right: 6px;
  font-family: var(--au-font-serif);
  font-size: var(--ax-fs-caption);
  font-weight: 700;
  color: var(--au-text);
}

.todo-bar.clear .todo-lead { color: var(--au-success); }

.todo-lead-count {
  font-family: var(--au-font-sans);
  background: var(--au-danger-soft);
  border: 1px solid var(--au-danger-border);
  color: var(--au-danger);
  border-radius: var(--au-r-full);
  padding: 0 8px;
  font-size: var(--ax-fs-aux-sm);
  line-height: 20px;
}

.todo-pill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 32px;
  padding: 0 12px;
  border-radius: var(--au-r-full);
  border: 1px solid var(--au-border);
  background: var(--au-surface-2);
  color: var(--au-text-2);
  font-size: var(--ax-fs-aux-sm);
  text-decoration: none;
  transition: border-color var(--au-fast) var(--au-ease), color var(--au-fast) var(--au-ease);
}

.todo-pill:hover { border-color: var(--au-border-strong); color: var(--au-text); }
.todo-pill strong { color: var(--au-text); font-size: var(--ax-fs-aux); font-variant-numeric: tabular-nums; }
.todo-pill.zero { opacity: 0.55; }
.todo-pill.zero strong { color: var(--au-text-3); }
.todo-pill.danger:not(.zero) strong { color: var(--au-danger); }
.todo-pill.warning:not(.zero) strong { color: var(--au-warning); }
.todo-pill.info:not(.zero) strong { color: var(--au-info); }
.todo-arrow { opacity: 0; transition: opacity var(--au-fast) var(--au-ease); }
.todo-pill:hover .todo-arrow { opacity: 1; }

/* ===== 趋势 ===== */
.metric-tabs { display: flex; gap: 4px; flex-wrap: wrap; }

.metric-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 10px;
  border-radius: var(--au-r-full);
  border: 1px solid transparent;
  background: transparent;
  color: var(--au-text-3);
  font-size: var(--ax-fs-aux-sm);
  cursor: pointer;
  transition: color var(--au-fast) var(--au-ease), border-color var(--au-fast) var(--au-ease),
    background var(--au-fast) var(--au-ease);
}

.metric-dot { width: 6px; height: 6px; border-radius: 50%; opacity: 0.5; }
.metric-tab:hover { color: var(--au-text); }
/* 激活的指标签：玻璃胶囊（同 AxNavPill 导航项配方） */
.metric-tab.active {
  color: var(--au-text);
  border-color: var(--ax-glass-border);
  background: var(--ax-glass-d);
  backdrop-filter: var(--ax-glass-filter);
  -webkit-backdrop-filter: var(--ax-glass-filter);
  font-weight: 600;
}
html[data-theme='light'] .metric-tab.active {
  background: var(--ax-glass-l);
  border-color: var(--ax-glass-border-l);
}
.metric-tab.active .metric-dot { opacity: 1; }
.metric-tab:focus-visible { outline: 2px solid var(--au-border-focus); outline-offset: 2px; }

.trend-total { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; }
.trend-total-label { font-size: var(--ax-fs-aux-sm); color: var(--au-text-3); }
.trend-total-value { font-size: var(--ax-fs-h3); font-weight: 700; font-variant-numeric: tabular-nums; }

.chart-wrap { position: relative; }
.chart { width: 100%; height: 160px; display: block; }

.chart-axis {
  display: flex;
  justify-content: space-between;
  font-size: var(--ax-fs-micro);
  color: var(--au-text-4);
  margin-top: 6px;
  font-variant-numeric: tabular-nums;
}

/* ===== 状态条（v5 块2，玻璃栈同待办条配方） ===== */
.status-bar {
  display: flex;
  align-items: center;
  gap: 10px 22px;
  flex-wrap: wrap;
  padding: 12px 16px;
  border-radius: var(--au-r-lg);
  border: 1px solid var(--ax-glass-border);
  background: var(--ax-glass-d);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08), var(--ax-shadow-card-d);
  backdrop-filter: var(--ax-glass-filter);
  -webkit-backdrop-filter: var(--ax-glass-filter);
}
html[data-theme='light'] .status-bar {
  background: var(--ax-glass-l);
  border-color: var(--ax-glass-border-l);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--ax-shadow-card-l);
}
.status-item {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  color: var(--au-text-2);
  font-size: var(--ax-fs-aux-sm);
  text-decoration: none;
}
.status-label { color: var(--au-text-3); flex-shrink: 0; }
.status-text { color: var(--au-text); font-variant-numeric: tabular-nums; }
.status-item.warn .status-text { color: var(--au-warning); }
.status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--au-text-4); flex-shrink: 0; }
.status-dot.ok { background: var(--au-success); }
.status-dot.warn { background: var(--au-warning); }

/* ===== 大家在看什么（v5 块3） ===== */
.watch-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 28px; }
.now-playing { margin-bottom: 14px; }
.watch-sub { font-size: var(--ax-fs-aux-sm); color: var(--au-text-3); margin-bottom: 8px; }
.rank-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; counter-reset: rank; }
.rank-list li {
  display: flex; align-items: baseline; gap: 8px;
  font-size: var(--ax-fs-aux); color: var(--au-text-2); min-width: 0;
}
.rank-list li::before {
  counter-increment: rank; content: counter(rank);
  color: var(--au-text-4); font-variant-numeric: tabular-nums; flex-shrink: 0;
}
.rank-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rank-count {
  margin-left: auto; color: var(--au-text-3); font-size: var(--ax-fs-aux-sm);
  font-variant-numeric: tabular-nums; flex-shrink: 0;
}
.rank-empty { font-size: var(--ax-fs-aux-sm); color: var(--au-text-4); margin: 0; }

@media (max-width: 640px) {
  .watch-grid { grid-template-columns: minmax(0, 1fr); }
  .status-bar { padding: 10px 12px; gap: 8px 14px; }
}

/* ===== 手机 ===== */
@media (max-width: 640px) {
  .dashboard { gap: 12px; }
  .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .kpi-grid > :last-child:nth-child(odd) { grid-column: 1 / -1; }
  .todo-bar { padding: 10px 12px; gap: 8px; }
  .todo-lead { width: 100%; padding-right: 0; }
  .chart { height: 132px; }
  /* 7-30 个日期标签在窄屏会挤成一团，隔一个显示一个 */
  .chart-axis span:nth-child(even) { display: none; }
  .trend-total-value { font-size: var(--ax-fs-title); }
  .glance-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .glance-card { padding: 11px 12px; }
  .glance-value { font-size: var(--ax-fs-title); }
}
</style>
