<template>
  <UhdAccountLayout>
    <div class="uhd-shell-scope wallet-page">
      <h1 class="w-title">我的钱包</h1>

      <!-- 余额区：标签 + 大数字 + 充值钮（照友站钱包页截图） -->
      <p class="w-balance-label">账户余额</p>
      <div class="w-balance-row">
        <p class="w-balance"><span v-if="loading">—</span><span v-else>${{ balance }}</span></p>
        <button type="button" class="btn-primary w-recharge" @click="onRecharge">充值</button>
      </div>
      <p v-if="!walletEnabled && !loading" class="w-alert" role="alert">
        钱包功能暂未开启，余额相关操作不可用，请联系服主。
      </p>
      <div v-if="loadError" class="v4-err">
        钱包加载失败，请稍后重试<br /><button class="v4-retry" @click="loadWallet">重试</button>
      </div>

      <!-- 分段页签：我的兑换码 / 资金明细 -->
      <div class="w-seg" role="tablist" aria-label="钱包页签">
        <button
          type="button"
          role="tab"
          class="w-seg__btn"
          :class="{ 'w-seg__btn--on': tab === 'codes' }"
          :aria-selected="tab === 'codes'"
          @click="tab = 'codes'"
        >我的兑换码</button>
        <button
          type="button"
          role="tab"
          class="w-seg__btn"
          :class="{ 'w-seg__btn--on': tab === 'ledger' }"
          :aria-selected="tab === 'ledger'"
          @click="switchToLedger"
        >资金明细</button>
      </div>

      <!-- ============ 我的兑换码 ============ -->
      <div v-show="tab === 'codes'" class="w-tab">
        <section aria-label="使用兑换码">
          <h2 class="w-sec-title">使用兑换码</h2>
          <div class="w-redeem-row">
            <input
              v-model="redeemCode"
              class="w-redeem-input"
              type="text"
              placeholder="输入好友分享的兑换码"
              aria-label="兑换码"
              maxlength="64"
              autocomplete="off"
              :disabled="redeeming"
              @keydown.enter.prevent="doRedeem"
            />
            <button
              type="button"
              class="btn-primary w-redeem-btn"
              :disabled="!redeemCode.trim() || redeeming"
              @click="doRedeem"
            >{{ redeeming ? '兑换中…' : '兑换' }}</button>
          </div>
          <p class="w-note">面值会立刻存进你的余额，兑换成功后不能撤销。</p>
          <p v-if="redeemError" class="w-err" role="alert">{{ redeemError }}</p>
        </section>

        <section aria-label="生成兑换码">
          <h2 class="w-sec-title w-sec-title--rule">生成兑换码</h2>
          <div class="w-gen-row">
            <div class="w-gen-text">
              <p class="w-gen-name">把余额做成礼品卡</p>
              <p class="w-gen-sub">自定义面额，有效期最长 7 天 · 可用余额 ${{ balance }}</p>
            </div>
            <button
              type="button"
              class="w-outline-btn"
              :disabled="!walletEnabled || loading"
              @click="giftOpen = true"
            >生成</button>
          </div>
        </section>

        <section aria-label="我生成的兑换码">
          <div class="w-list-head w-sec-title--rule">
            <h2 class="w-sec-title w-sec-title--inhead">我生成的兑换码</h2>
            <button
              type="button"
              class="w-refresh"
              aria-label="刷新列表"
              :disabled="giftLoading"
              @click="loadGiftCodes"
            >
              <RefreshCw :size="17" :class="{ 'w-refresh--spin': giftLoading }" />
            </button>
            <div class="w-filter">
              <button
                type="button"
                class="w-filter__btn"
                aria-haspopup="listbox"
                :aria-expanded="filterOpen"
                @click="filterOpen = !filterOpen"
              >{{ filterLabel }}<ChevronDown :size="15" /></button>
              <div v-if="filterOpen" class="w-filter__mask" @click="filterOpen = false"></div>
              <ul v-if="filterOpen" class="w-filter__menu" role="listbox">
                <li
                  v-for="opt in filterOptions"
                  :key="opt.value"
                  role="option"
                  :aria-selected="giftFilter === opt.value"
                >
                  <button
                    type="button"
                    class="w-filter__opt"
                    :class="{ 'w-filter__opt--on': giftFilter === opt.value }"
                    @click="setFilter(opt.value)"
                  >{{ opt.label }}</button>
                </li>
              </ul>
            </div>
          </div>

          <p v-if="!giftLoading && giftCodes.length === 0" class="w-empty">没有符合条件的兑换码</p>
          <ul v-else class="w-code-list">
            <li v-for="item in giftCodes" :key="item.id" class="w-code-row">
              <div class="w-code-main">
                <button
                  type="button"
                  class="w-code-text"
                  title="点击复制兑换码"
                  @click="copyGiftCode(item.code)"
                >{{ item.code }}</button>
                <span class="w-code-meta">{{ fmtTime(item.created_at) }} 生成 · {{ fmtTime(item.expires_at) }} 到期</span>
              </div>
              <div class="w-code-side">
                <span class="w-code-amount">${{ item.amount }}</span>
                <span class="w-pill" :class="`w-pill--${item.status}`">{{ statusText(item.status) }}</span>
              </div>
            </li>
          </ul>
        </section>
      </div>

      <!-- ============ 资金明细 ============ -->
      <div v-show="tab === 'ledger'" class="w-tab">
        <p v-if="!ledgerLoading && ledger.length === 0" class="w-empty">暂无资金明细</p>
        <template v-else>
          <ul class="w-led-list">
            <li v-for="row in ledger" :key="row.id" class="w-led-row">
              <div class="w-led-main">
                <b>{{ walletLedgerLabel(row) }}</b>
                <span v-if="row.note" class="w-led-note">{{ row.note }}</span>
                <span class="w-led-time">{{ fmtTime(row.created_at) }}</span>
              </div>
              <span
                class="w-led-amount"
                :class="{ 'w-led-amount--in': Number(row.amount) >= 0 }"
              >{{ Number(row.amount) >= 0 ? '+' : '-' }}${{ absAmount(row.amount) }}</span>
            </li>
          </ul>
          <button
            v-if="ledger.length < ledgerTotal"
            type="button"
            class="w-more"
            :disabled="ledgerLoading"
            @click="loadMoreLedger"
          >{{ ledgerLoading ? '加载中…' : '加载更多' }}</button>
        </template>
      </div>

      <!-- 弹层必须留在 .uhd-shell-scope 内继承壳面令牌（挪出即落到
           v4-paper 暖纸令牌、整层变暖棕色，2026-11-15 QA 实证） -->
      <GiftCodeSheet
        :open="giftOpen"
        :balance="balance"
        @close="giftOpen = false"
        @created="onGiftCreated"
      />
      <TopupSheet
        :open="topupOpen"
        :info="topupInfo"
        @close="topupOpen = false"
      />
    </div>
  </UhdAccountLayout>
</template>

<script setup lang="ts">
/**
 * 钱包（/wallet，2026-11-15 照 UHDNow 钱包页整页重做，旧实现已删）：
 * 余额 + 充值钮 → 分段页签（我的兑换码 / 资金明细）。
 * 我的兑换码：用兑换码入账（既有 redeem 口）+ 把余额做成礼品卡
 * （自生成 balance 兑换码，GiftCodeSheet 弹层）+ 我生成的码列表
 * （状态筛选；过期未用的面值由后端在列表查询时惰性退回）。
 * 资金明细：钱包流水分页加载更多。
 * 在线充值（钱包批 3）：充值钮先查邮箱绑定（未绑定提示并跳账号
 * 安全页，照友站口径），已绑定开 TopupSheet——美元计价、易支付收
 * 人民币，回调到账后入账美元余额；未开通时弹层给诚实提示。
 * 支付回调落点：易支付 return_url 指到 /wallet?paid=1（订阅）与
 * /wallet?topup=1（充值）——进页时识别参数，刷新余额并提示到账结果。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChevronDown, RefreshCw } from 'lucide-vue-next'
import UhdAccountLayout from '@/components/uhd/UhdAccountLayout.vue'
import GiftCodeSheet from '@/components/profile/GiftCodeSheet.vue'
import TopupSheet from '@/components/profile/TopupSheet.vue'
import { useUserStore } from '@/stores/user'
import {
  walletApi,
  walletLedgerLabel,
  type GiftCodeCreateResult,
  type GiftCodeItem,
  type WalletLedgerRow,
  type WalletTopupInfo,
} from '@/api/wallet'
import { useToast } from '@/composables/useToast'
import { toUserMessage } from '@/utils/toUserMessage'

const toast = useToast()
const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const loading = ref(true)
const loadError = ref(false)
const balance = ref('0.00')
const walletEnabled = ref(true)

/* ---------- 在线充值 ---------- */
const topupOpen = ref(false)
const topupInfo = ref<WalletTopupInfo | null>(null)

const tab = ref<'codes' | 'ledger'>('codes')

/* ---------- 使用兑换码 ---------- */
const redeemCode = ref('')
const redeeming = ref(false)
const redeemError = ref('')

/* ---------- 我生成的兑换码 ---------- */
const giftOpen = ref(false)
const giftCodes = ref<GiftCodeItem[]>([])
const giftLoading = ref(false)
type GiftFilter = 'all' | 'unused' | 'used' | 'expired'
const giftFilter = ref<GiftFilter>('all')
const filterOpen = ref(false)
const filterOptions: { value: GiftFilter; label: string }[] = [
  { value: 'all', label: '全部记录' },
  { value: 'unused', label: '未使用' },
  { value: 'used', label: '已使用' },
  { value: 'expired', label: '已过期' },
]
const filterLabel = computed(
  () => filterOptions.find((o) => o.value === giftFilter.value)?.label ?? '全部记录',
)

/* ---------- 资金明细 ---------- */
const ledger = ref<WalletLedgerRow[]>([])
const ledgerTotal = ref(0)
const ledgerPage = ref(1)
const ledgerLoading = ref(false)
const ledgerLoaded = ref(false)
const LEDGER_PAGE_SIZE = 20

function fmtTime(iso: string | null): string {
  if (!iso) return ''
  return iso.slice(0, 10) + ' ' + iso.slice(11, 16)
}

function absAmount(amount: string): string {
  return amount.startsWith('-') ? amount.slice(1) : amount
}

function statusText(status: GiftCodeItem['status']): string {
  return { unused: '未使用', used: '已使用', expired: '已过期' }[status]
}

async function loadWallet() {
  loading.value = true
  loadError.value = false
  try {
    const info = await walletApi.get()
    balance.value = info.balance
    walletEnabled.value = info.wallet_enabled
    topupInfo.value = info.topup ?? null
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function loadGiftCodes() {
  giftLoading.value = true
  try {
    const res = await walletApi.giftCodes(giftFilter.value)
    giftCodes.value = res.items
  } catch {
    giftCodes.value = []
  } finally {
    giftLoading.value = false
  }
}

function setFilter(v: GiftFilter) {
  giftFilter.value = v
  filterOpen.value = false
  void loadGiftCodes()
}

async function loadLedger(reset: boolean) {
  if (ledgerLoading.value) return
  ledgerLoading.value = true
  try {
    const page = reset ? 1 : ledgerPage.value + 1
    const res = await walletApi.ledger({ page, page_size: LEDGER_PAGE_SIZE })
    ledger.value = reset ? res.items : [...ledger.value, ...res.items]
    ledgerTotal.value = res.total
    ledgerPage.value = page
    ledgerLoaded.value = true
  } catch {
    if (reset) {
      ledger.value = []
      ledgerTotal.value = 0
    }
  } finally {
    ledgerLoading.value = false
  }
}

function switchToLedger() {
  tab.value = 'ledger'
  if (!ledgerLoaded.value) void loadLedger(true)
}

function loadMoreLedger() {
  void loadLedger(false)
}

async function doRedeem() {
  const code = redeemCode.value.trim()
  if (!code || redeeming.value) return
  redeeming.value = true
  redeemError.value = ''
  try {
    const res = await walletApi.redeem(code)
    toast.success(res.message || `已入账 $${res.balance_value ?? ''}`)
    redeemCode.value = ''
    // 兑的可能是别人给的码、也可能回头影响我生成的码状态，两边都刷新
    await Promise.all([
      loadWallet(),
      loadGiftCodes(),
      ledgerLoaded.value ? loadLedger(true) : Promise.resolve(),
    ])
  } catch (e) {
    redeemError.value = toUserMessage(e)
  } finally {
    redeeming.value = false
  }
}

function onGiftCreated(res: GiftCodeCreateResult) {
  balance.value = res.balance
  void loadGiftCodes()
  if (ledgerLoaded.value) void loadLedger(true)
}

function onRecharge() {
  // 前置拦截（照友站口径）：未绑定邮箱先去账号安全页绑定，不开弹窗
  if (!userStore.user?.email) {
    toast.info('请先绑定邮箱后再充值')
    router.push('/game/account/settings')
    return
  }
  topupOpen.value = true
}

async function copyGiftCode(code: string) {
  try {
    await navigator.clipboard.writeText(code)
    toast.success('兑换码已复制')
  } catch {
    toast.error('复制失败，请长按兑换码手动复制')
  }
}

onMounted(() => {
  void loadWallet()
  void loadGiftCodes()
  // 支付回调落点（economy.return_url = /wallet?paid=1）：进页即刷新
  // 余额并给到账提示；order 参数同理（网关同步跳转带订单号）。
  if (route.query.paid === '1' || route.query.order) {
    toast.success('支付已完成，余额与订阅已更新')
  }
  // 充值回调落点（/wallet?topup=1）：余额已在进页时重新拉过
  if (route.query.topup === '1') {
    toast.success('支付已完成，余额已更新')
  }
})
</script>

<style scoped>
/* 钱包页（UHD 重做）：令牌走用户中心壳面（--paper/--paper2/--paper-card/
   --ink/--ink2/--line），深色为默认、浅色随 html[data-theme='light']，
   与友站截图的深色形态同值（卡 #17181a / 输入 #242526 / 边 #26262a）。 */
.wallet-page { padding-bottom: 40px; }

.w-title { margin: 6px 0 0; font-size: 30px; font-weight: 800; letter-spacing: -0.01em; color: var(--ink); }

.w-balance-label { margin: 26px 0 0; font-size: 13.5px; color: var(--ink2); }
.w-balance-row {
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px; margin-top: 6px;
}
.w-balance {
  margin: 0;
  font-family: var(--uhd-font-display);
  font-size: 40px;
  font-weight: var(--uhd-fw-black, 900);
  letter-spacing: -0.02em;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
/* 充值钮：小号彩虹描边玻璃胶囊（.btn-primary 提供彩虹边＋玻璃配方，
   这里只收尺寸，照友站钱包帧的小号量级） */
.w-recharge {
  flex: none;
  height: 36px;
  min-height: 36px;
  padding: 0 18px;
  border-radius: 999px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
}
.w-recharge:active { transform: scale(var(--uhd-press-scale)); }
.w-alert { margin: 12px 0 0; font-size: 12.5px; color: #f87171; }
html[data-theme='light'] .w-alert { color: #dc2626; }

/* 分段页签：深色胶囊容器 + 选中项浅底（照截图） */
.w-seg {
  margin-top: 26px;
  display: inline-flex;
  gap: 4px;
  padding: 4px;
  border-radius: 999px;
  background: var(--paper-card);
  border: 1px solid var(--line);
}
.w-seg__btn {
  height: 38px;
  min-height: 38px;
  padding: 0 17px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--ink2);
  font-family: inherit;
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
}
.w-seg__btn--on {
  background: var(--paper2);
  border-color: var(--line);
  color: var(--ink);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08), var(--uhd-shadow-soft);
}
html[data-theme='light'] .w-seg__btn--on {
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65), var(--uhd-shadow-seg);
}

.w-tab { margin-top: 26px; display: flex; flex-direction: column; gap: 32px; }
.w-sec-title { margin: 0 0 12px; font-size: 18px; font-weight: 700; color: var(--ink); }
.w-sec-title--rule { border-bottom: 1px solid var(--line); padding-bottom: 12px; }
.w-sec-title--inhead { margin: 0; }
.w-note { margin: 10px 0 0; font-size: 12.5px; color: var(--ink2); }
.w-err { margin: 10px 0 0; font-size: 12.5px; color: #f87171; }
html[data-theme='light'] .w-err { color: #dc2626; }
.w-empty { margin: 16px 0 0; font-size: 13px; color: var(--ink2); }

/* 使用兑换码 */
.w-redeem-row { display: flex; gap: 10px; align-items: stretch; }
.w-redeem-input {
  flex: 1;
  min-width: 0;
  height: 46px;
  padding: 0 18px;
  border-radius: 23px;
  background: var(--paper2);
  border: 1px solid var(--line);
  color: var(--ink);
  font-family: inherit;
  font-size: 14px;
  outline: none;
}
.w-redeem-input::placeholder { color: var(--ink2); }
.w-redeem-input:focus { border-color: var(--line-deep, var(--line)); }
.w-redeem-btn {
  flex: none;
  height: 46px;
  padding: 0 22px;
  border-radius: 23px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
}

/* 生成兑换码行 */
.w-gen-row { display: flex; align-items: center; gap: 14px; }
.w-gen-text { flex: 1; min-width: 0; }
.w-gen-name { margin: 0; font-size: 15.5px; font-weight: 700; color: var(--ink); }
.w-gen-sub { margin: 5px 0 0; font-size: 12.5px; line-height: 1.5; color: var(--ink2); }
.w-outline-btn {
  flex: none;
  height: 36px;
  min-height: 36px;
  padding: 0 17px;
  border-radius: 999px;
  border: 1.5px solid var(--line-deep, var(--line));
  background: transparent;
  color: var(--ink);
  font-family: inherit;
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
}
.w-outline-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.w-outline-btn:active:not(:disabled) { transform: scale(var(--uhd-press-scale)); }

/* 我生成的兑换码：标题行 + 筛选 */
.w-list-head { display: flex; align-items: center; gap: 8px; }
.w-refresh {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: none;
  background: transparent;
  color: var(--ink2);
  cursor: pointer;
}
.w-refresh:disabled { cursor: default; }
.w-refresh--spin { animation: w-spin 0.9s linear infinite; }
@keyframes w-spin { to { transform: rotate(360deg); } }
.w-filter { position: relative; margin-left: auto; }
.w-filter__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  min-height: 36px;
  padding: 0 13px;
  border-radius: 999px;
  background: var(--paper-card);
  border: 1px solid var(--line);
  color: var(--ink);
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.w-filter__mask { position: fixed; inset: 0; z-index: 40; }
.w-filter__menu {
  /* 向上展开：列表头在页面下半部，向下展开会溢出视口、被底部坞挡住
     （2026-11-15 QA 实证），向上弹盖住上方区块是浮层常态 */
  position: absolute;
  right: 0;
  bottom: calc(100% + 6px);
  z-index: 41;
  min-width: 136px;
  margin: 0;
  padding: 6px;
  list-style: none;
  background: var(--paper-card);
  border: 1px solid var(--line);
  border-radius: 14px;
  box-shadow: var(--uhd-shadow-float);
}
.w-filter__opt {
  width: 100%;
  text-align: left;
  padding: 9px 12px;
  border-radius: 9px;
  border: none;
  background: transparent;
  color: var(--ink2);
  font-family: inherit;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.w-filter__opt--on { color: var(--ink); background: var(--paper2); }

/* 礼品卡列表行 */
.w-code-list { list-style: none; margin: 6px 0 0; padding: 0; }
.w-code-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 0;
  border-top: 1px solid var(--line);
}
.w-code-row:first-child { border-top: none; }
.w-code-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
}
.w-code-text {
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  color: var(--ink);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.03em;
  text-align: left;
  overflow-wrap: anywhere;
}
.w-code-meta { font-size: 12px; color: var(--ink2); }
.w-code-side {
  flex: none;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 5px;
}
.w-code-amount { font-size: 14px; font-weight: 700; color: var(--ink); font-variant-numeric: tabular-nums; }
.w-pill {
  font-size: 11.5px;
  font-weight: 700;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: var(--paper2);
  color: var(--ink2);
  white-space: nowrap;
}
.w-pill--unused {
  background: rgba(22, 163, 74, 0.15);
  border-color: rgba(22, 163, 74, 0.5);
  color: #4ade80;
}
.w-pill--expired {
  background: rgba(220, 38, 38, 0.12);
  border-color: rgba(220, 38, 38, 0.4);
  color: #f87171;
}
html[data-theme='light'] .w-pill--unused { background: #e8f0e9; color: #16a34a; }
html[data-theme='light'] .w-pill--expired { background: #fdecec; color: #dc2626; }

/* 资金明细行 */
.w-led-list { list-style: none; margin: 0; padding: 0; }
.w-led-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 13px 0;
  border-top: 1px solid var(--line);
}
.w-led-row:first-child { border-top: none; }
.w-led-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.w-led-main b { font-size: 13.5px; font-weight: 600; color: var(--ink); }
.w-led-note {
  font-size: 12.5px;
  color: var(--ink2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.w-led-time { font-size: 12px; color: var(--ink2); }
.w-led-amount {
  flex: none;
  font-size: 14px;
  font-weight: 700;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.w-led-amount--in { color: var(--green); }
.w-more {
  display: block;
  margin: 18px auto 0;
  height: 36px;
  min-height: 36px;
  padding: 0 20px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: var(--paper-card);
  color: var(--ink);
  font-family: inherit;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.w-more:disabled { opacity: 0.5; cursor: default; }
</style>
