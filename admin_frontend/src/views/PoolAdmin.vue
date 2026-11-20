<script setup lang="ts">
/**
 * 贡献池管理（goal-33 PR-4）
 *
 * 成员生命周期运营面：授予候选资格、审核（批准/拒绝）、暂停/恢复、
 * 清退（可勾违规，置位后该成员违规清退期不结激励）、单盘上限覆盖，
 * 外加按条目查池副本与手动下线。池配置在「系统设置 · 公共贡献池」卡。
 * 成员区按贡献者聚合（一人一卡、卡内逐号明细与操作）：后端逐行返回
 * 每个盘一行，一个人贡献多个号时平铺成行看不出归属与待批准数；
 * 卡片流式布局也避免宽表在手机上横滚 + 固定列遮挡。
 * 版式与其它页一致：PageHeader + StatTile + SectionCard，颜色只用
 * --au-* 令牌。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  AlertTriangle, Brush, Coins, Database, Flag, HardDrive, Play, RefreshCw, Users,
} from 'lucide-vue-next'
import { PageHeader, SectionCard, StatTile, EmptyState } from '@/components/ui'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import FilterBar from '@/components/FilterBar.vue'
import type { FilterConfig } from '@/components/FilterBar.vue'
import {
  approvePoolMember, clearPoolMemberViolation, ejectPoolMember,
  fetchPoolCopies, fetchPoolJanitorConfig, fetchPoolMembers,
  fetchPoolOverview, fetchPoolRetryConfig,
  fetchPoolRewardConfig, fetchPoolRewardDetails, fetchPoolRewardPreview,
  fetchPoolRewardSettlements, grantPoolCandidate, patchPoolMember,
  rejectPoolMember, releasePoolCopy, releasePoolRewardDetail,
  resumePoolMember, savePoolJanitorConfig, savePoolRetryConfig,
  savePoolRewardConfig,
  settlePoolRewards,
  suspendPoolMember,
  type PoolCopy, type PoolJanitorConfig, type PoolMember,
  type PoolOverview, type PoolRetryConfig,
  type PoolRewardConfig, type PoolRewardDetail, type PoolRewardPreview,
  type PoolRewardSettlement,
} from '@/api/pool'

const loading = ref(false)
const loadError = ref('')
const overview = ref<PoolOverview | null>(null)
const members = ref<PoolMember[]>([])
const actingId = ref<number | null>(null)

const grantUserId = ref<number | null>(null)
const granting = ref(false)

const copiesItemId = ref<number | null>(null)
const copies = ref<PoolCopy[]>([])
const copiesLoading = ref(false)
const copiesQueried = ref(false)

const ejectTarget = ref<PoolMember | null>(null)
const ejectVisible = ref(false)
const ejectViolation = ref(false)
const ejectReason = ref('')
const ejecting = ref(false)

const STATUS_LABELS: Record<string, string> = {
  candidate: '候选',
  pending: '待审',
  active: '在池',
  suspended: '已暂停',
  exited: '已退出',
  rejected: '已拒绝',
  ejected: '已清退',
}

const REASON_LABELS: Record<string, string> = {
  manual: '管理员暂停',
  breaker: '熔断',
  binding_invalid: '绑定失效',
  contributor_exit: '贡献者退出',
  admin_eject: '管理员清退',
}

/** 卡头汇总口径：与逐行状态文案分开，用管理员口语（在池=已批准、待审=待批准）。 */
const SUMMARY_LABELS: Record<string, string> = {
  active: '已批准',
  pending: '待批准',
  candidate: '候选',
  suspended: '已暂停',
  exited: '已退出',
  rejected: '已拒绝',
  ejected: '已清退',
}
const SUMMARY_ORDER = [
  'pending', 'active', 'candidate', 'suspended', 'exited', 'rejected', 'ejected',
]
/** 逐号行排序：待批准排最前，管理员先处理新申请。 */
const ROW_ORDER: Record<string, number> = {
  pending: 0, candidate: 1, active: 2, suspended: 3,
  exited: 4, rejected: 5, ejected: 6,
}

interface ContributorGroup {
  userId: number
  username: string | null
  rows: PoolMember[]
  pendingCount: number
  summary: string
}

/**
 * 按贡献者聚合：后端逐行返回每个盘一行，前端按 user_id 收成一人一卡，
 * 否则一个人贡献多个号时看不出归属、也看不出有几个待批准。
 */
const contributorGroups = computed<ContributorGroup[]>(() => {
  const byUser = new Map<number, PoolMember[]>()
  for (const m of members.value) {
    const list = byUser.get(m.user_id)
    if (list) list.push(m)
    else byUser.set(m.user_id, [m])
  }
  const groups: ContributorGroup[] = []
  for (const [userId, rows] of byUser) {
    const sorted = [...rows].sort((a, b) =>
      (ROW_ORDER[a.status] ?? 9) - (ROW_ORDER[b.status] ?? 9) || a.id - b.id)
    const counts = new Map<string, number>()
    for (const r of sorted) counts.set(r.status, (counts.get(r.status) ?? 0) + 1)
    const summary = SUMMARY_ORDER
      .filter((s) => counts.has(s))
      .map((s) => `${SUMMARY_LABELS[s]} ×${counts.get(s)}`)
      .join(' · ')
    groups.push({
      userId,
      username: sorted[0]?.username ?? null,
      rows: sorted,
      pendingCount: counts.get('pending') ?? 0,
      summary,
    })
  }
  // 有待批准的贡献者排最前，其余按用户名稳定排序
  groups.sort((a, b) =>
    b.pendingCount - a.pendingCount
    || (a.username ?? '').localeCompare(b.username ?? '')
    || a.userId - b.userId)
  return groups
})

function fmtBytes(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

function fmtTime(s: string | null): string {
  return s ? s.replace('T', ' ').slice(0, 16) : '—'
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const [ov, list] = await Promise.all([
      fetchPoolOverview(), fetchPoolMembers(),
    ])
    overview.value = ov
    members.value = list.members
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '请求失败'
  } finally {
    loading.value = false
  }
}

async function runAction(id: number, fn: () => Promise<unknown>, okText: string) {
  actingId.value = id
  try {
    await fn()
    ElMessage.success(okText)
    await load()
  } catch {
    // request 层已弹错误提示
  } finally {
    actingId.value = null
  }
}

async function doGrant() {
  if (!grantUserId.value) {
    ElMessage.warning('先填站内用户 ID')
    return
  }
  granting.value = true
  try {
    await grantPoolCandidate(grantUserId.value)
    ElMessage.success('候选资格已授予')
    grantUserId.value = null
    await load()
  } catch {
    // request 层已弹错误提示
  } finally {
    granting.value = false
  }
}

async function confirmDo(
  title: string, text: string, fn: () => Promise<unknown>, okText: string,
  id: number,
) {
  try {
    await ElMessageBox.confirm(text, title, {
      confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning',
    })
  } catch {
    return
  }
  await runAction(id, fn, okText)
}

function openEject(member: PoolMember) {
  ejectTarget.value = member
  ejectViolation.value = false
  ejectReason.value = ''
  ejectVisible.value = true
}

async function doEject() {
  if (!ejectTarget.value) return
  const target = ejectTarget.value
  ejecting.value = true
  try {
    await ejectPoolMember(target.id, {
      violation: ejectViolation.value,
      violation_reason: ejectViolation.value ? ejectReason.value || null : null,
    })
    ElMessage.success('已清退')
    ejectTarget.value = null
    ejectVisible.value = false
    await load()
  } catch {
    // request 层已弹错误提示
  } finally {
    ejecting.value = false
  }
}

async function editCapacity(member: PoolMember) {
  let value: string
  try {
    const res = await ElMessageBox.prompt(
      '填这个盘的池占用上限（字节）。清空 = 回落全局配置。',
      `单盘上限 · ${member.username ?? member.user_id} · ${member.account_label ?? '候选资格'}`,
      {
        confirmButtonText: '保存',
        cancelButtonText: '取消',
        inputValue: member.capacity_cap_bytes != null
          ? String(member.capacity_cap_bytes) : '',
        inputPattern: /^\d*$/,
        inputErrorMessage: '只能填非负整数（字节）',
      },
    )
    value = res.value
  } catch {
    return
  }
  const cap = value.trim() === '' ? null : Number(value)
  await runAction(
    member.id,
    () => patchPoolMember(member.id, { capacity_cap_bytes: cap }),
    '单盘上限已更新',
  )
}

async function loadCopies() {
  if (!copiesItemId.value) {
    ElMessage.warning('先填条目 ID')
    return
  }
  copiesLoading.value = true
  try {
    const res = await fetchPoolCopies(copiesItemId.value)
    copies.value = res.copies
    copiesQueried.value = true
  } catch {
    // request 层已弹错误提示
  } finally {
    copiesLoading.value = false
  }
}

async function doReleaseCopy(copy: PoolCopy) {
  try {
    await ElMessageBox.confirm(
      '下线后这份副本立即停发，盘内文件会尝试删除。确定下线吗？',
      '手动下线副本',
      { confirmButtonText: '下线', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await releasePoolCopy(copy.id)
    ElMessage.success('已下线')
    await loadCopies()
    await load()
  } catch {
    // request 层已弹错误提示
  }
}

// ------------------------------------------------------------ 巡检清理（闲置主动清理）

const janitorCfg = ref<PoolJanitorConfig | null>(null)
const janitorCfgSaving = ref(false)

async function loadJanitorCfg() {
  try {
    janitorCfg.value = await fetchPoolJanitorConfig()
  } catch {
    // 巡检区加载失败不拖垮成员区；request 层已弹提示
  }
}

async function doSaveJanitorCfg() {
  if (!janitorCfg.value) return
  janitorCfgSaving.value = true
  try {
    janitorCfg.value = await savePoolJanitorConfig({
      pool_idle_evict_proactive_enabled:
        janitorCfg.value.pool_idle_evict_proactive_enabled,
    })
    ElMessage.success('巡检配置已保存')
  } catch {
    // request 层已弹错误提示
  } finally {
    janitorCfgSaving.value = false
  }
}

// ------------------------------------------------------------ 重试（copy_forbidden 换盘）

const retryCfg = ref<PoolRetryConfig | null>(null)
const retryCfgSaving = ref(false)

async function loadRetryCfg() {
  try {
    retryCfg.value = await fetchPoolRetryConfig()
  } catch {
    // 重试区加载失败不拖垮成员区；request 层已弹提示
  }
}

async function doSaveRetryCfg() {
  if (!retryCfg.value) return
  retryCfgSaving.value = true
  try {
    retryCfg.value = await savePoolRetryConfig({
      pool_copy_forbidden_cooldown_sec:
        retryCfg.value.pool_copy_forbidden_cooldown_sec,
      pool_copy_forbidden_max_attempts:
        retryCfg.value.pool_copy_forbidden_max_attempts,
      pool_source_block_max_failures:
        retryCfg.value.pool_source_block_max_failures,
      pool_source_block_window_sec:
        retryCfg.value.pool_source_block_window_sec,
      pool_source_block_cooldown_sec:
        retryCfg.value.pool_source_block_cooldown_sec,
      pool_source_heal_enabled:
        retryCfg.value.pool_source_heal_enabled,
    })
    ElMessage.success('重试配置已保存')
  } catch {
    // request 层已弹错误提示
  } finally {
    retryCfgSaving.value = false
  }
}

// ------------------------------------------------------------ 激励结算（PR-6）

const rewardCfg = ref<PoolRewardConfig | null>(null)
const rewardCfgSaving = ref(false)
const rewardPeriod = ref('')
const preview = ref<PoolRewardPreview | null>(null)
const previewLoading = ref(false)
const settling = ref(false)
const settlements = ref<PoolRewardSettlement[]>([])
const rewardDetails = ref<PoolRewardDetail[]>([])
const detailSettlementId = ref<number | null>(null)
const detailsLoading = ref(false)
const releasingId = ref<number | null>(null)

const REWARD_STATUS_LABELS: Record<string, string> = {
  shadow: '影子（只算账）',
  pending: '待发',
  frozen: '冻结待审',
  paid: '已发放',
  skipped: '不结',
}

const REWARD_REASON_LABELS: Record<string, string> = {
  below_threshold: '门槛不达',
  ejected_violation: '违规清退',
  daily_play_freeze: '单日激增冻结',
  user_missing: '账号不存在',
  sub_account: '子账号不参与',
}

/** 批 2 收编：激励预览 / 结算批次 / 批次明细 / 条目副本四张表一律 DataTable，
 * 列序、列宽与文案沿原裸表口径（原 stripe 斑马纹随收编退场，模具无斑马纹）。 */
const previewColumns: DataColumn[] = [
  { key: 'contributor', label: '贡献者', minWidth: 120, mobile: 'title' },
  { key: 'plays', label: '计奖起播 / 原始', minWidth: 120 },
  { key: 'active_days', label: '有效在线天', width: 100 },
  { key: 'amount_usd', label: '应发金额（$）', width: 110 },
  { key: 'cap_usd', label: '月封顶（$）', width: 100 },
  { key: 'would_status', label: '预计走向', minWidth: 130 },
]

const settlementColumns: DataColumn[] = [
  { key: 'period_key', label: '周期', width: 110, mobile: 'title' },
  { key: 'status', label: '批次状态', width: 110 },
  { key: 'counts', label: '成员 / 已发 / 冻结 / 不结', minWidth: 170 },
  { key: 'paid_amount_total', label: '已发金额（$）', width: 110 },
  { key: 'created_at', label: '建批时间', minWidth: 120 },
  { key: 'actions', label: '操作', width: 100 },
]

const detailColumns: DataColumn[] = [
  { key: 'contributor', label: '贡献者', minWidth: 120, mobile: 'title' },
  { key: 'counted_plays', label: '计奖起播', width: 90 },
  { key: 'active_days', label: '在线天', width: 80 },
  { key: 'amount', label: '应发 / 实发（$）', width: 130 },
  { key: 'transfer_bytes', label: '转存（对账）', width: 110 },
  { key: 'status', label: '状态', minWidth: 140 },
  { key: 'actions', label: '操作', width: 100 },
]

const copyColumns: DataColumn[] = [
  { key: 'id', label: '副本 ID', width: 90, mobile: 'title' },
  { key: 'item', label: '条目', minWidth: 160 },
  { key: 'member_id', label: '成员', width: 90 },
  { key: 'status', label: '状态', width: 100 },
  { key: 'size_bytes', label: '大小', width: 110 },
  { key: 'last_played_at', label: '最近播放', minWidth: 120 },
  { key: 'actions', label: '操作', width: 100 },
]

/** 按条目查副本：FilterBar 自定义控件（条目 ID 输入框沿原口径，回车即查） */
const copiesFilterConfigs = computed<FilterConfig[]>(() => [
  { key: 'item_id', label: '条目 ID', type: 'custom', value: copiesItemId.value },
])

function normalizeRewardCfg(cfg: PoolRewardConfig): PoolRewardConfig {
  // 美金额键后端按字符串下发（Decimal 落库口径），表单要数字。
  cfg.pool_reward_usd_per_play = Number(cfg.pool_reward_usd_per_play)
  cfg.pool_reward_usd_per_active_day =
    Number(cfg.pool_reward_usd_per_active_day)
  cfg.pool_reward_cap_ratio = Number(cfg.pool_reward_cap_ratio)
  return cfg
}

async function loadRewards() {
  try {
    const [cfg, list] = await Promise.all([
      fetchPoolRewardConfig(), fetchPoolRewardSettlements(),
    ])
    rewardCfg.value = normalizeRewardCfg(cfg)
    settlements.value = list.settlements
  } catch {
    // 激励区加载失败不拖垮成员区；request 层已弹提示
  }
}

async function doSaveRewardCfg() {
  if (!rewardCfg.value) return
  rewardCfgSaving.value = true
  try {
    rewardCfg.value = normalizeRewardCfg(await savePoolRewardConfig({
      pool_reward_enabled: rewardCfg.value.pool_reward_enabled,
      pool_reward_payout_enabled: rewardCfg.value.pool_reward_payout_enabled,
      pool_reward_usd_per_play: rewardCfg.value.pool_reward_usd_per_play,
      pool_reward_usd_per_active_day:
        rewardCfg.value.pool_reward_usd_per_active_day,
      pool_reward_cap_ratio: rewardCfg.value.pool_reward_cap_ratio,
      pool_reward_min_active_days: rewardCfg.value.pool_reward_min_active_days,
      pool_reward_min_counted_plays:
        rewardCfg.value.pool_reward_min_counted_plays,
    }))
    ElMessage.success('激励配置已保存')
  } catch {
    // request 层已弹错误提示
  } finally {
    rewardCfgSaving.value = false
  }
}

async function doPreview() {
  previewLoading.value = true
  try {
    preview.value = await fetchPoolRewardPreview(
      rewardPeriod.value.trim() || undefined)
  } catch {
    // request 层已弹错误提示
  } finally {
    previewLoading.value = false
  }
}

async function doSettle() {
  try {
    await ElMessageBox.confirm(
      '按当前配置执行这一期结算。真发开关开着时会真的入账钱包余额；' +
      '关着时只落影子行对账。重复执行不会重复发钱。',
      '执行激励结算',
      { confirmButtonText: '执行结算', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  settling.value = true
  try {
    const res = await settlePoolRewards(rewardPeriod.value.trim() || undefined)
    ElMessage.success(`结算完成：${res.settlement.period_key}`)
    detailSettlementId.value = null
    rewardDetails.value = []
    await loadRewards()
  } catch {
    // request 层已弹错误提示
  } finally {
    settling.value = false
  }
}

async function viewDetails(settlement: PoolRewardSettlement) {
  detailsLoading.value = true
  try {
    const res = await fetchPoolRewardDetails(settlement.id)
    rewardDetails.value = res.details
    detailSettlementId.value = settlement.id
  } catch {
    // request 层已弹错误提示
  } finally {
    detailsLoading.value = false
  }
}

async function doReleaseDetail(detail: PoolRewardDetail) {
  try {
    await ElMessageBox.confirm(
      '放行后按冻结时已计（封顶后）的数发放；真发开关关着时先落影子行，' +
      '开闸重跑后补发。',
      '放行冻结明细',
      { confirmButtonText: '放行', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  releasingId.value = detail.id
  try {
    const updated = await releasePoolRewardDetail(detail.id)
    ElMessage.success('已放行')
    const idx = rewardDetails.value.findIndex(d => d.id === updated.id)
    if (idx >= 0) rewardDetails.value[idx] = updated
    await loadRewards()
  } catch {
    // request 层已弹错误提示
  } finally {
    releasingId.value = null
  }
}

onMounted(() => {
  void load()
  void loadRewards()
  void loadJanitorCfg()
  void loadRetryCfg()
})
</script>

<template>
  <div class="pool-page">
    <PageHeader
      eyebrow="媒体"
      title="贡献池"
      description="公共贡献池成员管理：候选资格、审核、暂停/恢复、清退与副本下线。池开关与限额在「系统设置 · 公共贡献池」里改。"
    >
      <template #actions>
        <el-button :loading="loading" :icon="RefreshCw" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <SectionCard v-if="loadError && !overview">
      <EmptyState compact :icon="AlertTriangle" title="读取贡献池状态失败" :description="loadError">
        <template #actions><el-button :loading="loading" @click="load">重试</el-button></template>
      </EmptyState>
    </SectionCard>

    <template v-else-if="overview">
      <div class="pool-stats">
        <StatTile label="在池成员" :value="overview.members_active" :icon="Users" />
        <StatTile
          label="待审 / 候选"
          :value="overview.members_pending + overview.members_candidate"
          :hint="`待审 ${overview.members_pending} · 候选 ${overview.members_candidate}`"
          :tone="overview.members_pending ? 'warn' : 'plain'"
          :icon="Users"
        />
        <StatTile
          label="池副本"
          :value="overview.copies_total"
          :hint="`占用 ${fmtBytes(overview.copies_bytes)}`"
          :icon="Database"
        />
        <StatTile
          label="今日起播"
          :value="overview.today_play_starts"
          :hint="`今日转存 ${overview.today_transfer_jobs} 单 · ${fmtBytes(overview.today_transfer_bytes)}`"
          :icon="Play"
        />
        <StatTile
          label="熔断中"
          :value="overview.members_breaker"
          :hint="`已暂停共 ${overview.members_suspended} 个`"
          :tone="overview.members_breaker ? 'warn' : 'plain'"
          :icon="AlertTriangle"
        />
      </div>

      <div v-if="overview.members_pending > 0" class="pool-pending-banner" role="alert">
        <AlertTriangle :size="18" class="pool-pending-banner__icon" />
        <p class="pool-pending-banner__text">
          <strong>{{ overview.members_pending }} 个盘待批准</strong>
          <span>在下方成员卡里处理：带「待批准」角标的就是新申请，逐个号批准或拒绝。</span>
        </p>
      </div>

      <SectionCard title="授予候选资格" :icon="Flag" description="给某个站内账号预批入池资格：他绑盘时走池成员上限（默认 10 个），绑定后申请会挂到这行上。">
        <div class="pool-grant">
          <el-input-number
            v-model="grantUserId"
            :min="1"
            :controls="false"
            placeholder="站内用户 ID"
            class="pool-grant-input"
          />
          <el-button type="primary" :loading="granting" @click="doGrant">授予候选资格</el-button>
        </div>
      </SectionCard>

      <SectionCard title="成员" :icon="Users" :meta="`${contributorGroups.length} 位贡献者 · ${members.length} 个号`" description="一人一卡：卡头是这个人一共贡献了几个号、几个待批准；每个号一行明细，操作只作用于那一个号。">
        <div v-if="contributorGroups.length" class="pool-groups">
          <article
            v-for="g in contributorGroups"
            :key="g.userId"
            class="pool-group"
            :class="{ 'has-pending': g.pendingCount > 0 }"
          >
            <header class="pool-group-head">
              <div class="pool-group-who">
                <span class="pool-group-name">{{ g.username ?? '—' }}</span>
                <span class="pool-cell-sub">#{{ g.userId }}</span>
                <span class="pool-chip">共 {{ g.rows.length }} 个号</span>
                <span v-if="g.pendingCount > 0" class="pool-badge-pending">
                  {{ g.pendingCount }} 个待批准
                </span>
              </div>
              <div class="pool-group-summary">{{ g.summary }}</div>
            </header>
            <div
              v-for="row in g.rows"
              :key="row.id"
              class="pool-acct"
              :class="{ 'is-pending': row.status === 'pending' }"
            >
              <div class="pool-acct-top">
                <div class="pool-acct-id">
                  <template v-if="row.binding_id != null">
                    <span class="pool-cell-main">{{ row.account_label ?? '（未命名）' }}</span>
                    <span class="pool-cell-sub">
                      {{ row.provider ?? '—' }} · 绑定 #{{ row.binding_id }}
                      <template v-if="row.binding_status"> · 绑定{{ row.binding_status === 'active' ? '正常' : '已失效' }}</template>
                    </span>
                  </template>
                  <template v-else>
                    <span class="pool-cell-main">候选资格 · 未绑盘</span>
                    <span class="pool-cell-sub">还没绑定网盘，绑盘后申请会挂到这个号上</span>
                  </template>
                </div>
                <span class="pool-status" :class="`is-${row.status}`">
                  {{ STATUS_LABELS[row.status] ?? row.status }}
                </span>
              </div>
              <div v-if="row.suspended_reason" class="pool-cell-sub">
                暂停原因：{{ REASON_LABELS[row.suspended_reason] ?? row.suspended_reason }}
                <template v-if="row.breaker_until"> · 熔断至 {{ fmtTime(row.breaker_until) }}</template>
              </div>
              <div v-if="row.violation_flagged" class="pool-viol">
                违规 · {{ row.violation_reason ?? '未写原因' }}
              </div>
              <dl class="pool-acct-meta">
                <div class="pool-meta-item">
                  <dt>池占用 / 上限</dt>
                  <dd>
                    {{ fmtBytes(row.copies_bytes) }} / {{ fmtBytes(row.effective_capacity_bytes) }}{{ row.capacity_cap_bytes != null ? '（单盘覆盖）' : '' }}
                  </dd>
                </div>
                <div class="pool-meta-item">
                  <dt>副本</dt>
                  <dd>{{ row.copies_count }} 份</dd>
                </div>
                <div class="pool-meta-item">
                  <dt>今日转存</dt>
                  <dd>{{ row.today_transfer_jobs }} 单 · {{ fmtBytes(row.today_transfer_bytes) }}</dd>
                </div>
                <div class="pool-meta-item">
                  <dt>今日起播 / 额度命中</dt>
                  <dd>{{ row.today_play_starts }} 次 · {{ row.today_quota_hits }} 次</dd>
                </div>
                <div class="pool-meta-item">
                  <dt>加入</dt>
                  <dd>{{ fmtTime(row.joined_at ?? row.created_at) }}</dd>
                </div>
              </dl>
              <div class="pool-actions">
                <el-button
                  v-if="row.status === 'pending'"
                  size="small" type="primary" :loading="actingId === row.id"
                  @click="runAction(row.id, () => approvePoolMember(row.id), '已批准')"
                >批准</el-button>
                <el-button
                  v-if="row.status === 'pending' || row.status === 'candidate'"
                  size="small" :loading="actingId === row.id"
                  @click="confirmDo('拒绝申请', '拒绝后这个号落为已拒绝，贡献者可以重新申请。', () => rejectPoolMember(row.id), '已拒绝', row.id)"
                >拒绝</el-button>
                <el-button
                  v-if="row.status === 'active'"
                  size="small" :loading="actingId === row.id"
                  @click="confirmDo('暂停成员', '暂停后这块盘不再接新单，它的副本暂停下发（有第二副本时观众走第二副本）。', () => suspendPoolMember(row.id), '已暂停', row.id)"
                >暂停</el-button>
                <el-button
                  v-if="row.status === 'suspended'"
                  size="small" type="primary" :loading="actingId === row.id"
                  @click="runAction(row.id, () => resumePoolMember(row.id), '已恢复')"
                >恢复</el-button>
                <el-button
                  v-if="!['exited', 'rejected', 'ejected'].includes(row.status)"
                  size="small" :loading="actingId === row.id"
                  @click="editCapacity(row)"
                >上限</el-button>
                <el-button
                  v-if="!['exited', 'rejected', 'ejected'].includes(row.status)"
                  size="small" type="danger" plain :loading="actingId === row.id"
                  @click="openEject(row)"
                >清退</el-button>
                <el-button
                  v-if="row.violation_flagged"
                  size="small" :loading="actingId === row.id"
                  @click="runAction(row.id, () => clearPoolMemberViolation(row.id), '违规标志已清除')"
                >清除违规</el-button>
              </div>
            </div>
          </article>
        </div>
        <EmptyState
          v-if="!members.length"
          compact :icon="Users" title="暂无成员"
          description="先在上方授予候选资格，或等贡献者从用户端申请。"
        />
      </SectionCard>

      <SectionCard title="巡检清理" :icon="Brush" description="巡检每 30 分钟一轮：闲置主动清理开时，不等成员盘容量紧张，闲置超过「闲置淘汰天数」（系统设置 · 公共贡献池，默认 7 天）的池副本直接清掉；正在播的与保留期内的副本不动，每成员每轮最多清 20 份。">
        <template v-if="janitorCfg">
          <div class="pool-grant">
            <el-switch v-model="janitorCfg.pool_idle_evict_proactive_enabled" />
            <span>闲置主动清理</span>
            <el-button type="primary" :loading="janitorCfgSaving" @click="doSaveJanitorCfg">保存巡检配置</el-button>
          </div>
        </template>
      </SectionCard>

      <SectionCard title="转存重试" :icon="RefreshCw" description="池转存读不到源文件（copy_forbidden）时不再一刀切放弃：自动排除失败的那块盘、换其他成员盘重试，全部可用盘试完才算失败；失败冷却也不再一律 24 小时。换盘总尝试上限 0 ＝以可用成员盘试完为界。条目级源熔断：同一部片在计数窗口内读源失败达阈值（或全部成员盘都试过），整片暂停建单一个阻断冷却期，期满自动放一个探测单试恢复；阈值 0 ＝只按全部试过触发。">
        <template v-if="retryCfg">
          <div class="pool-grant">
            <span class="pool-cell-sub">失败冷却（秒）</span>
            <el-input-number v-model="retryCfg.pool_copy_forbidden_cooldown_sec" :min="60" :max="86400" :controls="false" class="pool-num-input" />
            <span class="pool-cell-sub">换盘总尝试上限（0=试完所有可用盘）</span>
            <el-input-number v-model="retryCfg.pool_copy_forbidden_max_attempts" :min="0" :max="64" :controls="false" class="pool-num-input" />
            <span class="pool-cell-sub">源熔断失败阈值（窗口内，0=关）</span>
            <el-input-number v-model="retryCfg.pool_source_block_max_failures" :min="0" :max="1000" :controls="false" class="pool-num-input" />
            <span class="pool-cell-sub">源熔断计数窗口（秒）</span>
            <el-input-number v-model="retryCfg.pool_source_block_window_sec" :min="300" :max="604800" :controls="false" class="pool-num-input" />
            <span class="pool-cell-sub">源熔断阻断冷却（秒）</span>
            <el-input-number v-model="retryCfg.pool_source_block_cooldown_sec" :min="300" :max="604800" :controls="false" class="pool-num-input" />
            <el-switch v-model="retryCfg.pool_source_heal_enabled" />
            <span>源自愈（读不到源时先补公开分享再重试）</span>
            <el-button type="primary" :loading="retryCfgSaving" @click="doSaveRetryCfg">保存重试配置</el-button>
          </div>
        </template>
      </SectionCard>

      <SectionCard title="激励结算" :icon="Coins" description="贡献者激励按周期结算：先影子跑对账（只算账落行、不发钱），核对无误再开真发开关。计奖口径：每次起播 $0.005 + 每有效在线日 $0.025（默认，可改）入账钱包余额，门槛＝在线 ≥7 天且起播 ≥10；每月入账上限＝本人套餐月价 × 封顶比例（无付费套餐按在售最低档兜底）。">
        <template v-if="rewardCfg">
          <div class="pool-grant">
            <el-switch v-model="rewardCfg.pool_reward_enabled" />
            <span>激励总开关</span>
            <el-switch v-model="rewardCfg.pool_reward_payout_enabled" />
            <span>真发开关（关＝影子跑）</span>
            <el-button type="primary" :loading="rewardCfgSaving" @click="doSaveRewardCfg">保存激励配置</el-button>
          </div>
          <div class="pool-grant" style="margin-top: 10px">
            <span class="pool-cell-sub">每起播奖励（$）</span>
            <el-input-number v-model="rewardCfg.pool_reward_usd_per_play" :min="0" :step="0.005" :precision="4" :controls="false" class="pool-num-input" />
            <span class="pool-cell-sub">每在线日奖励（$）</span>
            <el-input-number v-model="rewardCfg.pool_reward_usd_per_active_day" :min="0" :step="0.005" :precision="4" :controls="false" class="pool-num-input" />
            <span class="pool-cell-sub">月封顶比例（×套餐月价）</span>
            <el-input-number v-model="rewardCfg.pool_reward_cap_ratio" :min="0" :step="0.1" :precision="2" :controls="false" class="pool-num-input" />
            <span class="pool-cell-sub">门槛在线天</span>
            <el-input-number v-model="rewardCfg.pool_reward_min_active_days" :min="0" :controls="false" class="pool-num-input" />
            <span class="pool-cell-sub">门槛起播</span>
            <el-input-number v-model="rewardCfg.pool_reward_min_counted_plays" :min="0" :controls="false" class="pool-num-input" />
          </div>
        </template>
        <div class="pool-grant" style="margin-top: 14px">
          <el-input
            v-model="rewardPeriod"
            placeholder="周期键，如 2026-11（留空＝上一完整周期）"
            class="pool-grant-input"
            clearable
          />
          <el-button :loading="previewLoading" @click="doPreview">结算预览</el-button>
          <el-button type="primary" :loading="settling" @click="doSettle">执行结算</el-button>
        </div>
        <template v-if="preview">
          <p class="pool-dialog-text" style="margin-top: 12px">
            周期 {{ preview.period_key }}（{{ preview.period_start.slice(0, 10) }} 至 {{ preview.period_end.slice(0, 10) }}）；
            激励总开关{{ preview.reward_enabled ? '开' : '关' }}、真发{{ preview.payout_enabled ? '开' : '关（影子）' }}。
            预览只算账、不落行。
          </p>
          <DataTable
            v-if="preview.rows.length"
            :rows="preview.rows"
            :columns="previewColumns"
            row-key="member_id"
            class="pool-table"
          >
            <template #cell-contributor="{ row }">{{ row.username ?? `#${row.user_id}` }}</template>
            <template #cell-plays="{ row }">{{ row.counted_plays }} / {{ row.raw_plays }}</template>
            <template #cell-would_status="{ row }">
              {{ REWARD_STATUS_LABELS[row.would_status] ?? row.would_status }}
              <span v-if="row.reason" class="pool-cell-sub">（{{ REWARD_REASON_LABELS[row.reason] ?? row.reason }}）</span>
            </template>
          </DataTable>
          <EmptyState
            v-if="!preview.rows.length"
            compact :icon="Coins" title="这一期没有可结算的成员"
            description="还没有成员真正进过池，或本期没有任何起播计量。"
          />
        </template>
        <DataTable
          v-if="settlements.length"
          :rows="settlements"
          :columns="settlementColumns"
          class="pool-table"
          style="margin-top: 14px"
        >
          <template #cell-status="{ row }">
            {{ row.status }}{{ row.shadow ? '（影子建批）' : '' }}
          </template>
          <template #cell-counts="{ row }">
            {{ row.members }} / {{ row.paid }} / {{ row.frozen }} / {{ row.skipped }}
          </template>
          <template #cell-created_at="{ row }">{{ fmtTime(row.created_at) }}</template>
          <template #cell-actions="{ row }">
            <el-button size="small" :loading="detailsLoading" @click="viewDetails(row)">明细</el-button>
          </template>
        </DataTable>
        <template v-if="detailSettlementId != null">
          <p class="pool-dialog-text" style="margin-top: 12px">
            批次 #{{ detailSettlementId }} 明细：冻结行核对后可放行（按已计封顶数发）。
          </p>
          <DataTable :rows="rewardDetails" :columns="detailColumns" class="pool-table">
            <template #cell-contributor="{ row }">{{ row.username ?? `#${row.user_id}` }}</template>
            <template #cell-amount="{ row }">{{ row.amount_usd }} / {{ row.credited_usd }}</template>
            <template #cell-transfer_bytes="{ row }">{{ fmtBytes(row.transfer_bytes) }}</template>
            <template #cell-status="{ row }">
              {{ REWARD_STATUS_LABELS[row.status] ?? row.status }}
              <span v-if="row.reason" class="pool-cell-sub">（{{ REWARD_REASON_LABELS[row.reason] ?? row.reason }}）</span>
            </template>
            <template #cell-actions="{ row }">
              <el-button
                v-if="row.status === 'frozen'"
                size="small" type="primary" :loading="releasingId === row.id"
                @click="doReleaseDetail(row)"
              >放行</el-button>
            </template>
          </DataTable>
        </template>
      </SectionCard>

      <SectionCard title="按条目查副本" :icon="HardDrive" description="看某个条目的池副本都在哪些盘上；可单份手动下线（停发 + 尝试删文件）。">
        <FilterBar
          :filters="copiesFilterConfigs"
          :loading="copiesLoading"
          query-button
          @query="loadCopies"
        >
          <template #filter-item_id>
            <el-input-number
              v-model="copiesItemId"
              :min="1"
              :controls="false"
              placeholder="条目 ID"
              class="pool-grant-input"
              @keyup.enter="loadCopies"
            />
          </template>
        </FilterBar>
        <DataTable
          v-if="copiesQueried && copies.length"
          :rows="copies"
          :columns="copyColumns"
          class="pool-table"
        >
          <template #cell-id="{ row }">#{{ row.id }}</template>
          <template #cell-item="{ row }">{{ row.item_name ?? `#${row.item_id}` }}</template>
          <template #cell-member_id="{ row }">{{ row.member_id ?? '—' }}</template>
          <template #cell-size_bytes="{ row }">{{ fmtBytes(row.size_bytes) }}</template>
          <template #cell-last_played_at="{ row }">{{ fmtTime(row.last_played_at) }}</template>
          <template #cell-actions="{ row }">
            <el-button
              v-if="row.status === 'ready' || row.status === 'orphan'"
              size="small" type="danger" plain
              @click="doReleaseCopy(row)"
            >下线</el-button>
          </template>
        </DataTable>
        <EmptyState
          v-if="copiesQueried && !copies.length"
          compact :icon="HardDrive" title="这个条目没有池副本"
          description="还没人通过公共池看过它，或副本已全部下线。"
        />
      </SectionCard>
    </template>

    <el-dialog v-model="ejectVisible" title="清退成员" width="440px" :close-on-click-modal="false">
      <template v-if="ejectTarget">
        <p class="pool-dialog-text">
          清退 {{ ejectTarget.username ?? ejectTarget.user_id }} 的盘
          「{{ ejectTarget.account_label ?? '候选资格 · 未绑盘' }}」后：这个号落终态，
          它的池副本会停发并尝试删除（该贡献者的其它号不受影响）。
          清退不可恢复（重新入池要重新走申请）。
        </p>
        <div class="pool-eject-form">
          <el-switch v-model="ejectViolation" />
          <span>这次清退属于违规清退</span>
        </div>
        <p v-if="ejectViolation" class="pool-dialog-text">
          勾上违规：该成员本期激励不结（违规清退口径）；普通清退不勾，历史贡献照常结算。
        </p>
        <el-input
          v-if="ejectViolation"
          v-model="ejectReason"
          placeholder="违规原因（留痕备查）"
          maxlength="200"
        />
      </template>
      <template #footer>
        <el-button @click="ejectVisible = false">取消</el-button>
        <el-button type="danger" :loading="ejecting" @click="doEject">确定清退</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.pool-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.pool-grant {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.pool-grant-input {
  width: 200px;
}
.pool-num-input {
  width: 90px;
}
.pool-table {
  width: 100%;
}
.pool-cell-main {
  font-weight: 600;
  color: var(--au-text);
}
.pool-cell-sub {
  font-size: var(--ax-fs-aux-sm);
  color: var(--au-text-3);
}
.pool-status {
  font-weight: 600;
}
.pool-status.is-active {
  color: var(--au-success);
}
.pool-status.is-pending,
.pool-status.is-candidate {
  color: var(--au-warning);
}
.pool-status.is-suspended,
.pool-status.is-ejected {
  color: var(--au-danger);
}
.pool-viol {
  font-size: var(--ax-fs-aux-sm);
  color: var(--au-danger);
}
.pool-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}
/* 待批准总览横幅：有新申请时一眼可见 */
.pool-pending-banner {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 16px;
  margin-bottom: 16px;
  background: var(--au-warning-soft);
  border: 1px solid var(--au-warning-border);
  border-radius: var(--au-r-lg);
}
.pool-pending-banner__icon {
  flex-shrink: 0;
  margin-top: 2px;
  color: var(--au-warning);
}
.pool-pending-banner__text {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  margin: 0;
  font-size: var(--ax-fs-aux);
  line-height: 1.55;
  color: var(--au-text-2);
}
.pool-pending-banner__text strong {
  color: var(--au-text);
}
/* 成员区：一人一卡的流式布局（不用宽表，避免手机横滚 + 固定列遮挡） */
.pool-groups {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}
.pool-group {
  min-width: 0;
  background: var(--au-surface-2);
  border: 1px solid var(--au-border);
  border-radius: var(--au-r-lg);
  overflow: hidden;
}
.pool-group.has-pending {
  border-color: var(--au-warning-border);
}
.pool-group-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 6px 12px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--au-border);
}
.pool-group-who {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 8px;
  min-width: 0;
}
.pool-group-name {
  font-size: var(--ax-fs-body);
  font-weight: 700;
  color: var(--au-text);
  overflow-wrap: anywhere;
}
.pool-chip {
  padding: 2px 8px;
  font-size: var(--ax-fs-aux-sm);
  font-weight: 600;
  color: var(--au-text-2);
  background: var(--au-surface-3);
  border-radius: var(--au-r-full);
}
.pool-badge-pending {
  padding: 2px 10px;
  font-size: var(--ax-fs-aux-sm);
  font-weight: 700;
  color: var(--au-on-warning);
  background: var(--au-warning);
  border-radius: var(--au-r-full);
}
.pool-group-summary {
  font-size: var(--ax-fs-aux-sm);
  color: var(--au-text-3);
  overflow-wrap: anywhere;
}
.pool-acct {
  padding: 12px 14px;
}
.pool-acct + .pool-acct {
  border-top: 1px solid var(--au-border);
}
.pool-acct.is-pending {
  background: var(--au-warning-soft);
}
.pool-acct-top {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 4px 12px;
}
.pool-acct-id {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.pool-acct-id .pool-cell-main,
.pool-acct-id .pool-cell-sub {
  overflow-wrap: anywhere;
}
.pool-acct-meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 6px 16px;
  margin: 10px 0 0;
}
.pool-meta-item {
  min-width: 0;
}
.pool-meta-item dt {
  font-size: var(--ax-fs-micro);
  color: var(--au-text-4);
}
.pool-meta-item dd {
  margin: 2px 0 0;
  font-size: var(--ax-fs-aux-sm);
  color: var(--au-text-2);
  overflow-wrap: anywhere;
}
.pool-dialog-text {
  color: var(--au-text-2);
  line-height: 1.6;
  margin: 0 0 12px;
}
.pool-eject-form {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
</style>
