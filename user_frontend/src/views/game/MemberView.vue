<template>
  <UhdAccountLayout title="我的订阅">
  <div class="uhd-shell-scope member-page">
    <div class="member-body">
      <!-- 订阅购买入口：购买流程在订阅页（单选卡＋时长＋优惠码＋吸底结算）；
           公益服不卖套餐，不显示入口 -->
      <RouterLink v-if="!isFreeRealm" class="member-sub-entry" to="/game/account/subscribe">
        <span class="member-sub-entry__text">
          <b>{{ activeSub ? '续费订阅' : '开通订阅' }}</b>
          <span>{{ activeSub ? '当前订阅到期前续费，天数顺延' : '选套餐、选时长，在线支付' }}</span>
        </span>
        <span class="member-sub-entry__btn">{{ activeSub ? '去续费' : '去开通' }}</span>
      </RouterLink>
      <div v-if="subsError" class="v4-err">订阅加载失败，请稍后重试<br><button class="v4-retry" @click="loadSubs">重试</button></div>
      <!-- 在途显示骨架屏 —— 请求还没回来时别闪现"还没有会员" -->
      <div v-else-if="subsLoading" class="v4-skeleton" style="height:180px"></div>
      <ProfileSubscriptions
        v-else
        :active-sub="activeSub"
        :subscriptions="subscriptions"
        :expiring-soon="expiringSoon"
        :is-free-realm="isFreeRealm"
        :realm-note="realmNote"
        :cancelling-sub="cancellingSub"
        :toggling-renew="togglingRenew"
        :welfare-is-welfare="!!userStore.user?.is_welfare"
        :welfare-expires-at="welfareExpiresAt"
        :welfare-status-loaded="welfareStatusLoaded"
        :points-balance="pointsBalance"
        :tg-bound="tgBound"
        :view-unlock="viewUnlock"
        :renewing-welfare="renewingWelfare"
        @cancel="cancelSubscription"
        @toggle-renew="toggleAutoRenew"
        @renew-welfare="renewWelfare"
      />
    </div>

    <!-- 终止订阅二次确认（纸色 Modal，与原账号页同款同文案） -->
    <Modal v-model="cancelConfirmOpen" size="sm" content-class="paper-confirm">
      <template #header>
        <h3 class="modal-title">终止订阅</h3>
</template>
      <p class="confirm-text">{{ pendingCancel ? confirmCopy.cancelSubscription(pendingCancel.plan_name) : '' }}</p>
      <template #footer>
        <div class="confirm-actions">
          <button type="button" class="m-btn ghost" @click="cancelConfirmOpen = false">再想想</button>
          <button type="button" class="m-btn primary" :disabled="cancellingSub !== null" @click="doCancelSubscription">
            {{ cancellingSub !== null ? '终止中' : '确定终止' }}
          </button>
        </div>
      </template>
    </Modal>
  </div>
  </UhdAccountLayout>
</template>

<script setup lang="ts">
import UhdAccountLayout from '@/components/uhd/UhdAccountLayout.vue'
/**
 * 我的订阅（从个人中心「我的权益」进入的子页）：承接原账号页的全部订阅
 * 管理——展示层全在 ProfileSubscriptions（到期 / 自动续订 / 历史账单 /
 * 终止、公益资格续期、查看凭证续费，长规则文字随组件在本页呈现）。
 * 逻辑自旧 AccountSettingsView 原样迁入，口径不变（含焦点归还：确认弹窗
 * 任何关闭路径都把焦点还给触发按钮）。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import Modal from '@/components/ui/Modal.vue'
import ProfileSubscriptions from '@/components/profile/ProfileSubscriptions.vue'
import { subscriptionApi, embyApi, isExpiringSoon, type MySubscription, type AccountCard } from '@/api'
import { welfareApi } from '@/api/economy'
import { tgApi } from '@/api/tg'
import { useUserStore } from '@/stores/user'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'
import { toastCopy, confirmCopy } from '@/constants/toastMessages'
import { toUserMessage } from '@/utils/toUserMessage'

const toast = useToast()
const userStore = useUserStore()

const subscriptions = ref<MySubscription[]>([])
const subsError = ref('')
const subsLoading = ref(false)
const cancellingSub = ref<number | null>(null)
const togglingRenew = ref<number | null>(null)

const activeSub = computed(
  () => subscriptions.value.find(s => s.status === 'active' && s.days_left > 0) || null,
)
const expiringSoon = computed(() => isExpiringSoon(activeSub.value))
const isFreeRealm = computed(() => userStore.isFreeRealm)
const realmNote = computed(() => userStore.realmNote)

/** 弹窗关闭后焦点回到触发按钮（终止订阅用） */
const lastTrigger = ref<HTMLElement | null>(null)

async function loadSubs() {
  subsLoading.value = true
  subsError.value = ''
  try {
    subscriptions.value = await subscriptionApi.getMine()
  } catch (e) {
    // 加载失败别装成"没订阅"，留错误态给用户重试
    subsError.value = humanizeError(e)
  } finally {
    subsLoading.value = false
  }
}

async function toggleAutoRenew(s: MySubscription) {
  if (togglingRenew.value !== null) return
  togglingRenew.value = s.id
  try {
    const res = await subscriptionApi.setAutoRenew(s.id, !s.auto_renew)
    toast.success(res.message || (res.auto_renew ? '已开启自动续订' : '已关闭自动续订'))
    await loadSubs()
  } catch (err: any) {
    toast.error(toUserMessage(err, 'toggleAutoRenewFailed'))
  } finally {
    togglingRenew.value = null
  }
}

// ===== 自助续期（公益服） =====
const accountCard = ref<AccountCard | null>(null)
const tgBound = ref<boolean | null>(null)
const welfareExpiresAt = ref<string | null>(null)
const welfareStatusLoaded = ref(false)
const renewingWelfare = ref<number | null>(null)

const viewUnlock = computed(() => {
  const vp = accountCard.value?.view_permission
  if (!vp) return null
  return {
    granted: vp.granted,
    expiresAt: vp.expires_at,
    source: vp.source ?? null,
  }
})
const pointsBalance = computed(() => accountCard.value?.view_permission?.points_balance ?? null)

async function loadRenewal() {
  // 三路各自容错：账号卡失败不挡 TG 状态，反之亦然
  try {
    accountCard.value = await embyApi.getAccountCard()
  } catch {
    accountCard.value = null
  }
  try {
    const st = await tgApi.status()
    tgBound.value = st.telegram_id !== null && st.telegram_id !== undefined
  } catch {
    tgBound.value = null
  }
  try {
    const st = await welfareApi.status()
    welfareExpiresAt.value = st.welfare_expires_at
    if (userStore.user) userStore.user.is_welfare = st.is_welfare
    welfareStatusLoaded.value = true
  } catch {
    // 状态口失败时不显示日期，不阻断页面
  }
}

async function renewWelfare(days: 7 | 30) {
  if (renewingWelfare.value !== null) return
  renewingWelfare.value = days
  try {
    const res = await welfareApi.redeem(days)
    welfareExpiresAt.value = res.expires_at
    welfareStatusLoaded.value = true
    if (userStore.user) userStore.user.is_welfare = true
    toast.success(`续期成功，已延至 ${res.expires_at ? res.expires_at.slice(0, 10) : `${days} 天后`}`)
    await loadRenewal()
  } catch (err: any) {
    toast.error(toUserMessage(err, 'renewFailed'))
  } finally {
    renewingWelfare.value = null
  }
}

const pendingCancel = ref<MySubscription | null>(null)
const cancelConfirmOpen = computed({
  get: () => pendingCancel.value !== null,
  // 任何关闭路径（"再想想"、Modal 内部关闭/Esc）都把焦点还给触发按钮
  set: (v: boolean) => { if (!v) { pendingCancel.value = null; lastTrigger.value?.focus?.() } },
})
function cancelSubscription(s: MySubscription) {
  lastTrigger.value = document.activeElement as HTMLElement | null
  pendingCancel.value = s
}
async function doCancelSubscription() {
  const s = pendingCancel.value
  if (!s || cancellingSub.value !== null) return
  cancellingSub.value = s.id
  try {
    const res = await subscriptionApi.cancel(s.id)
    toast.success(res.message || toastCopy.subscriptionCancelled())
    cancelConfirmOpen.value = false
    await loadSubs()
  } catch (err: any) {
    toast.error(toUserMessage(err, 'cancelFailed'))
    // 失败时保持确认弹窗打开，用户可直接重试，不丢失上下文
  } finally {
    cancellingSub.value = null
  }
}

onMounted(() => {
  void loadSubs()
  if (userStore.isFreeRealm) void loadRenewal()
})
</script>

<style scoped>
/* 页根（2026-11-14 统一壳单）：摘掉 .v4-scope/.v4-page 纸面嵌套后，
   宽度与底距由本类自承——手机与原 .v4-page 同口径（430 居中＋坞距），
   桌面随办事面容器 1000px；令牌由 .uhd-shell-scope（布局全局样式）供给，
   与原壳内覆盖值一致。文字色在此定死，免继承链漂移。 */
.member-page {
  max-width: min(430px, 100%);
  margin: 0 auto;
  padding-bottom: calc(76px + env(safe-area-inset-bottom));
  color: var(--uhd-l-text);
}
@media (min-width: 769px) {
  .member-page {
    max-width: var(--uhd-container-app);
    padding-bottom: 34px;
  }
}
/* 横向不再自加内边距：与布局 H1 左缘对齐（布局 main 已给 16/32px） */
.member-body { padding-top: 4px; }
/* 订阅购买入口：平面行（发丝线收尾），彩虹主按钮引到订阅页（购买流程在那边一页完成） */
.member-sub-entry {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
  margin-bottom: 6px;
  padding: 10px 0 18px;
  border-bottom: 1px solid var(--line);
  text-decoration: none;
}
.member-sub-entry__text {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.member-sub-entry__text b {
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
}
.member-sub-entry__text span {
  font-size: 13px;
  color: var(--ink2);
}
/* 入口主钮 UHD 口径（2026-11-13 补齐七彩＋玻璃）：由满面彩虹填充改彩虹边框 +
   淡彩粉彩底 + 玻璃栈，与全站主钮统一（本页令牌由 .uhd-shell-scope 供给浅色值） */
.member-sub-entry__btn {
  flex: 0 0 auto;
  padding: 10px 20px;
  border: 1.5px solid transparent;
  border-radius: 999px;
  background:
    var(--uhd-rainbow-soft) padding-box,
    var(--uhd-rainbow) border-box;
  color: #111111;
  font-size: 14px;
  font-weight: 700;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.65),
    var(--uhd-shadow-card-l);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
}
.modal-title { margin: 0; font-size: 17px; font-weight: 600; color: var(--ink); }
.confirm-text { margin: 0; font-size: 14px; line-height: 1.6; color: var(--ink); }
.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
.m-btn {
  height: 44px;
  padding: 0 16px;
  border-radius: var(--paper-r-md, 10px);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}
.m-btn.ghost {
  background: var(--paper2);
  border: 1px solid var(--line);
  color: var(--ink2);
}
/* 主钮 UHD 口径（2026-11-13 补齐七彩＋玻璃）：彩虹边框 + 淡彩粉彩底 + 玻璃栈
   （2026-11-14 摘钉浅色后外观跟随用户三态；本钮底色为不透明粉彩，配方两态共用） */
.m-btn.primary {
  border: 1.5px solid transparent;
  background:
    var(--uhd-rainbow-soft) padding-box,
    var(--uhd-rainbow) border-box;
  color: #111111;
  font-weight: 600;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.65),
    var(--uhd-shadow-card-l);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
}
/* 禁用：彩虹退场、中性灰 */
.m-btn.primary:disabled {
  background: var(--paper2);
  color: var(--ink2);
  box-shadow: inset 0 0 0 1px var(--line);
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
.m-btn:disabled { opacity: 0.5; cursor: not-allowed; }
/* 桌面宽随页根 .member-page 的 1000px 口径（见页头注释），本页不再自收 720 */
</style>
