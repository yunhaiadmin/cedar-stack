<script setup lang="ts">
/**
 * 会员卡码管理（v2.6.0 / v2.6.11 改版；Batch F 码体系正名：卡码 → 会员卡码）
 *
 * 借鉴 twilight-kotomi 的 RegCode 体系：一套入口生成并管理
 * 续期码 / 白名单码 / 诱饵码 / 指名码，并给出运营总览。
 * （注册码已于 2026-11-15 整体下线：原「注册码」分区取消，本页只剩
 * 会员卡码；存量注册码行仍在同一张表里，统计的按类型行会如实显示，
 * 列表默认排除。）
 *
 * v2.6.11：改用 DataTable（桌面表格 / 手机卡片列表），并把页面里那套
 * `#737373`、`#a3a3a3` 之类的硬编码灰色换成主题令牌——这些写死的颜色既不符合
 * 后台主题，对比度也不达标（#737373 在深色上只有 3.4:1）。
 *
 * v2.6.24：会员卡码是**一个服一个**的——一张会员卡码开的是它所属服的会员（乙服的卡码
 * 在乙服的 EA 上才生效）。默认只看当前服，可切「全部服」汇总；生成时可以指定归属服。
 *
 * v2.54（暗房影院）：PageHeader（范围切换 + 生成入口）· StatTile 总览（诱饵命中才染危险色）·
 * 「按类型」SectionCard · 码表 flush + 统一工具条（筛选左、计数与查询右）；
 * 加载失败给可重试的错误态。
 *
 * Batch F：「注册模式」卡片已删——该设置早已在「系统设置 → 注册策略」有完整入口
 * （模式＋关闭提示＋注册限流），同一配置键只留一处写入口；本页只留一行跳转提示。
 * 生成弹窗改为两步向导：「你想送什么」→「码的用途」，机械项收进「高级」折叠。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  AlertTriangle, CalendarPlus, Copy, KeySquare, Layers, Plus, RefreshCw, ShieldAlert, TimerOff, Trash2,
} from 'lucide-vue-next'
import { EmptyState, PageHeader, SectionCard, StatTile } from '@/components/ui'
import {
  deleteRegistrationCode,
  fetchCodeList,
  fetchCodeRedemptions,
  fetchCodeStats,
  fetchWhitelistAccounts,
  generateCodes,
  patchRegistrationCode,
  revokeWhitelistAccount,
} from '@/api/admin'
import type { CodeRedemptionRow, WhitelistAccount } from '@/api/admin'
import type { CodeStats, RegistrationCode } from '@/types'
import { useRealmStore } from '@/stores/realm'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import FilterBar from '@/components/FilterBar.vue'
import type { AppliedFilter, FilterConfig } from '@/components/FilterBar.vue'

// 本组件原是「注册码 / 会员卡码」双变体共用（variant prop）；注册码
// 分区已随注册码下线（2026-11-15）取消，只剩会员卡码一态：列表整体
// 排除存量注册码（exclude_code_type=1）、生成只发续期/白名单码。

const realm = useRealmStore()
/** 统计范围：当前服（默认）或全部服 */
const scope = ref<'realm' | 'all'>('realm')
/** 可选归属服（后端下发，跨服汇总时也能用） */
const realmOptions = ref<{ id: number; name: string }[]>([])

const codes = ref<RegistrationCode[]>([])
const stats = ref<CodeStats | null>(null)
const loading = ref(false)
const total = ref(0)
/** 码表加载失败（区别于「还没有生成过会员卡码」） */
const loadError = ref(false)

const filters = ref({ code_type: 0, state: '', keyword: '' })
/** 最近一次发出查询时用的关键字：防抖 watch 据此跳过「已经查过」的值 */
let lastKeyword = ''

const genVisible = ref(false)
/** 生成向导当前步：0 你想送什么 / 1 码的用途 */
const genStep = ref(0)
/** 生成 / 行内动作进行中（按钮 loading，防重复点击） */
const genBusy = ref(false)
const rowBusyId = ref<number | null>(null)
const genForm = ref({
  code_type: 2 as 2 | 3,
  count: 5,
  days: 30,
  max_uses: 1,
  expires_days: 30,
  // M1：奖励类型（balance=钱包余额码，钱包批 1；welfare 公益资格码已下线）
  reward_type: 'subscription' as 'subscription' | 'discount' | 'balance',
  discount_pct: 85,
  /** 余额码面额（美元，字符串避免 float 丢精度） */
  balance_value: '50',
  algorithm: 'base32-20',
  is_decoy: false,
  target_username: '',
  note: '',
  /** 这批码开通哪个服的会员 */
  realm_id: null as number | null,
})
const generated = ref<{ code: string; days_text: string }[]>([])
const resultVisible = ref(false)

const ALGORITHMS = [
  { value: 'base32-20', label: 'Base32 · 20 位（推荐，便于手抄）' },
  { value: 'base32-16', label: 'Base32 · 16 位' },
  { value: 'hex-24', label: '十六进制 · 24 位' },
  { value: 'urlsafe-24', label: 'URL 安全 · 24 位' },
]

const TYPE_META: Record<number, { label: string; desc: string }> = {
  2: { label: '续期码', desc: '在当前到期时间上叠加天数' },
  3: { label: '白名单码', desc: '置为长期有效（永久）' },
}

/** 表格列定义：手机端只保留最关键的几列，其余（用量 / 指名 / 备注 / 使用者）收进详情，避免卡片过长 */
const columns = computed<DataColumn[]>(() => [
  { key: 'code', label: '会员卡码', minWidth: 210, mobile: 'title' },
  { key: 'code_type', label: '类型', width: 96 },
  // 会员卡码开哪个服的会员：跨服汇总时才需要这一列
  ...(scope.value === 'all'
    ? [{ key: 'realm', label: '归属服', minWidth: 120 } as DataColumn]
    : []),
  { key: 'days_text', label: '授予', width: 90 },
  { key: 'use_count', label: '用量', width: 84, mobile: 'hide' },
  { key: 'state', label: '状态', width: 92 },
  { key: 'target_username', label: '指名', width: 110, mobile: 'hide' },
  { key: 'expires_at', label: '有效期至', width: 150 },
  { key: 'note', label: '备注', minWidth: 120, mobile: 'hide' },
  { key: 'used_by', label: '使用者', minWidth: 150, mobile: 'hide' },
  { key: 'actions', label: '操作', width: 210, fixed: 'right', align: 'right' },
])

/** 核销流水小表（批 2 收编 DataTable：列与空态文案沿原裸表口径） */
const flowColumns: DataColumn[] = [
  { key: 'username', label: '使用者', minWidth: 160, mobile: 'title' },
  { key: 'redeemed_at', label: '核销时间', minWidth: 160 },
]

/** 白名单账号表（批 2 收编 DataTable：状态列由 is_permanent_active 派生，见模板插槽） */
const whitelistColumns: DataColumn[] = [
  { key: 'username', label: '账号', minWidth: 140, mobile: 'title' },
  { key: 'code', label: '核销的白名单码', minWidth: 170 },
  { key: 'subscription_longterm', label: '会员订阅', width: 100 },
  { key: 'state', label: '状态', width: 100 },
  { key: 'actions', label: '操作', width: 100 },
]

/** 归属服展示名（未标注 = 升级前的老会员卡码，按当前服算） */
function realmLabel(row: RegistrationCode): string {
  if (!row.realm_id) return '未标注'
  return row.realm_name || realmOptions.value.find((r) => r.id === row.realm_id)?.name || `#${row.realm_id}`
}

const dailyDefault = computed(() =>
  genForm.value.code_type === 3 ? '永久' : `${genForm.value.days} 天`)

const STATE_LABEL: Record<string, string> = {
  active: '可用',
  disabled: '已停用',
  expired: '已过期',
  used_up: '已用尽',
}

async function load() {
  loading.value = true
  loadError.value = false
  lastKeyword = filters.value.keyword
  try {
    // realm_id=0 → 全部服；其余按服过滤（后端以当前服作为兜底）
    const scopeId = scope.value === 'all' ? 0 : (realm.activeId ?? 0)
    const [list, stat] = await Promise.all([
      fetchCodeList({
        // 存量注册码整体排除：本页只看会员卡码
        exclude_code_type: 1,
        code_type: filters.value.code_type || undefined,
        state: filters.value.state || undefined,
        keyword: filters.value.keyword || undefined,
        realm_id: scopeId,
        limit: 200,
      }),
      fetchCodeStats(scopeId),
    ])
    codes.value = list.codes
    total.value = list.total
    stats.value = stat
    if (list.realms?.length) realmOptions.value = list.realms
  } catch {
    // 错误提示由 HTTP 拦截器统一处理；这里只记下失败，给出重试入口
    loadError.value = true
  } finally {
    loading.value = false
  }
}

// ---------- 白名单账号（核销过白名单码的永久账号） ----------
const whitelistAccounts = ref<WhitelistAccount[]>([])
const whitelistLoading = ref(false)
const revokeBusyId = ref<number | null>(null)

async function loadWhitelist() {
  whitelistLoading.value = true
  try {
    const res = await fetchWhitelistAccounts()
    whitelistAccounts.value = res.accounts
  } catch {
    // 错误提示由 HTTP 拦截器统一处理；名单加载失败不挡码表
  } finally {
    whitelistLoading.value = false
  }
}

async function onRevokeWhitelist(row: WhitelistAccount) {
  try {
    await ElMessageBox.confirm(
      `回收「${row.username}」的永久资格？其长期订阅会到期；` +
      '该账号自己购买/续费的有限期权益不受影响。',
      '回收白名单',
      { type: 'warning', confirmButtonText: '回收', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  revokeBusyId.value = row.user_id
  try {
    await revokeWhitelistAccount(row.user_id)
    ElMessage.success(`已回收 ${row.username} 的永久资格`)
    await loadWhitelist()
  } finally {
    revokeBusyId.value = null
  }
}

onMounted(() => {
  load()
  loadWhitelist()
})

const hasFilter = computed(() => !!(filters.value.code_type || filters.value.state || filters.value.keyword))

function resetFilters() {
  filters.value = { code_type: 0, state: '', keyword: '' }
  load()
}

/** 筛选一律 FilterBar（统一标准第 4 条）：关键字 400ms 防抖自动查（组件内防抖），
 * 点「查询」立即按当前草稿查；chips 只反映已提交条件（load 时落定的 filters）。 */
const codeFilterConfigs = computed<FilterConfig[]>(() => [
  {
    key: 'code_type', label: '类型', type: 'select', value: filters.value.code_type, placeholder: '全部类型',
    options: [
      { value: 0, label: '全部类型' },
      { value: 2, label: '续期码' },
      { value: 3, label: '白名单码' },
    ],
  },
  {
    key: 'state', label: '状态', type: 'select', value: filters.value.state, placeholder: '全部状态',
    options: [
      { value: '', label: '全部状态' },
      { value: 'active', label: '可用' },
      { value: 'disabled', label: '已停用' },
      { value: 'expired', label: '已过期' },
      { value: 'used_up', label: '已用尽' },
    ],
  },
])

const appliedFilters = computed<AppliedFilter[]>(() => {
  const items: AppliedFilter[] = []
  if (filters.value.keyword) {
    items.push({ key: 'keyword', label: '关键词', text: filters.value.keyword })
  }
  if (filters.value.code_type) {
    items.push({
      key: 'code_type', label: '类型',
      text: TYPE_META[filters.value.code_type]?.label ?? String(filters.value.code_type),
    })
  }
  if (filters.value.state) {
    items.push({ key: 'state', label: '状态', text: STATE_LABEL[filters.value.state] ?? filters.value.state })
  }
  return items
})

function onSearchUpdate(value: string) {
  filters.value.keyword = value
  if (value !== lastKeyword) load()
}

function onFilterChange(key: string, value: string | number | boolean) {
  if (key === 'code_type') filters.value.code_type = value === '' ? 0 : Number(value)
  else if (key === 'state') filters.value.state = String(value)
  else return
  load()
}

function onRemoveChip(key: string) {
  if (key === 'keyword') filters.value.keyword = ''
  else if (key === 'code_type') filters.value.code_type = 0
  else if (key === 'state') filters.value.state = ''
  else return
  load()
}

/** 类型徽章：注册 / 续期 / 白名单 三种各一档（不再用紫色） */
const TYPE_BADGE: Record<number, string> = { 1: 'au-badge-info', 2: 'au-badge-amber', 3: 'badge-warn' }

function openGenerate(codeType: 2 | 3) {
  genForm.value.code_type = codeType
  genForm.value.days = codeType === 2 ? 7 : 30
  genForm.value.is_decoy = false
  genForm.value.target_username = ''
  genStep.value = 0
  // 默认开当前服的会员（就是列表正在看的这个服）
  genForm.value.realm_id = realm.activeId ?? null
  genVisible.value = true
}

async function generate() {
  if (genBusy.value) return
  genBusy.value = true
  try {
    const res = await generateCodes({
      code_type: genForm.value.code_type,
      count: genForm.value.count,
      days: genForm.value.code_type === 3 ? -1 : genForm.value.days,
      max_uses: genForm.value.max_uses,
      expires_days: genForm.value.expires_days,
      reward_type: genForm.value.reward_type,
      discount_pct: genForm.value.discount_pct,
      balance_value: genForm.value.reward_type === 'balance' ? genForm.value.balance_value : undefined,
      algorithm: genForm.value.algorithm,
      is_decoy: genForm.value.is_decoy,
      target_username: genForm.value.target_username || undefined,
      note: genForm.value.note || undefined,
      realm_id: genForm.value.realm_id ?? undefined,
    })
    generated.value = res.codes.map((c) => ({ code: c.code, days_text: c.days_text }))
    genVisible.value = false
    resultVisible.value = true
    ElMessage.success(res.message)
    load()
  } catch {
    /* 写操作失败由请求拦截器统一弹错 */
  } finally {
    genBusy.value = false
  }
}

async function toggle(row: RegistrationCode) {
  rowBusyId.value = row.id
  try {
    await patchRegistrationCode(row.id, { is_active: !row.is_active })
    ElMessage.success(row.is_active ? '已停用' : '已启用')
    load()
  } catch {
    /* 写操作失败由请求拦截器统一弹错 */
  } finally {
    rowBusyId.value = null
  }
}

async function remove(row: RegistrationCode) {
  try {
    await ElMessageBox.confirm(`确认删除会员卡码 ${row.code}？`, '删除会员卡码', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return // 点了取消 / 关闭：不是错误
  }
  rowBusyId.value = row.id
  try {
    const res = await deleteRegistrationCode(row.id)
    ElMessage.success(res.message)
    load()
  } catch {
    /* 写操作失败由请求拦截器统一弹错 */
  } finally {
    rowBusyId.value = null
  }
}

// ---------- 核销流水：谁在何时核销了这张码 ----------
const flowVisible = ref(false)
const flowLoading = ref(false)
const flowCode = ref('')
const flowItems = ref<CodeRedemptionRow[]>([])

async function openFlow(row: RegistrationCode) {
  flowCode.value = row.code
  flowItems.value = []
  flowVisible.value = true
  flowLoading.value = true
  try {
    const res = await fetchCodeRedemptions(row.id)
    flowItems.value = res.items
  } catch {
    /* 失败提示由请求拦截器统一处理 */
  } finally {
    flowLoading.value = false
  }
}

/** 剪贴板只在安全上下文（https / localhost）可用：http 部署下 writeText 会直接 reject */
async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('浏览器不允许写入剪贴板，请手动选中复制')
  }
}

function fmtDate(s: string | null): string {
  if (!s) return '—'
  return s.slice(0, 16).replace('T', ' ')
}

function fmtDateTime(s: string | null): string {
  return s ? fmtDate(s) : '—'
}

function usedByNames(row: RegistrationCode): string {
  return row.used_by.map((u) => u.username).join('、')
}
</script>

<template>
  <div class="admin-page codes-page">
    <PageHeader
      eyebrow="经营"
      title="会员卡码管理"
      description="续期码 / 白名单码 / 诱饵码 / 指名码——生成与审计。会员卡码开的是它所属服的会员。"
    >
      <template #actions>
        <el-radio-group v-model="scope" @change="load">
          <el-radio-button value="realm">当前服</el-radio-button>
          <el-radio-button value="all">全部服</el-radio-button>
        </el-radio-group>
        <el-button :icon="RefreshCw" :loading="loading" aria-label="刷新" @click="load">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openGenerate(2)">生成会员卡码</el-button>
      </template>
    </PageHeader>

    <!-- 运营总览：数字走正文色，只有诱饵码被命中才染危险色 -->
    <section v-if="stats" class="stat-row" aria-label="会员卡码总览">
      <StatTile label="会员卡码总数" :value="stats.total" :icon="KeySquare" :hint="`可用 ${stats.active} · 停用 ${stats.disabled}`" />
      <StatTile
        label="已过期 / 已用尽"
        :value="stats.expired"
        :suffix="`/ ${stats.used_up}`"
        :icon="TimerOff"
        hint="到期与次数用尽的会员卡码"
      />
      <StatTile
        label="累计授予"
        :value="stats.days_granted"
        suffix="天"
        :icon="CalendarPlus"
        hint="不含白名单（永久）与诱饵码"
      />
      <StatTile
        label="诱饵码命中"
        :value="stats.decoy.triggered"
        :suffix="`/ ${stats.decoy.total}`"
        :icon="ShieldAlert"
        :tone="stats.decoy.triggered > 0 ? 'danger' : 'plain'"
        hint="命中即自动封禁使用者账号"
      />
    </section>

    <!-- 注册模式已搬家：同一配置键全站只留一处写入口（系统设置 → 注册策略，含模式＋关闭提示＋注册限流），这里只留跳转提示 -->
    <p class="moved-hint">
      「注册模式」设置已收敛到 <router-link to="/settings">系统设置 → 注册策略</router-link>，本页只管理会员卡码。
    </p>

    <!-- 按类型统计（全类型如实显示：存量注册码行也在内，只作审计口径） -->
    <SectionCard title="按类型" :icon="Layers" flush>
      <ul v-if="stats?.by_type.length" class="type-list">
        <li v-for="t in stats.by_type" :key="t.code_type" class="type-row">
          <span class="au-badge" :class="TYPE_BADGE[t.code_type] || 'au-badge-muted'">{{ t.code_type_name }}</span>
          <span class="type-num"><strong>{{ t.available }}</strong> 可用 / {{ t.total }} 总计</span>
          <span class="type-used">已核销 {{ t.used }} 次</span>
        </li>
      </ul>
      <EmptyState v-else compact title="还没有按类型的统计" description="生成第一批会员卡码后这里会分类统计。" />
    </SectionCard>

    <!-- 生成弹窗：两步向导——①你想送什么 ②码的用途；机械项收进「高级」折叠 -->
    <el-dialog v-model="genVisible" title="生成会员卡码" width="min(520px, 92vw)">
      <el-steps :active="genStep" simple class="gen-steps">
        <el-step title="你想送什么" />
        <el-step title="码的用途" />
      </el-steps>

      <!-- ① 你想送什么：奖励内容 -->
      <el-form v-show="genStep === 0" label-position="top" class="gen-step">
        <el-form-item label="奖励类型">
          <el-radio-group v-model="genForm.reward_type">
            <el-radio-button value="subscription">会员天数</el-radio-button>
            <el-radio-button value="discount">折扣券</el-radio-button>
            <el-radio-button value="balance">钱包余额</el-radio-button>
          </el-radio-group>
          <div class="form-hint">这批码送出去，对方能拿到什么</div>
        </el-form-item>
        <el-form-item v-if="genForm.reward_type === 'subscription'" label="会员天数">
          <el-input-number
            v-model="genForm.days"
            :min="1"
            :max="3650"
            :disabled="genForm.code_type === 3"
          />
          <span class="form-hint inline-hint">本次授予：{{ dailyDefault }}</span>
        </el-form-item>
        <el-form-item v-if="genForm.reward_type === 'discount'" label="折扣（实付百分比）">
          <el-input-number v-model="genForm.discount_pct" :min="1" :max="99" />
          <span class="form-hint inline-hint">如 85 = 八五折</span>
        </el-form-item>
        <el-form-item v-if="genForm.reward_type === 'balance'" label="余额面额（美元）">
          <el-input v-model="genForm.balance_value" placeholder="如 50" style="width: 160px" />
          <span class="form-hint inline-hint">核销后钱包到账这个金额；不能与白名单码组合</span>
        </el-form-item>
        <el-form-item label="数量">
          <el-input-number v-model="genForm.count" :min="1" :max="200" />
        </el-form-item>
        <!-- 会员卡码开哪个服的会员：码的归属服决定核销后开通哪个服 -->
        <el-form-item label="归属服">
          <el-select v-model="genForm.realm_id" placeholder="选择归属服" class="realm-select">
            <el-option v-for="r in realmOptions" :key="r.id" :label="r.name" :value="r.id" />
          </el-select>
          <div class="form-hint">核销后开通的是该服的会员，也只在该服的播放节点上生效。</div>
        </el-form-item>
      </el-form>

      <!-- ② 码的用途 -->
      <el-form v-show="genStep === 1" label-position="top" class="gen-step">
        <el-form-item label="码的用途">
          <el-radio-group v-model="genForm.code_type">
            <el-radio-button :value="2">续期码</el-radio-button>
            <el-radio-button :value="3">白名单码</el-radio-button>
          </el-radio-group>
          <div class="form-hint">{{ TYPE_META[genForm.code_type].desc }}</div>
        </el-form-item>
        <el-collapse class="gen-advanced">
          <el-collapse-item title="高级：次数 / 有效期 / 算法 / 指名 / 备注 / 诱饵码" name="advanced">
            <el-form-item label="每码次数">
              <el-input-number v-model="genForm.max_uses" :min="1" :max="1000" />
            </el-form-item>
            <el-form-item label="有效天数">
              <el-input-number v-model="genForm.expires_days" :min="1" :max="3650" />
              <div class="form-hint">会员卡码自身的兑换期限，过期作废</div>
            </el-form-item>
            <el-form-item label="随机算法">
              <el-select v-model="genForm.algorithm" style="width: 100%">
                <el-option v-for="a in ALGORITHMS" :key="a.value" :value="a.value" :label="a.label" />
              </el-select>
            </el-form-item>
            <el-form-item label="指名账号">
              <el-input v-model="genForm.target_username" placeholder="留空表示不限；填写后仅该账号可用" />
            </el-form-item>
            <el-form-item label="备注">
              <el-input v-model="genForm.note" placeholder="可选，如：2026 秋季活动" />
            </el-form-item>
            <el-form-item label="诱饵码">
              <el-switch v-model="genForm.is_decoy" />
              <div class="form-hint decoy-hint">
                <ShieldAlert :size="12" /> 蜜罐：在盗版渠道流通，使用即自动封禁账号
              </div>
            </el-form-item>
          </el-collapse-item>
        </el-collapse>
      </el-form>

      <template #footer>
        <el-button v-if="genStep === 1" @click="genStep = 0">上一步</el-button>
        <el-button @click="genVisible = false">取消</el-button>
        <el-button v-if="genStep === 0" type="primary" @click="genStep = 1">下一步</el-button>
        <el-button v-else type="primary" :loading="genBusy" @click="generate">生成</el-button>
      </template>
    </el-dialog>

    <!-- 生成结果 -->
    <el-dialog v-model="resultVisible" title="生成结果（点击复制）" width="min(460px, 92vw)">
      <div class="gen-list">
        <button v-for="c in generated" :key="c.code" class="gen-code" @click="copyText(c.code)">
          {{ c.code }}<span class="gen-days">{{ c.days_text }}</span>
        </button>
      </div>
      <template #footer>
        <el-button @click="copyText(generated.map((c) => c.code).join('\n'))">复制全部</el-button>
        <el-button type="primary" @click="resultVisible = false">完成</el-button>
      </template>
    </el-dialog>

    <!-- 码表：筛选在左、计数与查询在右；桌面表格 / 手机卡片 -->
    <SectionCard
      title="全部会员卡码"
      :icon="KeySquare"
      :meta="`共 ${total} 条${scope === 'realm' ? '（当前服）' : '（全部服）'}`"
      flush
    >
      <div class="view-toolbar">
        <FilterBar
          :search="{ value: filters.keyword, placeholder: '搜索会员卡码 / 备注 / 指名账号', mode: 'debounced', debounceMs: 400 }"
          :filters="codeFilterConfigs"
          :applied-filters="appliedFilters"
          :loading="loading"
          query-button
          @update:search="onSearchUpdate"
          @query="load"
          @filter-change="onFilterChange"
          @remove-chip="onRemoveChip"
          @clear="resetFilters"
        />
      </div>

      <EmptyState v-if="loadError && !codes.length" :icon="AlertTriangle" title="会员卡码加载失败" description="网络或服务暂时不可用，稍后重试。">
        <template #actions><el-button :loading="loading" @click="load">重试</el-button></template>
      </EmptyState>

      <DataTable v-else class="flush-table" :rows="codes" :columns="columns" :loading="loading" empty="还没有生成过会员卡码">
        <template #cell-code="{ row }">
          <div class="code-cell">
            <button type="button" class="code-chip" title="点击复制" @click.stop="copyText(row.code)">
              {{ row.code }}<Copy :size="12" />
            </button>
            <span v-if="row.is_decoy" class="au-badge au-badge-rose">诱饵</span>
          </div>
        </template>

        <template #cell-code_type="{ row }">
          <span class="au-badge" :class="TYPE_BADGE[row.code_type] || 'au-badge-muted'">{{ row.code_type_name }}</span>
        </template>

        <template #cell-realm="{ row }">
          <span class="au-badge" :class="row.realm_id ? 'au-badge-info' : 'au-badge-muted'">{{ realmLabel(row) }}</span>
        </template>

        <template #cell-days_text="{ row }">{{ row.days_text }}</template>

        <template #cell-use_count="{ row }"><span class="num">{{ row.use_count }} / {{ row.max_uses }}</span></template>

        <template #cell-state="{ row }">
          <span class="au-badge" :class="row.state === 'active' ? 'au-badge-green' : 'au-badge-muted'">
            {{ STATE_LABEL[row.state] || row.state }}
          </span>
        </template>

        <template #cell-target_username="{ row }">
          <span v-if="!row.target_username" class="faint">—</span>
          <span v-else>{{ row.target_username }}</span>
        </template>

        <template #cell-expires_at="{ row }"><span class="num">{{ fmtDateTime(row.expires_at) }}</span></template>

        <template #cell-note="{ row }">
          <span v-if="!row.note" class="faint">—</span>
          <span v-else>{{ row.note }}</span>
        </template>

        <template #cell-used_by="{ row }">
          <span v-if="row.used_by.length === 0" class="faint">—</span>
          <span v-else>{{ usedByNames(row) }}</span>
        </template>

        <template #cell-actions="{ row }">
          <el-button size="small" plain @click="openFlow(row)">流水</el-button>
          <el-button
            size="small"
            :type="row.is_active ? 'warning' : 'success'"
            plain
            :loading="rowBusyId === row.id"
            @click="toggle(row)"
          >
            {{ row.is_active ? '停用' : '启用' }}
          </el-button>
          <el-button size="small" type="danger" plain :icon="Trash2" :loading="rowBusyId === row.id" @click="remove(row)">
            删除
          </el-button>
        </template>

        <template #empty>
          <EmptyState
            compact
            :icon="KeySquare"
            :title="hasFilter ? '没有符合条件的会员卡码' : '还没有生成过会员卡码'"
            :description="hasFilter ? '换个条件，或清空筛选看全部。' : '用右上角「生成会员卡码」发一批续期码 / 白名单码。'"
          >
            <template #actions>
              <el-button v-if="hasFilter" size="small" @click="resetFilters">清空筛选</el-button>
              <el-button v-else size="small" type="primary" :icon="Plus" @click="openGenerate(2)">生成会员卡码</el-button>
            </template>
          </EmptyState>
        </template>
      </DataTable>
    </SectionCard>

    <!-- 核销流水：谁在何时核销了这张码 -->
    <el-dialog v-model="flowVisible" :title="`核销流水 · ${flowCode}`" width="min(480px, 92vw)">
      <DataTable
        :rows="flowItems"
        :columns="flowColumns"
        :loading="flowLoading"
        row-key="user_id"
        empty="还没有人用过这张码"
      >
        <template #cell-redeemed_at="{ row }">{{ fmtDateTime(row.redeemed_at) }}</template>
      </DataTable>
      <template #footer>
        <el-button type="primary" @click="flowVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <SectionCard title="白名单账号" :icon="ShieldAlert">
      <p class="whitelist-hint">
        核销过白名单码的账号：会员订阅为长期有效、不用续费。
        回收后长期订阅到期（账号自己购买/续费的有限期权益不受影响）。
      </p>
      <DataTable
        :rows="whitelistAccounts"
        :columns="whitelistColumns"
        :loading="whitelistLoading"
        row-key="user_id"
        empty="暂无白名单账号"
      >
        <template #cell-subscription_longterm="{ row }">{{ row.subscription_longterm ? '长期' : '—' }}</template>
        <template #cell-state="{ row }">{{ row.is_permanent_active ? '永久生效中' : '已回收' }}</template>
        <template #cell-actions="{ row }">
          <el-button
            v-if="row.is_permanent_active"
            size="small"
            type="danger"
            plain
            :loading="revokeBusyId === row.user_id"
            @click="onRevokeWhitelist(row)"
          >
            回收
          </el-button>
        </template>
      </DataTable>
    </SectionCard>
  </div>
</template>

<style scoped>
.stat-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.whitelist-hint {
  margin: 0 0 10px;
  font-size: 12.5px;
  color: var(--au-text-3);
}

/* 注册模式搬家提示：只留一行跳转，不留第二处写入口 */
.moved-hint {
  margin: 0 0 12px;
  font-size: 12.5px;
  color: var(--au-text-3);
}
.moved-hint a { color: var(--au-primary); }

/* 生成向导：两步 */
.gen-steps { margin-bottom: 18px; }
.gen-step { min-height: 280px; }
.gen-advanced { margin-top: 8px; }

.type-list { list-style: none; margin: 0; padding: 0; }
.type-row {
  display: flex;
  align-items: center;
  gap: 8px 12px;
  flex-wrap: wrap;
  padding: 10px 20px;
  font-size: 12.5px;
}
.type-row + .type-row { border-top: 1px solid var(--au-border); }
.type-num { flex: 1 1 auto; color: var(--au-text-2); font-variant-numeric: tabular-nums; }
.type-num strong { color: var(--au-text); font-weight: 700; }
.type-used { color: var(--au-text-4); font-variant-numeric: tabular-nums; }

/* ---------- 工具条：筛选在左、动作在右 ---------- */
.view-toolbar {
  padding: 4px 20px 14px;
  border-bottom: 1px solid var(--au-border);
}

.flush-table :deep(.dt-cards) { padding: 12px 12px 8px; }

/* ---------- 单元格 ---------- */
.num { font-variant-numeric: tabular-nums; }
.faint { color: var(--au-text-4); }
.badge-warn { background: var(--au-warning-soft); color: var(--au-warning); border-color: var(--au-warning-border); }
.code-cell { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

.code-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  font-family: var(--font-mono);
  font-size: 12.5px;
  letter-spacing: 0.06em;
  color: var(--au-primary);
  background: var(--au-primary-soft);
  border: 1px solid var(--au-primary-border);
  border-radius: var(--au-r-sm);
  cursor: pointer;
  transition: background var(--au-fast) var(--au-ease);
}
.code-chip:hover { background: var(--au-primary-mid); }
.code-chip:focus-visible { outline: 2px solid var(--au-border-focus); outline-offset: 2px; }

/* ---------- 弹窗 ---------- */
.realm-select { width: 220px; max-width: 100%; }
.inline-hint { margin-left: 10px; }
.decoy-hint { display: inline-flex; align-items: center; gap: 4px; }

.gen-list { display: flex; flex-direction: column; gap: 8px; max-height: 52vh; overflow-y: auto; }
.gen-code {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  font-family: var(--font-mono);
  letter-spacing: 0.06em;
  text-align: left;
  color: var(--au-text);
  background: var(--au-surface-2);
  border: 1px solid var(--au-border);
  border-radius: var(--au-r-md);
  cursor: pointer;
  transition: border-color var(--au-fast) var(--au-ease), background var(--au-fast) var(--au-ease);
}
.gen-code:hover { border-color: var(--au-primary-border); background: var(--au-primary-soft); }
.gen-days { font-size: 12px; color: var(--au-text-3); white-space: nowrap; }

@media (max-width: 768px) {
  .view-toolbar { padding: 2px 16px 12px; }
  .type-row { padding: 10px 16px; }
}
</style>
