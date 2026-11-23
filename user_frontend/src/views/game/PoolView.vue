<template>
  <!-- 贡献池（2026-11-24 池专属授权入口）：绑盘下线后入池的唯一
       用户端入口。介绍＋授权/申请/状态全在本页：未授权 → 用
       Google 账号授权（池专属 OAuth，只落一条池用绑定行）→
       授权完自动提交申请 → 待管理员审批 → 已入池。容量门槛与
       审批都走后端既有口径，本页只呈现状态与原因，不自判。 -->
  <AxAccountShellGate>
  <div class="pl-page">
    <div class="pl-inner">
      <header class="pl-head">
        <h1 class="pl-title">贡献池</h1>
      </header>

      <div v-if="loadError" class="pl-err">
        {{ loadError }}<button type="button" class="pl-retry" @click="() => loadStatus()">重试</button>
      </div>
      <div v-else-if="loading" class="pl-loading" role="status" aria-live="polite">
        <span class="pl-spin" aria-hidden="true"></span>
        <p class="pl-loading__text">正在加载贡献池…</p>
      </div>

      <template v-else-if="status">
        <!-- 介绍卡：池是干什么的、贡献有什么回报、授权只用在池里 -->
        <section class="pl-card pl-intro">
          <div class="pl-intro__head">
            <span class="pl-intro__icon" aria-hidden="true">
              <HardDriveDownload :size="26" />
            </span>
            <div class="pl-intro__text">
              <h2 class="pl-intro__title">把你的云盘贡献给公共池</h2>
              <p class="pl-intro__desc">
                入池后，你网盘里的片源可以被备进公共池，供其他付费用户播放；
                你的盘被播放时，你会获得钱包余额奖励。授权只用于池里取流，
                不会把你的账号令牌发给任何观众。
              </p>
            </div>
          </div>
        </section>

        <!-- 逐个已授权盘的状态卡 -->
        <section v-for="entry in status.bindings" :key="entry.binding_id" class="pl-card">
          <div class="pl-row">
            <div class="pl-row__main">
              <p class="pl-row__name">{{ entry.account_label }}</p>
              <p class="pl-row__state">{{ entryStateText(entry) }}</p>
              <p v-if="entryNote(entry)" class="pl-row__note">{{ entryNote(entry) }}</p>
            </div>
            <span class="pl-badge" :class="`pl-badge--${entryTone(entry)}`">
              {{ entryBadge(entry) }}
            </span>
          </div>
          <div class="pl-actions">
            <AxButton
              v-if="entry.can_apply"
              variant="primary"
              size="sm"
              :dark="themeResolved === 'dark'"
              :disabled="applyingId === entry.binding_id"
              @click="applyEntry(entry)"
            >
              {{ applyingId === entry.binding_id ? '提交中…' : '提交入池申请' }}
            </AxButton>
            <AxButton
              v-if="entry.member && entry.member.status === 'active'"
              variant="secondary"
              size="sm"
              :dark="themeResolved === 'dark'"
              :disabled="withdrawing"
              @click="withdraw(entry)"
            >
              {{ withdrawing ? '处理中…' : '退出贡献池' }}
            </AxButton>
            <AxButton
              v-if="entry.binding_status !== 'active' && !entry.member"
              variant="primary"
              size="sm"
              :dark="themeResolved === 'dark'"
              :disabled="authorizing"
              @click="startAuth(entry.account_label)"
            >
              重新授权
            </AxButton>
          </div>
        </section>

        <!-- 还没有任何授权盘：授权卡（起名 + 去 Google 授权） -->
        <section v-if="status.bindings.length === 0" class="pl-card">
          <h2 class="pl-card__title">授权你的 Google 云盘</h2>
          <p class="pl-card__desc">
            点下面的按钮会打开 Google 授权页，用你自己的 Google 账号确认后，
            回到这里就会自动提交入池申请，管理员审核通过后正式入池。
            入池的盘需要达到站点的容量门槛，容量不够会在这里直接告诉你原因。
          </p>
          <label class="pl-field">
            <span class="pl-field__label">给这个云盘起个名字</span>
            <input
              v-model="label"
              class="pl-input"
              type="text"
              maxlength="100"
              placeholder="例如：我的谷歌盘"
            />
          </label>
          <div class="pl-actions">
            <AxButton
              variant="primary"
              :dark="themeResolved === 'dark'"
              :disabled="authorizing"
              @click="startAuth(label)"
            >
              {{ authorizing ? '正在打开…' : '用 Google 账号授权' }}
            </AxButton>
          </div>
          <p v-if="authorizing || polling" class="pl-hint">
            已打开 Google 授权页，完成授权后回到这里即可，不用再点别的。
          </p>
        </section>
      </template>
    </div>
  </div>
  </AxAccountShellGate>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import './pool-view.css'
import { HardDriveDownload } from 'lucide-vue-next'
import AxAccountShellGate from '@/components/ax/AxAccountShellGate.vue'
import AxButton from '@/components/ax/AxButton.vue'
import { useTheme } from '@/composables/useTheme'
import { useToast } from '@/composables/useToast'
import {
  poolContributionApi,
  type PoolContributionBinding,
  type PoolContributionResponse,
} from '@/api/economy'

type PoolBindingEntry = PoolContributionBinding
type PoolStatus = PoolContributionResponse

const { resolved: themeResolved } = useTheme()
const toast = useToast()

const status = ref<PoolStatus | null>(null)
const loading = ref(true)
const loadError = ref('')
const label = ref('')
const authorizing = ref(false)
const polling = ref(false)
const applyingId = ref<number | null>(null)
const withdrawing = ref(false)
let pollTimer: number | null = null
let pollTicks = 0

/** 本页错误文案：后端 detail 本来就是人话，优先透传。 */
function poolErrorMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail
  return typeof detail === 'string' && detail ? detail : fallback
}

async function loadStatus(silent = false) {
  if (!silent) loading.value = true
  try {
    status.value = await poolContributionApi.status()
    loadError.value = ''
  } catch (err) {
    if (!silent) loadError.value = poolErrorMessage(err, '贡献池信息加载失败，请稍后重试')
  } finally {
    if (!silent) loading.value = false
  }
}

function entryBadge(entry: PoolBindingEntry): string {
  const m = entry.member
  if (m) {
    if (m.status === 'active') return '已入池'
    if (m.status === 'pending') return '待审批'
    if (m.status === 'candidate') return '待处理'
    if (m.status === 'suspended') return '已暂停'
    if (m.status === 'rejected') return '未通过'
    if (m.status === 'ejected') return '已清退'
    if (m.status === 'left') return '已退出'
    return m.status
  }
  if (entry.binding_status !== 'active') return '授权失效'
  if (entry.can_apply) return '可申请'
  return '暂不能申请'
}

function entryTone(entry: PoolBindingEntry): string {
  const m = entry.member
  if (m?.status === 'active') return 'ok'
  if (m?.status === 'pending' || m?.status === 'candidate') return 'wait'
  if (m) return 'bad'
  if (entry.binding_status !== 'active') return 'bad'
  return entry.can_apply ? 'ok' : 'wait'
}

function entryStateText(entry: PoolBindingEntry): string {
  const m = entry.member
  if (m) {
    if (m.status === 'active') return '这个盘已经在公共池里，正在为其他用户提供片源。'
    if (m.status === 'pending') return '申请已提交，正在等管理员审核。'
    if (m.status === 'candidate') return '申请已登记，正在等后续处理。'
    if (m.status === 'suspended') return '这个盘已被管理员暂停出池。'
    if (m.status === 'rejected') return '上次申请没有通过审核。'
    if (m.status === 'ejected') return '这个盘已被管理员清退。'
    if (m.status === 'left') return '你已经退出过这个盘的入池。'
    return ''
  }
  if (entry.binding_status !== 'active') {
    return '这个盘的授权已经失效，重新授权后才能申请入池。'
  }
  if (entry.can_apply) return '授权完成，可以提交入池申请了。'
  return '暂时还不能申请入池。'
}

function entryNote(entry: PoolBindingEntry): string {
  if (entry.member?.suspended_reason) return entry.member.suspended_reason
  if (!entry.member && !entry.can_apply && entry.apply_block_reason) {
    return entry.apply_block_reason
  }
  return ''
}

async function applyEntry(entry: PoolBindingEntry) {
  applyingId.value = entry.binding_id
  try {
    await poolContributionApi.apply(entry.binding_id)
    toast.success('申请已提交，等管理员审核')
  } catch (err) {
    toast.error(poolErrorMessage(err, '提交申请失败，请稍后再试'))
  } finally {
    applyingId.value = null
    await loadStatus(true)
  }
}

async function withdraw(entry: PoolBindingEntry) {
  if (!entry.member) return
  withdrawing.value = true
  try {
    await poolContributionApi.withdraw(entry.member.id)
    toast.success('已退出贡献池')
  } catch (err) {
    toast.error(poolErrorMessage(err, '退出失败，请稍后再试'))
  } finally {
    withdrawing.value = false
    await loadStatus(true)
  }
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
  polling.value = false
}

/** 授权弹窗打开后轮询状态：新绑定行一出现就停轮询，并对可申请
 * 的盘自动提交申请（用户点授权就是为了入池，不多绕一步）。 */
function startPolling() {
  stopPolling()
  polling.value = true
  pollTicks = 0
  pollTimer = window.setInterval(() => {
    pollTicks += 1
    void (async () => {
      await loadStatus(true)
      const entries = status.value?.bindings ?? []
      if (entries.length > 0) {
        stopPolling()
        const entry = entries.find((e) => e.can_apply)
        if (entry) void applyEntry(entry)
      } else if (pollTicks >= 40) {
        stopPolling()
      }
    })()
  }, 3000)
}

async function startAuth(rawLabel: string) {
  const name = (rawLabel || '').trim()
  if (!name) {
    toast.error('先给这个云盘起个名字')
    return
  }
  authorizing.value = true
  try {
    const res = await poolContributionApi.startOAuth(name)
    if (!res?.authorization_url) throw new Error('missing authorization_url')
    window.open(res.authorization_url, '_blank', 'width=520,height=720')
    toast.success('已打开 Google 授权页，完成授权后回到这里')
    startPolling()
  } catch (err) {
    toast.error(poolErrorMessage(err, '发起授权失败，请稍后再试'))
  } finally {
    authorizing.value = false
  }
}

onMounted(() => {
  void loadStatus()
})

onBeforeUnmount(stopPolling)
</script>

<style scoped>
.pl-page {
  min-height: 100dvh;
  background: #050505;
  color: #ffffff;
  font-family: var(--ax-font-sans);
  -webkit-font-smoothing: antialiased;
}

html[data-theme='light'] .pl-page {
  background: #f8f8f8;
  color: #111111;
}

.pl-inner {
  max-width: 880px;
  margin: 0 auto;
  padding: 28px 20px 64px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.pl-title {
  font-size: 24px;
  font-weight: 700;
  margin: 0;
}

.pl-card {
  background: var(--pl-card);
  border: 1px solid var(--pl-line);
  border-radius: 16px;
  padding: 20px;
}

.pl-card__title {
  font-size: 17px;
  font-weight: 600;
  margin: 0 0 8px;
}

.pl-card__desc {
  color: var(--pl-text-2);
  font-size: 14px;
  line-height: 1.6;
  margin: 0 0 16px;
}

.pl-intro__head {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}

.pl-intro__icon {
  display: inline-flex;
  padding: 10px;
  border-radius: 12px;
  background: var(--pl-input);
}

.pl-intro__title {
  font-size: 17px;
  font-weight: 600;
  margin: 0 0 6px;
}

.pl-intro__desc {
  color: var(--pl-text-2);
  font-size: 14px;
  line-height: 1.6;
  margin: 0;
}

.pl-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.pl-row__name {
  font-size: 16px;
  font-weight: 600;
  margin: 0 0 4px;
}

.pl-row__state {
  color: var(--pl-text-2);
  font-size: 14px;
  line-height: 1.6;
  margin: 0;
}

.pl-row__note {
  color: var(--pl-wait);
  font-size: 13px;
  line-height: 1.6;
  margin: 6px 0 0;
}

.pl-badge {
  flex: none;
  font-size: 12px;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--pl-line);
}

.pl-badge--ok { color: var(--pl-ok); }
.pl-badge--wait { color: var(--pl-wait); }
.pl-badge--bad { color: var(--pl-bad); }

.pl-actions {
  display: flex;
  gap: 10px;
  margin-top: 16px;
  flex-wrap: wrap;
}

.pl-actions:empty {
  display: none;
}

.pl-field {
  display: block;
  margin-bottom: 4px;
}

.pl-field__label {
  display: block;
  font-size: 13px;
  color: var(--pl-text-2);
  margin-bottom: 8px;
}

.pl-input {
  width: 100%;
  box-sizing: border-box;
  background: var(--pl-input);
  border: 1px solid var(--pl-line);
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 14px;
  color: inherit;
}

.pl-hint {
  color: var(--pl-text-3);
  font-size: 13px;
  margin: 12px 0 0;
}

.pl-err {
  color: var(--pl-bad);
  font-size: 14px;
}

.pl-retry {
  margin-left: 10px;
  text-decoration: underline;
  background: none;
  border: none;
  color: inherit;
  cursor: pointer;
  font: inherit;
}

.pl-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--pl-text-2);
}

.pl-spin {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 2px solid var(--pl-line);
  border-top-color: currentColor;
  animation: pl-spin 0.8s linear infinite;
}

@keyframes pl-spin {
  to { transform: rotate(360deg); }
}

.pl-loading__text {
  margin: 0;
  font-size: 14px;
}
</style>
