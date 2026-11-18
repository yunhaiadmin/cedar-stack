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
 * v2.54（暗房影院）：整页改用 components/ui 的共享原语（PageHeader / SectionCard /
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
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import {
  MessageSquareDashed, Ticket, Users, Wallet,
  ArrowRight, TrendingUp,
  HardDrive, ScanSearch, Activity,
  AlertTriangle, Gauge, HeartPulse, ListTodo,
} from 'lucide-vue-next'
import { EmptyState, PageHeader, SectionCard, StatTile } from '@/components/ui'
import {
  fetchLibraries, fetchOverview, fetchPlaybackStats,
  fetchStatsTrend, fetchDashboardAggregate, fetchQuotaDashboard,
  fetchPanelHealth, fetchPlayStartStats,
  type DashboardAggregate, type DashboardDetailTables, type QuotaDashboard,
  type PanelHealth, type PlayStartStats } from '@/api/admin'
import { fetchEconomyStats, type EconomyStats } from '@/api/economy'
import type {
  EmbyLibrary, OverviewStats, PlaybackStats, TrendStats,
} from '@/types'

const overview = ref<OverviewStats | null>(null)
const playback = ref<PlaybackStats | null>(null)
/** 聚合 detail part：本页只消费其中的刮削进度（队列积压卡的补资料 pending 合计） */
const detail = ref<DashboardDetailTables | null>(null)
const economy = ref<EconomyStats | null>(null)
const trend = ref<TrendStats | null>(null)
// 存储健康卡按库的本机目录可读性判（见 mountSummary）。以前这里还单独拉一次 /mounts，
// 结果从没被用到——「存储来源」独立页撤销后成了每次进仪表盘白打的一个请求，已去掉。
const libraries = ref<EmbyLibrary[]>([])
const loading = ref(true)
const trendLoading = ref(false)
/** 核心三项（概览 / 播放 / 经营）里拿不到的那几项：页面照常渲染其余部分，顶部给一条可重试的提示 */
const failedParts = ref<string[]>([])

const days = ref(14)
type Metric = 'new_users' | 'plays' | 'revenue'
const metric = ref<Metric>('new_users')
// 趋势图指标色（暗房影院）：走 --au-* 令牌，随深浅主题与品牌色切换。
// SVG 的 stroke / stop-color **属性**不认 var()，所以模板里一律写进 style（CSS 属性认）。
const metricTabs: { key: Metric; label: string; color: string }[] = [
  { key: 'new_users', label: '新增用户', color: 'var(--au-primary)' },
  { key: 'plays', label: '播放次数', color: 'var(--au-info)' },
  { key: 'revenue', label: '营收 ($)', color: 'var(--au-success)' },
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
  libraries.value = parts.libraries?.libraries ?? []
  detail.value = parts.detail ?? null
  failedParts.value = failed
  loading.value = false
  return true
}

/**
 * 首屏：优先走聚合接口，失败则回退逐个调用（概览/播放/经营/媒体库/趋势）。
 * （聚合只是把"逐个调用"搬到服务端一次完成，任一形态下各区块的
 *  失败兜底语义保持一致：失败的那几项在顶部点名并给「重试」。）
 */
async function loadAll() {
  loading.value = true
  if (await loadAggregate()) return  // 内部已收尾 loading/failedParts
  const failed: string[] = []
  const soft = <T,>(label: string, p: Promise<T>) => p.catch(() => { failed.push(label); return null })
  try {
    const [o, p, e, libraryData] = await Promise.all([
      soft('概览', fetchOverview()),
      soft('播放统计', fetchPlaybackStats()),
      soft('经营数据', fetchEconomyStats()),
      fetchLibraries().catch(() => ({ libraries: [] as EmbyLibrary[] })),
      loadTrend(),
    ])
    overview.value = o
    playback.value = p
    economy.value = e
    libraries.value = libraryData.libraries
  } finally {
    failedParts.value = failed
    loading.value = false
  }
}

/** 额度口（只读）：本页只消费转存队列计数（队列积压卡）；开关关时后端 404，计数按未拿到处理 */
const quota = ref<QuotaDashboard | null>(null)
async function loadQuota() {
  try {
    quota.value = await fetchQuotaDashboard()
  } catch {
    quota.value = null
  }
}

/** 面板健康（/api/health）：全站状态一眼区的「站点健康」来源，独立软加载 */
const panelHealth = ref<PanelHealth | null>(null)
async function loadPanelHealth() {
  try {
    panelHealth.value = await fetchPanelHealth()
  } catch {
    panelHealth.value = null
  }
}

/** 起播统计（play-start-stats）：独立软加载，窗口 24 小时 / 7 天可切 */
const playStart = ref<PlayStartStats | null>(null)
const playStartHours = ref(24)
const playStartLoading = ref(false)
async function loadPlayStart() {
  playStartLoading.value = true
  try {
    playStart.value = await fetchPlayStartStats(playStartHours.value)
  } catch {
    playStart.value = null
  } finally {
    playStartLoading.value = false
  }
}
watch(playStartHours, loadPlayStart)

onMounted(() => { loadAll(); loadQuota(); loadPanelHealth(); loadPlayStart() })

// ==================== 全站状态一眼区 ====================

/** 站点健康：与系统健康页同口径（healthy 正常 / degraded 有警告 / unhealthy 异常） */
const healthGlance = computed(() => {
  const h = panelHealth.value
  if (!h) return { label: '无法确认', tone: 'plain' as KpiTone, hint: '健康状态暂未拿到' }
  const issues = (h.health_issues ?? []).map((i) => i.message).filter(Boolean).join('；')
  if (h.status === 'healthy') return { label: '正常', tone: 'ok' as KpiTone, hint: 'API 正常响应' }
  if (h.status === 'degraded') return { label: '有警告', tone: 'warn' as KpiTone, hint: issues || 'API 正常响应' }
  if (h.status === 'unhealthy') return { label: '异常', tone: 'danger' as KpiTone, hint: issues || '点开看系统健康' }
  return { label: h.status, tone: 'plain' as KpiTone, hint: '' }
})

/** 补资料积压：聚合 detail 的刮削进度按库 pending 合计（detail 没拿到时为 null，不显示假数） */
const enrichPending = computed(() =>
  detail.value ? detail.value.enrich_progress.reduce((s, r) => s + r.pending, 0) : null,
)

/** 队列积压：补资料（明细合计）+ 转存（额度口）+ 扫描中（媒体库状态），都用页内已拉到的数据 */
const queueGlance = computed(() => {
  const parts: string[] = []
  let pending = 0
  let known = false
  if (enrichPending.value != null) {
    parts.push(`补资料待处理 ${enrichPending.value}`)
    pending += enrichPending.value
    known = true
  }
  const t = quota.value?.transfer_queue.counts
  if (t) {
    parts.push(`转存待跑 ${t.pending} · 进行中 ${t.running}`)
    pending += t.pending
    known = true
    if (t.failed > 0) parts.push(`转存失败 ${t.failed}`)
  }
  if (scanSummary.value.scanning > 0) parts.push(`扫描中 ${scanSummary.value.scanning}`)
  return { known, pending, hint: parts.join('\n') || '队列暂未拿到' }
})

// ==================== 起播统计汇总 ====================

/** 起播来源显示名：挂载行优先挂载名，其余按 provider 既有取值给中文名 */
const PLAY_PROVIDER_LABEL: Record<string, string> = {
  drive: 'Google Drive',
  local: '本机文件',
  mount: '挂载',
  unknown: '未知来源',
  upstream: '上游线路',
}
function playSourceName(row: PlayStartStats['by_provider'][number]): string {
  if (row.mount_name) return row.mount_name
  if (row.provider === 'mount' && row.mount_id != null) return `挂载 #${row.mount_id}`
  return PLAY_PROVIDER_LABEL[row.provider] || row.provider
}

function fmtMs(v: number | null): string {
  return v == null ? '—' : `${Math.round(v)} ms`
}

/** 起播汇总：总数/失败为各来源行合计；平均解析按各行起播数加权；最慢取各行最大 */
const playTotals = computed(() => {
  const rows = playStart.value?.by_provider ?? []
  const total = rows.reduce((s, r) => s + r.total, 0)
  const errors = rows.reduce((s, r) => s + r.errors, 0)
  const weighted = rows.filter((r) => r.avg_resolve_ms != null)
  const wTotal = weighted.reduce((s, r) => s + r.total, 0)
  const avg = wTotal > 0
    ? weighted.reduce((s, r) => s + (r.avg_resolve_ms ?? 0) * r.total, 0) / wTotal
    : null
  const maxes = rows.map((r) => r.max_resolve_ms).filter((v): v is number => v != null)
  return { total, errors, avg, max: maxes.length ? Math.max(...maxes) : null }
})

const playFailRateText = computed(() => {
  const { total, errors } = playTotals.value
  return total > 0 ? `${((errors / total) * 100).toFixed(1)}%` : '—'
})

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

// ==================== 交付链数据卡 ====================


/**
 * 扫描状态：正常 / 异常（只有 failed）/ 部分完成（partial，单独呈现、
 * 不计入异常——它表示「有来源这轮没读到、已跳过」，不是故障）/ 正在扫 /
 * 从没扫过。2026-11-04 口径修复：此前 partial 与 failed 一起算「异常」，
 * 无界库长期 partial 时卡面恒显「异常 9」，误报。
 */
const scanSummary = computed(() => {
  const list = libraries.value
  const statusOf = (l: EmbyLibrary) => (l.is_scanning ? 'running' : l.last_scan?.status || '')
  return {
    total: list.length,
    ok: list.filter((l) => statusOf(l) === 'success').length,
    bad: list.filter((l) => statusOf(l) === 'failed').length,
    partial: list.filter((l) => statusOf(l) === 'partial').length,
    scanning: list.filter((l) => statusOf(l) === 'running').length,
    never: list.filter((l) => !l.last_scan && !l.is_scanning).length,
  }
})

/**
 * 存储健康：以 EM（面板进程）能不能碰到为准（em_reachable），没有体检结果时退回落库结果
 *
 * 「被媒体库绑定、但播放节点（EA）够不着」是唯一阻断播放的一种，单独点出来。
 */
/** 存储卡改看**本机目录可读性**（v2.46.0）

之前这里看的是 EA 可达性（旧的挂载体检状态），但面板自己真正会因挂载断掉而扫不到的是
本机路径：那个路径在容器里读不到，扫描就会把它当成“来源没了”。所以改成按库的本机目录
判：ok=能列举 / false=有路径读不到 / null=纯远程或没配本机路径，不参与判定。
 */
const mountSummary = computed(() => {
  const libs = libraries.value.filter((l) => !l.is_virtual)
  const local = libs.filter((l) => l.local_dirs_readable !== null)
  const failed = local.filter((l) => l.local_dirs_readable === false)
  return {
    total: libs.length,
    // 可判定的来源数（有本机目录可读性结论的库）：卡面分母用它，
    // 不拿全部库数当分母——纯远程库 local_dirs_readable 恒为 null、
    // 不参与判定，全算进分母会恒显「0 个正常/共 N 条来源」式误报
    // （2026-11-04 截图点名）。
    checked: local.length,
    ok: local.filter((l) => l.local_dirs_readable === true).length,
    failed: failed.length,
    unchecked: libs.length - local.length,
    blocked: libs.filter((l) => (l.local_dirs_readable === false)
      && (l.path_entries?.length || 0) > 0).length,
    failedNames: failed.slice(0, 2).map((l) => l.name).join('、'),
    failedMore: failed.length > 2,
  }
})

type KpiTone = 'plain' | 'ok' | 'info' | 'warn' | 'danger'

/** 顶部三张卡：用户 → 内容（扫描）→ 存储；待处理计数由待办条独占，不在此重复 */
const kpis = computed<{
  key: string; label: string; value: number | string; foot: string
  to: string; icon: unknown; tone: KpiTone; title: string
}[]>(() => {
  const scan = scanSummary.value
  const mount = mountSummary.value
  return [
    {
      key: 'users', label: '用户总数', value: overview.value?.users.total ?? 0,
      foot: `活跃 ${overview.value?.users.active ?? 0} · 今日播放 ${playback.value?.today.plays ?? 0} 次`,
      to: '/users', icon: Users, tone: 'plain', title: '用户与账号',
    },
    {
      key: 'scan', label: '扫描状态', value: `${scan.ok} 个正常/共 ${scan.total} 个库`,
      foot: scan.total
        ? [
            scan.bad ? `异常 ${scan.bad}` : '',
            scan.partial ? `部分完成 ${scan.partial}` : '',
            scan.scanning ? `扫描中 ${scan.scanning}` : '',
            scan.never ? `未扫过 ${scan.never}` : '',
          ].filter(Boolean).join('\n') || '全部正常'
        : '还没有媒体库',
      to: '/emby', icon: ScanSearch,
      tone: scan.bad ? 'warn' : scan.partial || scan.scanning ? 'info' : scan.total ? 'ok' : 'plain',
      title: '媒体库与扫描',
    },
    {
      key: 'mounts', label: '存储健康', value: mount.checked ? `${mount.ok} 个正常/共 ${mount.checked} 条来源` : '无本机目录来源',
      foot: mount.total
        ? [
            mount.failed ? `异常 ${mount.failed}${mount.failedNames ? `（${mount.failedNames}${mount.failedMore ? ' 等' : ''}）` : ''}` : '',
            mount.unchecked ? `纯远程来源 ${mount.unchecked}（无需体检）` : '',
            mount.blocked ? `播放节点够不着 ${mount.blocked}` : '',
          ].filter(Boolean).join('\n') || '全部正常'
        : '还没有存储来源',
      to: '/servers', icon: HardDrive,
      tone: mount.failed || mount.blocked ? 'warn' : mount.total ? 'ok' : 'plain',
      title: '存储来源（按服务器配置）',
    },
  ]
})

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
      description="先看现在能不能用、有没有要处理的，再看趋势与起播。每个数字都能点进带好筛选的明细页。"
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
        全站状态一眼区（v2.55 方便优先）：打开仪表盘先回答四件事——
        站点健不健康、队列堵不堵、起播快不快、今天怎么样。
        每张卡点进去就是对应明细（起播卡锚到本页起播统计区）。
      -->
      <section class="glance-grid" aria-label="全站状态">
        <RouterLink to="/health" class="glance-card">
          <div class="glance-head">
            <HeartPulse :size="15" />
            <span>站点健康</span>
            <span class="status-dot glance-dot" :class="{ warning: healthGlance.tone === 'warn', danger: healthGlance.tone === 'danger', muted: healthGlance.tone === 'plain' }" />
          </div>
          <div class="glance-value" :class="`tone-${healthGlance.tone}`">{{ healthGlance.label }}</div>
          <div class="glance-hint">{{ healthGlance.hint }}</div>
        </RouterLink>
        <RouterLink to="/tasks" class="glance-card">
          <div class="glance-head">
            <ListTodo :size="15" />
            <span>队列积压</span>
          </div>
          <div class="glance-value">{{ queueGlance.known ? `${queueGlance.pending} 项待处理` : '—' }}</div>
          <div class="glance-hint pre-line">{{ queueGlance.hint }}</div>
        </RouterLink>
        <a href="#play-start-stats" class="glance-card">
          <div class="glance-head">
            <Gauge :size="15" />
            <span>起播近况 · 近 24 小时</span>
          </div>
          <div class="glance-value">{{ playStart ? `${playTotals.total} 次起播` : '—' }}</div>
          <div class="glance-hint">
            {{ playStart ? `失败 ${playTotals.errors}（${playFailRateText}） · 平均解析 ${fmtMs(playTotals.avg)}` : '起播统计暂未拿到' }}
          </div>
        </a>
        <div class="glance-card">
          <div class="glance-head">
            <TrendingUp :size="15" />
            <span>今日数据</span>
          </div>
          <div class="glance-value">{{ playback ? `${playback.today.plays} 次播放` : '—' }}</div>
          <div class="glance-hint">
            播放用户 {{ playback?.today.users ?? '—' }} · 当前会话 {{ overview?.emby.active_sessions ?? '—' }}
          </div>
        </div>
      </section>

      <!--
        交付链数据卡（批 A 精简后 3 张）：用户 → 内容（扫描）→ 存储。
        待处理求片/工单的计数不在这里重复，由下方待办条独占。
      -->
      <section class="kpi-grid" aria-label="交付链">
        <StatTile
          v-for="k in kpis"
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



      <!-- 起播统计（v2.55 接线）：起播事件按来源/错误类型聚合——起播慢是慢在哪家源、
           失败是哪类错误，看这一张卡。窗口内没事件时是空态，不编造数据。 -->
      <SectionCard
        id="play-start-stats"
        title="起播统计"
        :icon="Gauge"
        :meta="playStart ? `近 ${playStart.window_hours} 小时` : '起播事件聚合'"
      >
        <template #actions>
          <el-radio-group v-model="playStartHours" size="small">
            <el-radio-button :value="24">24 小时</el-radio-button>
            <el-radio-button :value="168">7 天</el-radio-button>
          </el-radio-group>
        </template>
        <div v-loading="playStartLoading">
          <template v-if="playStart && playStart.by_provider.length">
            <div class="realm-stats play-summary">
              <span>总起播 <b>{{ playTotals.total }}</b></span>
              <span>失败 <b>{{ playTotals.errors }}</b></span>
              <span>失败率 <b>{{ playFailRateText }}</b></span>
              <span>平均解析 <b>{{ fmtMs(playTotals.avg) }}</b></span>
              <span>最慢解析 <b>{{ fmtMs(playTotals.max) }}</b></span>
            </div>
            <div class="play-table-wrap">
              <table class="detail-table">
                <thead><tr><th>来源</th><th>起播</th><th>失败</th><th>平均解析</th><th>最慢解析</th></tr></thead>
                <tbody>
                  <tr v-for="row in playStart.by_provider" :key="`${row.provider}:${row.mount_id ?? ''}`">
                    <td>{{ playSourceName(row) }}<span v-if="row.mount_type" class="rank-type">{{ row.mount_type }}</span></td>
                    <td>{{ row.total }}</td>
                    <td>{{ row.errors }}</td>
                    <td>{{ fmtMs(row.avg_resolve_ms) }}</td>
                    <td>{{ fmtMs(row.max_resolve_ms) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-if="playStart.by_error_kind.length" class="error-kinds">
              <span class="error-kinds-label">失败类型</span>
              <span v-for="k in playStart.by_error_kind" :key="k.error_kind" class="au-badge au-badge-muted">{{ k.error_kind }} × {{ k.total }}</span>
            </div>
            <p v-else class="play-noerror">窗口内没有失败记录。</p>
          </template>
          <EmptyState
            v-else-if="playStart"
            compact
            :icon="Gauge"
            title="暂无起播记录"
            description="这个时间窗内还没有起播事件；有人开始播放后这里会有数据。"
          />
          <div v-else-if="!playStartLoading" class="play-failed">
            <span>起播统计没拿到。</span>
            <el-button size="small" @click="loadPlayStart">重新加载</el-button>
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
 * 交付链 3 张卡：宽屏 3 列一行排完；≤1180px 维持 3 列（内容区约 890px、单卡约 290px）；
 * 手机 2 列＋末张通栏（见文末媒体查询）。 */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

/* 扫描/存储卡的 foot 是多行状态（\n 分隔）：按行拆开展示，单行卡不受影响 */
.kpi-grid :deep(.au-stat__hint) {
  white-space: pre-line;
}

/* ===== 全站状态一眼区（v2.55）===== */
.glance-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.glance-card {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 13px 16px;
  border-radius: var(--au-r-lg);
  border: 1px solid var(--au-border);
  background: var(--au-surface);
  color: inherit;
  text-decoration: none;
  min-width: 0;
}
a.glance-card { transition: border-color var(--au-fast) var(--au-ease), background var(--au-fast) var(--au-ease); }
a.glance-card:hover { border-color: var(--au-border-strong); background: var(--au-surface-2); }
.glance-head {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12.5px;
  color: var(--au-text-3);
}
.glance-head svg { color: var(--au-text-3); flex-shrink: 0; }
.glance-dot { margin-left: auto; }
.status-dot.muted { background: var(--au-text-4); box-shadow: 0 0 0 4px var(--au-surface-2); }
.glance-value {
  font-family: var(--au-font-serif);
  font-size: 1.3rem;
  font-weight: 700;
  color: var(--au-text);
  line-height: 1.25;
  font-variant-numeric: tabular-nums;
}
.glance-value.tone-ok { color: var(--au-success); }
.glance-value.tone-warn { color: var(--au-warning); }
.glance-value.tone-danger { color: var(--au-danger); }
.glance-hint {
  font-size: 12px;
  color: var(--au-text-3);
  line-height: 1.5;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.glance-hint.pre-line { white-space: pre-line; display: block; -webkit-line-clamp: unset; }


/* ===== 起播统计（v2.55）===== */
.play-summary { margin-bottom: 8px; }
/* 窄屏横向滚动容器：表头不折行（.detail-table 的 th 默认会换行），
   表给最小宽度，手机上左右滑看全，不挤成竖排两字 */
.play-table-wrap { overflow-x: auto; }
.play-table-wrap .detail-table { min-width: 430px; }
.play-table-wrap .detail-table th { white-space: nowrap; }
.error-kinds {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}
.error-kinds-label { font-size: 12px; color: var(--au-text-3); margin-right: 2px; }
.play-noerror { font-size: 12px; color: var(--au-text-3); margin: 10px 0 0; }
.play-failed {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: var(--au-text-3);
  padding: 6px 0;
}
@media (max-width: 1180px) {
  .kpi-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .glance-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

/* ===== 待办条 ===== */
.todo-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 12px 16px;
  border-radius: var(--au-r-lg);
  border: 1px solid var(--au-border);
  background: var(--au-surface);
}

.todo-bar.clear { border-color: var(--au-success-border); }

.todo-lead {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-right: 6px;
  font-family: var(--au-font-serif);
  font-size: 14px;
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
  font-size: 12px;
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
  font-size: 12.5px;
  text-decoration: none;
  transition: border-color var(--au-fast) var(--au-ease), color var(--au-fast) var(--au-ease);
}

.todo-pill:hover { border-color: var(--au-border-strong); color: var(--au-text); }
.todo-pill strong { color: var(--au-text); font-size: 13.5px; font-variant-numeric: tabular-nums; }
.todo-pill.zero { opacity: 0.55; }
.todo-pill.zero strong { color: var(--au-text-3); }
.todo-pill.danger:not(.zero) strong { color: var(--au-danger); }
.todo-pill.warning:not(.zero) strong { color: var(--au-warning); }
.todo-pill.info:not(.zero) strong { color: var(--au-info); }
.todo-arrow { opacity: 0; transition: opacity var(--au-fast) var(--au-ease); }
.todo-pill:hover .todo-arrow { opacity: 1; }

/* ===== 键值行（起播统计摘要与明细共用）===== */
.realm-stats { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-left: auto; }
.realm-stats span { color: var(--au-text-3); font-size: 12px; }
.realm-stats b { color: var(--au-text-2); font-variant-numeric: tabular-nums; }
.realm-stats span.warn b { color: var(--au-warning); }

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
  font-size: 12px;
  cursor: pointer;
  transition: color var(--au-fast) var(--au-ease), border-color var(--au-fast) var(--au-ease),
    background var(--au-fast) var(--au-ease);
}

.metric-dot { width: 6px; height: 6px; border-radius: 50%; opacity: 0.5; }
.metric-tab:hover { color: var(--au-text); }
.metric-tab.active { color: var(--au-text); border-color: var(--au-border); background: var(--au-surface-2); font-weight: 600; }
.metric-tab.active .metric-dot { opacity: 1; }
.metric-tab:focus-visible { outline: 2px solid var(--au-border-focus); outline-offset: 2px; }

.trend-total { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; }
.trend-total-label { font-size: 12px; color: var(--au-text-3); }
.trend-total-value { font-size: 1.5rem; font-weight: 700; font-variant-numeric: tabular-nums; }

.chart-wrap { position: relative; }
.chart { width: 100%; height: 160px; display: block; }

.chart-axis {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--au-text-4);
  margin-top: 6px;
  font-variant-numeric: tabular-nums;
}

.status-dot { width: 8px; height: 8px; flex: 0 0 8px; border-radius: 50%; background: var(--au-success); box-shadow: 0 0 0 4px var(--au-success-soft); }
.status-dot.warning { background: var(--au-warning); box-shadow: 0 0 0 4px var(--au-warning-soft); }
.status-dot.danger { background: var(--au-danger); box-shadow: 0 0 0 4px var(--au-danger-soft); }

/* 起播明细表：紧凑扫表 */
.detail-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.detail-table th {
  text-align: left;
  font-weight: 500;
  color: var(--au-text-2);
  padding: 6px 8px;
  border-bottom: 1px solid var(--au-border);
}
.detail-table td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--au-border);
  white-space: nowrap;
}
/* 起播来源表里的类型小字（挂载类型） */
.rank-type { font-size: 11px; color: var(--au-text-4); margin-left: 6px; }

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
  .trend-total-value { font-size: 1.25rem; }
  .glance-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .glance-card { padding: 11px 12px; }
  .glance-value { font-size: 1.1rem; }
}
</style>
