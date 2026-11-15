<script setup lang="ts">
/**
 * 钱包管理（钱包批 1）：按用户查余额与流水、手工调账、全站流水、钱包配置。
 * 余额是钱：金额一律字符串传输，调账必须填原因（后端记 operator_id + 审计）。
 *
 * UI 统一批 3：el-card 三块收编 PageHeader＋SectionCard；查询/调账改标准表单
 * （label-width 90px，配置表单按 Settings 样板 label-position=top）；全站流水
 * 筛选收编 FilterBar（提交口径不变：点「查询」才查并重置到第 1 页，筛选控件
 * 本身只改草稿）、表格与自带分页换 DataTable。功能与接口口径零变化。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { walletApi, type WalletBalanceResponse, type WalletLedgerRow } from '@/api/wallet'
import { PageHeader, SectionCard } from '@/components/ui'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import FilterBar from '@/components/FilterBar.vue'
import type { AppliedFilter, FilterConfig } from '@/components/FilterBar.vue'

const KIND_LABELS: Record<string, string> = {
  recharge: '在线充值',
  admin_adjust: '管理员调账',
  gift_code: '兑换码入账',
  rebate: '邀请返利',
  subscription_purchase: '订阅购买',
  traffic_purchase: '购流量',
  refund: '退款回余额',
  rebate_reversal: '返利撤回',
  pool_reward: '贡献池激励',
}

// ---------- 按用户查余额 + 调账 ----------
const queryUserId = ref<number | null>(null)
const balanceInfo = ref<WalletBalanceResponse | null>(null)
const queryLoading = ref(false)

async function loadBalance() {
  if (!queryUserId.value) {
    ElMessage.warning('先填用户 ID')
    return
  }
  queryLoading.value = true
  try {
    balanceInfo.value = await walletApi.balance(queryUserId.value)
  } finally {
    queryLoading.value = false
  }
}

const adjustForm = reactive({ amount: '', reason: '' })
const adjustLoading = ref(false)

async function submitAdjust() {
  if (!balanceInfo.value) {
    ElMessage.warning('先查询要调账的用户')
    return
  }
  if (!adjustForm.amount || Number(adjustForm.amount) === 0) {
    ElMessage.warning('金额不能为 0')
    return
  }
  if (!adjustForm.reason.trim()) {
    ElMessage.warning('调账必须填写原因')
    return
  }
  adjustLoading.value = true
  try {
    const res = await walletApi.adjust({
      user_id: balanceInfo.value.user_id,
      amount: adjustForm.amount,
      reason: adjustForm.reason.trim(),
    })
    ElMessage.success(`调账完成，当前余额 $${res.balance}`)
    adjustForm.amount = ''
    adjustForm.reason = ''
    await loadBalance()
    await loadLedger()
  } finally {
    adjustLoading.value = false
  }
}

// ---------- 全站流水 ----------
const ledgerRows = ref<WalletLedgerRow[]>([])
const ledgerTotal = ref(0)
const ledgerLoading = ref(false)
const ledgerQuery = reactive({ user_id: undefined as number | undefined, kind: '', page: 1, page_size: 20 })

const ledgerColumns: DataColumn[] = [
  { key: 'id', label: 'ID', width: 60 },
  { key: 'user', label: '用户', minWidth: 120, mobile: 'title' },
  { key: 'kind', label: '类型', width: 100 },
  { key: 'amount', label: '金额（美元）', width: 110 },
  { key: 'balance_after', label: '变动后余额', width: 110 },
  { key: 'note', label: '备注', minWidth: 125 },
  { key: 'operator', label: '操作人', width: 100 },
  { key: 'created_at', label: '时间', width: 175 },
]

/** chips 只反映最近一次真正执行过的查询（与 FilterBar 的草稿/已提交分离口径一致） */
const appliedLedger = reactive({ user_id: undefined as number | undefined, kind: '' })

const ledgerFilters = computed<FilterConfig[]>(() => [
  {
    key: 'user_id', label: '用户 ID', type: 'custom', value: ledgerQuery.user_id ?? null,
    isEmpty: (v: unknown) => v == null, chipText: (v: unknown) => `用户 ${v}`,
  },
  {
    key: 'kind', label: '类型', type: 'select', value: ledgerQuery.kind, placeholder: '全部',
    options: [
      { label: '全部', value: '' },
      ...Object.entries(KIND_LABELS).map(([value, label]) => ({ label, value })),
    ],
  },
])

const appliedLedgerFilters = computed<AppliedFilter[]>(() => {
  const chips: AppliedFilter[] = []
  if (appliedLedger.user_id != null) {
    chips.push({ key: 'user_id', label: '用户 ID', text: String(appliedLedger.user_id) })
  }
  if (appliedLedger.kind) {
    chips.push({ key: 'kind', label: '类型', text: KIND_LABELS[appliedLedger.kind] || appliedLedger.kind })
  }
  return chips
})

async function loadLedger() {
  ledgerLoading.value = true
  appliedLedger.user_id = ledgerQuery.user_id
  appliedLedger.kind = ledgerQuery.kind
  try {
    const res = await walletApi.ledger({
      user_id: ledgerQuery.user_id || undefined,
      kind: ledgerQuery.kind || undefined,
      page: ledgerQuery.page,
      page_size: ledgerQuery.page_size,
    })
    ledgerRows.value = res.items
    ledgerTotal.value = res.total
  } finally {
    ledgerLoading.value = false
  }
}

/** 筛选控件只改草稿（与原页面一致：点「查询」才真正查）；user_id 走 custom 插槽直绑，不经这里 */
function onLedgerFilterChange(key: string, value: unknown) {
  if (key === 'kind') ledgerQuery.kind = String(value ?? '')
}

function queryLedger() {
  ledgerQuery.page = 1
  void loadLedger()
}

function removeLedgerChip(key: string) {
  if (key === 'user_id') ledgerQuery.user_id = undefined
  if (key === 'kind') ledgerQuery.kind = ''
  queryLedger()
}

function clearLedgerFilters() {
  ledgerQuery.user_id = undefined
  ledgerQuery.kind = ''
  queryLedger()
}

// ---------- 钱包配置 ----------
const SETTING_FIELDS = [
  { key: 'wallet_enabled', label: '钱包总开关', type: 'bool', hint: '关了之后余额兑换与后续余额支付入口全灭，只读余额仍可查' },
  { key: 'wallet_min_topup_amount', label: '最低充值额（美分）', type: 'int', hint: '默认 1000 ＝ $10，在线充值（批 3）用' },
  { key: 'wallet_topup_fee_pct', label: '充值手续费比例（%）', type: 'int', hint: '默认 0，批 3 再议' },
  { key: 'wallet_invite_rebate_percent', label: '邀请返利比例（%）', type: 'int', hint: '默认 5，好友充值到账后返给邀请人（批 2 计提）' },
  { key: 'wallet_invite_rebate_cap', label: '单人返利上限（美分）', type: 'int', hint: '默认 0 ＝ 不限' },
  { key: 'wallet_invite_rebate_mode', label: '返利模式', type: 'str', hint: '默认 tiered ＝ 分档（门槛内按返利比例、超出按二档比例）；flat ＝ 全程统一比例；只填 tiered 或 flat' },
  { key: 'wallet_invite_rebate_tier_threshold', label: '分档门槛（美分）', type: 'int', hint: '默认 5000 ＝ $50，按每位好友累计付费计，仅分档模式用' },
  { key: 'wallet_invite_rebate_tier_percent', label: '二档比例（%）', type: 'int', hint: '默认 1，超出分档门槛的部分按此比例返，仅分档模式用' },
  { key: 'traffic_balance_price_per_100gb', label: '余额购流量单价（美分 / 100GB）', type: 'int', hint: '默认 30 ＝ $0.30/100GB（批 2 启用）' },
  { key: 'wallet_refund_to_balance', label: '退款回钱包', type: 'bool', hint: '默认开（批 2 落地）' },
  { key: 'wallet_auto_renew_enabled', label: '余额自动续订', type: 'bool', hint: '默认关，不开' },
] as const

const settings = reactive<Record<string, string>>({})
const settingsLoading = ref(false)

async function loadSettings() {
  const res = await walletApi.getSettings()
  Object.assign(settings, res.settings)
}

async function saveSettings() {
  settingsLoading.value = true
  try {
    await walletApi.saveSettings({ ...settings })
    ElMessage.success('钱包配置已保存')
    await loadSettings()
  } finally {
    settingsLoading.value = false
  }
}

function fmtTime(value: string | null) {
  return value ? value.replace('T', ' ').slice(0, 19) : '-'
}

onMounted(async () => {
  await Promise.all([loadLedger(), loadSettings()])
})
</script>

<template>
  <div class="admin-page">
    <PageHeader
      eyebrow="经营"
      title="钱包管理"
      description="按用户查余额与流水、手工调账、全站流水与钱包配置。金额一律字符串传输，调账必填原因并记操作人。"
    />

    <SectionCard title="用户钱包">
      <el-form label-width="90px">
        <el-form-item label="用户 ID">
          <el-input-number v-model="queryUserId" :min="1" controls-position="right" placeholder="用户 ID" />
          <el-button class="inline-btn" type="primary" :loading="queryLoading" @click="loadBalance">查询余额</el-button>
        </el-form-item>
      </el-form>

      <template v-if="balanceInfo">
        <el-descriptions :column="3" border class="balance-desc">
          <el-descriptions-item label="用户">{{ balanceInfo.username }}（ID {{ balanceInfo.user_id }}）</el-descriptions-item>
          <el-descriptions-item label="当前余额">${{ balanceInfo.balance }}</el-descriptions-item>
          <el-descriptions-item label="流水条数">{{ balanceInfo.ledger_total }}</el-descriptions-item>
        </el-descriptions>

        <el-form label-width="90px" class="adjust-form">
          <el-form-item label="调账金额（美元）">
            <el-input v-model="adjustForm.amount" class="amount-input" placeholder="正数增 / 负数减，如 50 或 -20" />
          </el-form-item>
          <el-form-item label="原因（必填）">
            <el-input v-model="adjustForm.reason" class="reason-input" placeholder="为什么调账" />
          </el-form-item>
          <el-form-item>
            <el-button type="warning" :loading="adjustLoading" @click="submitAdjust">提交调账</el-button>
          </el-form-item>
        </el-form>
        <p class="hint">调账会写一条流水并记操作人；扣减超过余额会被拒绝，余额不会扣成负数。</p>
      </template>
    </SectionCard>

    <SectionCard title="全站流水" flush>
      <div class="ledger-filter">
        <FilterBar
          :filters="ledgerFilters"
          :applied-filters="appliedLedgerFilters"
          :query-button="true"
          :loading="ledgerLoading"
          @filter-change="onLedgerFilterChange"
          @query="queryLedger"
          @remove-chip="removeLedgerChip"
          @clear="clearLedgerFilters"
        >
          <template #filter-user_id>
            <el-input-number
              v-model="ledgerQuery.user_id"
              :min="1"
              controls-position="right"
              placeholder="用户 ID 不限"
            />
          </template>
        </FilterBar>
      </div>
      <DataTable
        :rows="ledgerRows"
        :columns="ledgerColumns"
        :loading="ledgerLoading"
        :pagination="{ total: ledgerTotal, page: ledgerQuery.page, pageSize: ledgerQuery.page_size }"
        @page-change="(p: number) => { ledgerQuery.page = p; void loadLedger() }"
      >
        <template #cell-user="{ row }">{{ row.username || '-' }}（{{ row.user_id }}）</template>
        <template #cell-kind="{ row }">{{ KIND_LABELS[row.kind] || row.kind }}</template>
        <template #cell-amount="{ row }">
          <span :class="row.amount.startsWith('-') ? 'neg' : 'pos'">{{ row.amount.startsWith('-') ? '' : '+' }}{{ row.amount }}</span>
        </template>
        <template #cell-operator="{ row }">{{ row.operator_name || '-' }}</template>
        <template #cell-created_at="{ row }">{{ fmtTime(row.created_at) }}</template>
      </DataTable>
    </SectionCard>

    <SectionCard title="钱包配置">
      <el-form label-position="top" class="settings-form">
        <el-form-item v-for="field in SETTING_FIELDS" :key="field.key" :label="field.label">
          <div class="setting-row">
            <el-switch
              v-if="field.type === 'bool'"
              :model-value="settings[field.key] === 'true'"
              @update:model-value="(v: string | number | boolean) => { settings[field.key] = v === true ? 'true' : 'false' }"
            />
            <el-input v-else v-model="settings[field.key]" class="setting-input" />
            <span class="hint">{{ field.hint }}</span>
          </div>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="settingsLoading" @click="saveSettings">保存配置</el-button>
        </el-form-item>
      </el-form>
    </SectionCard>
  </div>
</template>

<style scoped>
.inline-btn { margin-left: 12px; }
.balance-desc { margin: 12px 0; }
.adjust-form { margin-top: 8px; }
.amount-input { width: 220px; }
.reason-input { width: 260px; }
.hint { color: var(--au-text-3); font-size: 12px; }
.ledger-filter { padding: 16px 16px 4px; }
.settings-form { max-width: 720px; }
.setting-row { display: flex; align-items: center; gap: 12px; }
.setting-input { width: 200px; }
.pos { color: var(--au-success); }
.neg { color: var(--au-danger); }
</style>
