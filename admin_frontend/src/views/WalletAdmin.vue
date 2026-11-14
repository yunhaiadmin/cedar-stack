<script setup lang="ts">
/**
 * 钱包管理（钱包批 1）：按用户查余额与流水、手工调账、全站流水、钱包配置。
 * 余额是钱：金额一律字符串传输，调账必须填原因（后端记 operator_id + 审计）。
 */
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { walletApi, type WalletBalanceResponse, type WalletLedgerRow } from '@/api/wallet'

const KIND_LABELS: Record<string, string> = {
  recharge: '在线充值',
  admin_adjust: '管理员调账',
  gift_code: '兑换码入账',
  rebate: '邀请返利',
  subscription_purchase: '订阅购买',
  traffic_purchase: '购流量',
  refund: '退款回余额',
  rebate_reversal: '返利撤回',
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
    ElMessage.success(`调账完成，当前余额 ¥${res.balance}`)
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

async function loadLedger() {
  ledgerLoading.value = true
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

// ---------- 钱包配置 ----------
const SETTING_FIELDS = [
  { key: 'wallet_enabled', label: '钱包总开关', type: 'bool', hint: '关了之后余额兑换与后续余额支付入口全灭，只读余额仍可查' },
  { key: 'wallet_min_topup_amount', label: '最低充值额（分）', type: 'int', hint: '默认 1000 ＝ ¥10，在线充值（批 3）用' },
  { key: 'wallet_topup_fee_pct', label: '充值手续费比例（%）', type: 'int', hint: '默认 0，批 3 再议' },
  { key: 'wallet_invite_rebate_percent', label: '邀请返利比例（%）', type: 'int', hint: '默认 5，好友充值到账后返给邀请人（批 2 计提）' },
  { key: 'wallet_invite_rebate_cap', label: '单人返利上限（分）', type: 'int', hint: '默认 0 ＝ 不限' },
  { key: 'wallet_invite_rebate_mode', label: '返利模式', type: 'str', hint: '默认 tiered ＝ 分档（门槛内按返利比例、超出按二档比例）；flat ＝ 全程统一比例；只填 tiered 或 flat' },
  { key: 'wallet_invite_rebate_tier_threshold', label: '分档门槛（分）', type: 'int', hint: '默认 35000 ＝ ¥350，按每位好友累计付费计，仅分档模式用' },
  { key: 'wallet_invite_rebate_tier_percent', label: '二档比例（%）', type: 'int', hint: '默认 1，超出分档门槛的部分按此比例返，仅分档模式用' },
  { key: 'traffic_balance_price_per_100gb', label: '余额购流量单价（分 / 100GB）', type: 'int', hint: '默认 300 ＝ ¥3/100GB（批 2 启用）' },
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
  <div class="wallet-admin">
    <el-card shadow="never" class="block">
      <template #header><span>用户钱包</span></template>
      <el-form inline>
        <el-form-item label="用户 ID">
          <el-input-number v-model="queryUserId" :min="1" controls-position="right" placeholder="用户 ID" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="queryLoading" @click="loadBalance">查询余额</el-button>
        </el-form-item>
      </el-form>

      <template v-if="balanceInfo">
        <el-descriptions :column="3" border class="balance-desc">
          <el-descriptions-item label="用户">{{ balanceInfo.username }}（ID {{ balanceInfo.user_id }}）</el-descriptions-item>
          <el-descriptions-item label="当前余额">¥{{ balanceInfo.balance }}</el-descriptions-item>
          <el-descriptions-item label="流水条数">{{ balanceInfo.ledger_total }}</el-descriptions-item>
        </el-descriptions>

        <el-form inline class="adjust-form">
          <el-form-item label="调账金额（元）">
            <el-input v-model="adjustForm.amount" placeholder="正数增 / 负数减，如 50 或 -20" style="width: 220px" />
          </el-form-item>
          <el-form-item label="原因（必填）">
            <el-input v-model="adjustForm.reason" placeholder="为什么调账" style="width: 260px" />
          </el-form-item>
          <el-form-item>
            <el-button type="warning" :loading="adjustLoading" @click="submitAdjust">提交调账</el-button>
          </el-form-item>
        </el-form>
        <p class="hint">调账会写一条流水并记操作人；扣减超过余额会被拒绝，余额不会扣成负数。</p>
      </template>
    </el-card>

    <el-card shadow="never" class="block">
      <template #header><span>全站流水</span></template>
      <el-form inline>
        <el-form-item label="用户 ID">
          <el-input-number v-model="ledgerQuery.user_id" :min="1" controls-position="right" placeholder="不限" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="ledgerQuery.kind" clearable placeholder="全部" style="width: 160px">
            <el-option v-for="(label, kind) in KIND_LABELS" :key="kind" :label="label" :value="kind" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button @click="ledgerQuery.page = 1; loadLedger()">查询</el-button>
        </el-form-item>
      </el-form>
      <el-table :data="ledgerRows" v-loading="ledgerLoading" stripe>
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column label="用户" min-width="140">
          <template #default="{ row }">{{ row.username || '-' }}（{{ row.user_id }}）</template>
        </el-table-column>
        <el-table-column label="类型" width="120">
          <template #default="{ row }">{{ KIND_LABELS[row.kind] || row.kind }}</template>
        </el-table-column>
        <el-table-column label="金额（元）" width="120">
          <template #default="{ row }">
            <span :class="row.amount.startsWith('-') ? 'neg' : 'pos'">{{ row.amount.startsWith('-') ? '' : '+' }}{{ row.amount }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="balance_after" label="变动后余额" width="120" />
        <el-table-column prop="note" label="备注" min-width="160" show-overflow-tooltip />
        <el-table-column label="操作人" width="120">
          <template #default="{ row }">{{ row.operator_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <el-pagination
        class="pager"
        layout="total, prev, pager, next"
        :total="ledgerTotal"
        :page-size="ledgerQuery.page_size"
        :current-page="ledgerQuery.page"
        @current-change="(p: number) => { ledgerQuery.page = p; loadLedger() }"
      />
    </el-card>

    <el-card shadow="never" class="block">
      <template #header><span>钱包配置</span></template>
      <el-form label-width="220px" style="max-width: 720px">
        <el-form-item v-for="field in SETTING_FIELDS" :key="field.key" :label="field.label">
          <el-switch
            v-if="field.type === 'bool'"
            :model-value="settings[field.key] === 'true'"
            @update:model-value="(v: string | number | boolean) => { settings[field.key] = v === true ? 'true' : 'false' }"
          />
          <el-input v-else v-model="settings[field.key]" style="width: 200px" />
          <span class="hint inline">{{ field.hint }}</span>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="settingsLoading" @click="saveSettings">保存配置</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.wallet-admin .block { margin-bottom: 16px; }
.balance-desc { margin: 12px 0; }
.adjust-form { margin-top: 8px; }
.hint { color: var(--el-text-color-secondary); font-size: 12px; }
.hint.inline { margin-left: 12px; }
.pager { margin-top: 12px; justify-content: flex-end; }
.pos { color: var(--el-color-success); }
.neg { color: var(--el-color-danger); }
</style>
