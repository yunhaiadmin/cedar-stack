<template>
  <UhdAccountLayout title="我的钱包">
    <div class="v4-scope">
      <div class="v4-page wallet-page">
        <div class="wallet-body">
          <!-- 余额卡：当前余额（唯一货币：现金余额，人民币元） -->
          <section class="wallet-card" aria-label="钱包余额">
            <p class="wallet-card__label">钱包余额</p>
            <p class="wallet-card__big">
              <span v-if="loading">—</span>
              <span v-else>¥ {{ balance }}</span>
            </p>
            <p class="wallet-card__note">全站唯一货币：现金余额（人民币）。订阅、购流量、邀请返利都走这里。</p>
            <p v-if="!walletEnabled" class="wallet-card__note" role="alert">钱包功能暂未开启，余额相关操作不可用，请联系服主。</p>
          </section>

          <div v-if="loadError" class="v4-err">
            钱包加载失败，请稍后重试<br /><button class="v4-retry" @click="loadAll">重试</button>
          </div>

          <!-- 在线充值（批 3）：商户信息未到位，入口只占位、不假接支付 -->
          <section class="wallet-sec" aria-label="在线充值">
            <h2 class="wallet-sec__title">在线充值</h2>
            <p class="wallet-sec__note">在线充值即将上线（最低 ¥10）。现在可通过管理员充值或兑换余额码入账。</p>
            <button type="button" class="wallet-btn wallet-btn--disabled" disabled>即将上线</button>
          </section>

          <!-- 余额码兑换（批 1 已有端点）：只认 reward_type=balance 的码 -->
          <section class="wallet-sec" aria-label="兑换余额码">
            <h2 class="wallet-sec__title">兑换余额码</h2>
            <div class="wallet-redeem__row">
              <input
                v-model="redeemCode"
                class="wallet-redeem__input"
                type="text"
                placeholder="输入余额码"
                aria-label="余额码"
                maxlength="64"
                autocomplete="off"
                :disabled="redeeming"
                @keydown.enter.prevent="doRedeem"
              />
              <button
                type="button"
                class="wallet-btn"
                :disabled="!redeemCode.trim() || redeeming"
                @click="doRedeem"
              >{{ redeeming ? '兑换中…' : '兑换' }}</button>
            </div>
            <p v-if="redeemError" class="wallet-err" role="alert">{{ redeemError }}</p>
          </section>

          <!-- 购流量入口：余额购流量走弹窗（与概览页同一弹窗） -->
          <section v-if="traffic?.enabled" class="wallet-sec" aria-label="购买流量">
            <h2 class="wallet-sec__title">购买流量</h2>
            <p class="wallet-sec__note">
              本月已用 {{ formatBytes(traffic.used_bytes) }} / 共 {{ formatBytes(traffic.quota_bytes) }}，
              余额可抵扣已用流量。
            </p>
            <button type="button" class="wallet-btn" @click="trafficModalOpen = true">购买流量</button>
          </section>

          <!-- 收支流水：只增不改，错账用冲正行纠 -->
          <section class="wallet-sec" aria-label="收支流水">
            <h2 class="wallet-sec__title">收支流水</h2>
            <div class="wallet-filter" role="group" aria-label="按类型筛选">
              <button
                v-for="opt in kindOptions"
                :key="opt.value"
                type="button"
                class="wallet-chip"
                :class="{ 'wallet-chip--on': kindFilter === opt.value }"
                :aria-pressed="kindFilter === opt.value"
                @click="setKind(opt.value)"
              >{{ opt.label }}</button>
            </div>
            <div v-if="ledgerLoading" class="v4-skeleton" style="height:120px"></div>
            <p v-else-if="ledger.length === 0" class="wallet-empty">暂无流水</p>
            <ul v-else class="wallet-list">
              <li v-for="row in ledger" :key="row.id" class="wallet-row">
                <span class="wallet-row__main">
                  <b>{{ walletKindText(row.kind) }}</b>
                  <span>{{ row.note || row.ref_id || '' }}</span>
                  <span class="wallet-row__time">{{ fmtTime(row.created_at) }}</span>
                </span>
                <span
                  class="wallet-row__amount"
                  :class="Number(row.amount) >= 0 ? 'wallet-row__amount--in' : 'wallet-row__amount--out'"
                >{{ Number(row.amount) >= 0 ? '+' : '' }}{{ row.amount }}</span>
              </li>
            </ul>
            <div v-if="ledgerTotal > pageSize" class="wallet-pager">
              <button type="button" class="wallet-btn wallet-btn--ghost" :disabled="page <= 1 || ledgerLoading" @click="page -= 1; loadLedger()">上一页</button>
              <span class="wallet-pager__info">第 {{ page }} 页 / 共 {{ totalPages }} 页</span>
              <button type="button" class="wallet-btn wallet-btn--ghost" :disabled="page >= totalPages || ledgerLoading" @click="page += 1; loadLedger()">下一页</button>
            </div>
          </section>
        </div>
      </div>
    </div>

    <TrafficPurchaseModal
      v-model:open="trafficModalOpen"
      :traffic="traffic"
      :options="traffic?.exchange_options ?? []"
      @exchanged="onTrafficExchanged"
    />
  </UhdAccountLayout>
</template>

<script setup lang="ts">
/**
 * 我的钱包（/wallet，钱包批 2）：余额卡 + 在线充值占位（批 3，
 * 不假接）+ 余额码兑换 + 购流量入口（弹窗）+ 收支流水分页。
 * 支付回调落点：易支付 return_url 指到 /wallet?paid=1——进页时
 * 识别该参数，刷新余额并提示到账结果（不再 404）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import UhdAccountLayout from '@/components/uhd/UhdAccountLayout.vue'
import TrafficPurchaseModal from '@/components/profile/TrafficPurchaseModal.vue'
import { walletApi, walletKindText, type WalletLedgerRow } from '@/api/wallet'
import { getTraffic, formatBytes, type TrafficOverview } from '@/api/traffic'
import { useToast } from '@/composables/useToast'
import { toUserMessage } from '@/utils/toUserMessage'

const toast = useToast()
const route = useRoute()

const loading = ref(true)
const loadError = ref(false)
const balance = ref('0.00')
const walletEnabled = ref(true)

const redeemCode = ref('')
const redeeming = ref(false)
const redeemError = ref('')

const traffic = ref<TrafficOverview | null>(null)
const trafficModalOpen = ref(false)

const ledger = ref<WalletLedgerRow[]>([])
const ledgerTotal = ref(0)
const ledgerLoading = ref(false)
const page = ref(1)
const pageSize = 20
const totalPages = computed(() => Math.max(1, Math.ceil(ledgerTotal.value / pageSize)))
const kindFilter = ref('')
const kindOptions = [
  { value: '', label: '全部' },
  { value: 'recharge', label: '充值' },
  { value: 'gift_code', label: '兑换码' },
  { value: 'rebate', label: '返利' },
  { value: 'subscription_purchase', label: '订阅' },
  { value: 'traffic_purchase', label: '流量' },
  { value: 'refund', label: '退款' },
  { value: 'admin_adjust', label: '调整' },
]

function fmtTime(iso: string | null): string {
  if (!iso) return ''
  return iso.slice(0, 10) + ' ' + iso.slice(11, 16)
}

async function loadWallet() {
  loading.value = true
  loadError.value = false
  try {
    const info = await walletApi.get()
    balance.value = info.balance
    walletEnabled.value = info.wallet_enabled
  } catch (e) {
    loadError.value = true
    void e
  } finally {
    loading.value = false
  }
}

async function loadLedger() {
  ledgerLoading.value = true
  try {
    const res = await walletApi.ledger({
      kind: kindFilter.value || undefined,
      page: page.value,
      page_size: pageSize,
    })
    ledger.value = res.items
    ledgerTotal.value = res.total
  } catch {
    ledger.value = []
    ledgerTotal.value = 0
  } finally {
    ledgerLoading.value = false
  }
}

function setKind(v: string) {
  kindFilter.value = v
  page.value = 1
  void loadLedger()
}

async function loadTraffic() {
  try {
    traffic.value = await getTraffic()
  } catch {
    traffic.value = null
  }
}

async function loadAll() {
  await Promise.all([loadWallet(), loadLedger(), loadTraffic()])
}

async function doRedeem() {
  const code = redeemCode.value.trim()
  if (!code || redeeming.value) return
  redeeming.value = true
  redeemError.value = ''
  try {
    const res = await walletApi.redeem(code)
    toast.success(res.message || `已入账 ¥${res.balance_value ?? ''}`)
    redeemCode.value = ''
    await loadAll()
  } catch (e) {
    redeemError.value = toUserMessage(e)
  } finally {
    redeeming.value = false
  }
}

function onTrafficExchanged() {
  void loadAll()
}

onMounted(() => {
  void loadAll()
  // 支付回调落点（economy.return_url = /wallet?paid=1）：进页即刷新
  // 余额并给到账提示；order 参数同理（网关同步跳转带订单号）。
  if (route.query.paid === '1' || route.query.order) {
    toast.success('支付已完成，余额与订阅已更新')
  }
})
</script>

<style scoped>
/* 浅色纸面口径（与 UhdAccountLayout 内其他账号页一致：--paper/--ink/--line；
   主钮用彩虹边框＋淡彩粉彩底＋玻璃栈，与 MemberView 同款） */
.wallet-page { padding-bottom: 34px; }
.wallet-body { padding-top: 4px; display: flex; flex-direction: column; gap: 28px; }

/* 余额卡：白底卡面＋大数字 */
.wallet-card {
  padding: 20px 22px;
  border-radius: var(--paper-r-lg, 16px);
  background: var(--paper-card);
  border: 1px solid var(--line);
}
.wallet-card__label { margin: 0 0 4px; font-size: 13px; color: var(--ink2); }
.wallet-card__big {
  margin: 0;
  font-family: var(--uhd-font-display);
  font-size: 40px;
  font-weight: var(--uhd-fw-black);
  letter-spacing: -0.02em;
  color: var(--ink);
}
.wallet-card__note { margin: 10px 0 0; font-size: 13px; line-height: 1.6; color: var(--ink2); }

.wallet-sec__title { margin: 0 0 8px; font-size: 16px; font-weight: 700; color: var(--ink); }
.wallet-sec__note { margin: 0 0 12px; font-size: 13px; line-height: 1.6; color: var(--ink2); }
.wallet-err { margin: 10px 0 0; font-size: 13px; color: #dc2626; }
.wallet-empty { margin: 0; font-size: 13px; color: var(--ink2); }

/* 按钮：主钮彩虹边框＋淡彩粉彩底＋玻璃栈（与 MemberView 入口钮同款） */
.wallet-btn {
  padding: 10px 20px;
  border: 1.5px solid transparent;
  border-radius: 999px;
  background:
    var(--uhd-rainbow-soft) padding-box,
    var(--uhd-rainbow) border-box;
  color: #111111;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.65),
    var(--uhd-shadow-card-l);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
}
.wallet-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.wallet-btn--disabled {
  background: var(--paper2);
  border-color: var(--line);
  color: var(--ink2);
  box-shadow: none;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
  cursor: not-allowed;
}
.wallet-btn--ghost {
  background: var(--paper2);
  border-color: var(--line);
  color: var(--ink);
  box-shadow: none;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
.wallet-btn:active:not(:disabled) { transform: scale(0.97); }

.wallet-redeem__row { display: flex; gap: 10px; align-items: stretch; }
.wallet-redeem__input {
  flex: 1 1 auto;
  min-width: 0;
  padding: 0 16px;
  min-height: 46px;
  border-radius: 999px;
  background: var(--paper-card);
  border: 1px solid var(--line);
  color: var(--ink);
  font-family: inherit;
  font-size: 15px;
  outline: none;
}
.wallet-redeem__input::placeholder { color: var(--ink2); }

.wallet-filter { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.wallet-chip {
  padding: 7px 14px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: transparent;
  color: var(--ink2);
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}
.wallet-chip--on { background: #111111; border-color: transparent; color: #ffffff; font-weight: 600; }

.wallet-list { list-style: none; margin: 0; padding: 0; }
.wallet-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 0;
  border-top: 1px solid var(--line);
}
.wallet-row__main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.wallet-row__main b { font-size: 14px; font-weight: 600; color: var(--ink); }
.wallet-row__main span { font-size: 12px; color: var(--ink2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wallet-row__amount { flex: 0 0 auto; font-size: 15px; font-weight: 700; font-variant-numeric: tabular-nums; }
.wallet-row__amount--in { color: var(--uhd-ok); }
.wallet-row__amount--out { color: var(--ink); }

.wallet-pager { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 14px; }
.wallet-pager__info { font-size: 13px; color: var(--ink2); }
</style>
