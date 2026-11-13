<!--
  TgBindGuideView.vue
  注册后 TG 绑定引导页
  用途：管理员要求用户绑定 Telegram 后才能使用系统功能时展示的引导页。
  流程：检查绑定状态 → 获取绑定码 → 引导用户向 Bot 发送 /bind 指令 → 轮询绑定状态 → 自动跳转。
-->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Send, Check } from 'lucide-vue-next'
import { tgApi, type TgBindStatus } from '@/api/tg'
import { useUserStore } from '@/stores/user'
import { useToast } from '@/composables/useToast'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const toast = useToast()

/** redirect 白名单：只允许站内路径，防止开放重定向 */
function safeRedirect(v: unknown): string {
  const s = Array.isArray(v) ? String(v[0] ?? '') : String(v ?? '')
  if (s.startsWith('/') && !s.startsWith('//') && !s.includes('://')) return s
  return '/'
}

const redirect = safeRedirect(route.query.redirect)

const loading = ref(true)          // 初始加载
const notConfigured = ref(false)   // bot_username 为空
const botUsername = ref('')
const bindCode = ref('')
const expiresIn = ref(0)           // 剩余秒数
const refreshing = ref(false)
const copyHint = ref('')           // 行内复制提示
const loadError = ref(false)    // 初始状态拉取失败：给错误态+重试，不渲染空白 bot 名/码
const loggingOut = ref(false)   // 退出登录进行中
let copyHintTimer: ReturnType<typeof setTimeout> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let tickTimer: ReturnType<typeof setInterval> | null = null
let codeDeadline = 0               // 绑定码过期时间戳（Date.now 基准）

const codeExpired = computed(() => expiresIn.value <= 0)
const countdownText = computed(() => {
  const s = Math.max(0, expiresIn.value)
  const m = Math.floor(s / 60)
  const ss = s % 60
  return `${m}:${String(ss).padStart(2, '0')}`
})

const commandText = computed(() => `/bind ${bindCode.value}`)

function clearTimers() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  if (tickTimer) { clearInterval(tickTimer); tickTimer = null }
}

function startTick() {
  if (tickTimer) clearInterval(tickTimer)
  const update = () => {
    const left = Math.max(0, Math.round((codeDeadline - Date.now()) / 1000))
    expiresIn.value = left
    if (left <= 0) {
      // R108-2：码过期了轮询不能再空等旧码，停掉并提示换码
      clearTimers()
      toast.info('绑定码已过期，点「重新获取」换个码')
    }
  }
  update()
  tickTimer = setInterval(update, 1000)
}

const POLL_INTERVAL = 3000
// R108-1：200 次 × 3 秒 = 600 秒，对齐后端 CODE_TTL_SECONDS（10 分钟）；
// 此前 100 次（5 分钟）会让码还有效时轮询先停，用户按指引发码也无人响应
const MAX_POLLS = 200
let pollCount = 0
let pollStart = 0
let pollInFlight = false
let pollFailStreak = 0   // R108-7：轮询连续失败计数
let pollNetWarned = false

function startPoll() {
  if (pollTimer) clearInterval(pollTimer)
  pollCount = 0
  pollStart = Date.now()
  pollFailStreak = 0
  pollNetWarned = false
  pollTimer = setInterval(async () => {
    if (pollInFlight) return
    pollInFlight = true
    try {
      const st: TgBindStatus = await tgApi.status()
      pollFailStreak = 0
      if (st.bound) {
        clearTimers()
        router.replace(redirect)
        return
      }
    } catch {
      // R108-7：轮询期网络中断不再静默，连续失败 3 次提示一次
      pollFailStreak += 1
      if (pollFailStreak >= 3 && !pollNetWarned) {
        pollNetWarned = true
        toast.info('网络有点晃，还在等你完成绑定')
      }
    } finally {
      pollInFlight = false
    }
    pollCount += 1
    if (pollCount >= MAX_POLLS || Date.now() - pollStart > MAX_POLLS * POLL_INTERVAL) {
      clearTimers()
      toast.info('长时间没扫上，点「重新获取」换个码再试')
    }
  }, POLL_INTERVAL)
}

async function fetchBindCode() {
  const res = await tgApi.bindCode()
  bindCode.value = res.code
  // 时间戳差值倒计时：切后台/休眠回来不漂移；后端没给时兜底 0
  codeDeadline = Date.now() + (res.expires_in || 0) * 1000
  expiresIn.value = Math.max(0, res.expires_in || 0)
  if (res.bot_username) botUsername.value = res.bot_username
}

async function refreshCode() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await fetchBindCode()
    startTick()
    // R108-3：轮询已停（过期/超上限）时重启，否则新码绑定成功无人感知、自动跳转不触发
    if (!pollTimer) startPoll()
  } catch {
    toast.error('刷新失败，点「重新获取」再试一次')
  } finally {
    refreshing.value = false
  }
}

async function copyCommand() {
  const text = commandText.value
  try {
    await navigator.clipboard.writeText(text)
    showCopyHint('已复制到剪贴板')
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.readOnly = true
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
      showCopyHint('已复制到剪贴板')
    } catch {
      showCopyHint('复制失败，请手动复制')
    }
  }
}

function showCopyHint(msg: string) {
  copyHint.value = msg
  if (copyHintTimer) clearTimeout(copyHintTimer)
  copyHintTimer = setTimeout(() => { copyHint.value = '' }, 2000)
}

function openBot() {
  window.open('https://t.me/' + botUsername.value, '_blank', 'noopener,noreferrer')
}

async function logout() {
  if (loggingOut.value) return
  loggingOut.value = true
  try {
    await userStore.logout()
    router.push('/login')
  } catch {
    toast.error('退出失败，请重试')
  } finally {
    loggingOut.value = false
  }
}

async function init() {
  loadError.value = false
  notConfigured.value = false
  loading.value = true
  clearTimers()
  try {
    const st = await tgApi.status()
    if (st.bound || !st.required || !st.guide_enabled) {
      router.replace(redirect)
      return
    }
    if (!st.bot_username) {
      notConfigured.value = true
      return
    }
    botUsername.value = st.bot_username
    await fetchBindCode()
    startTick()
    startPoll()
  } catch {
    // 初始状态拉取失败：给错误态 + 重试按钮，不再渲染空白 bot 名/码
    loadError.value = true
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  init()
})

onUnmounted(() => {
  clearTimers()
  if (copyHintTimer) clearTimeout(copyHintTimer)
})
</script>

<template>
  <div class="tg-guide-page">
    <div class="tg-guide-card">
      <header class="guide-header">
        <div class="header-icon">
          <Send :size="32" aria-hidden="true" />
        </div>
        <h1>请先绑定 Telegram</h1>
        <p class="header-sub">管理员要求所有用户绑定 Telegram 后才能使用系统功能，完成绑定后将自动进入面板</p>
      </header>

      <div v-if="loading" class="loading-box" role="status" aria-label="正在加载绑定指引">
        <span class="loading-spinner" aria-hidden="true"></span>
        <span class="loading-text">正在加载…</span>
      </div>

      <div v-else-if="loadError" class="warn-box" role="alert">
        <p>没连上服务器，请检查网络后重试</p>
        <button class="btn btn-primary" @click="init">重试</button>
      </div>

      <div v-else-if="notConfigured" class="warn-box" role="alert">
        <p>Telegram 绑定功能还没开启，请联系服主开启后再试</p>
        <button class="btn btn-secondary" @click="init">重新检查</button>
      </div>

      <template v-else>
        <ol class="steps">
          <li class="step-row">
            <span class="step-num" aria-hidden="true">1</span>
            <div class="step-main">
              <h2 class="step-title">打开 Telegram Bot</h2>
              <p class="step-desc">
                <span class="at">@</span>
                <span class="mono-ellipsis">{{ botUsername }}</span>
              </p>
              <button class="btn btn-primary" @click="openBot">打开 Telegram Bot</button>
            </div>
          </li>

          <li class="step-row">
            <span class="step-num" aria-hidden="true">2</span>
            <div class="step-main">
              <h2 class="step-title">发送绑定指令</h2>
              <div class="command-box">{{ commandText }}</div>
              <div class="step-actions">
                <button class="btn btn-secondary btn-sm" :disabled="codeExpired" @click="copyCommand">复制指令</button>
                <p v-if="copyHint" class="copy-hint" aria-live="polite">{{ copyHint }}</p>
              </div>
            </div>
          </li>
        </ol>

        <section class="code-panel">
          <span class="code-label">绑定码</span>
          <div class="code-big">{{ bindCode }}</div>
          <div class="countdown-row">
            <span v-if="!codeExpired" class="countdown-text">{{ countdownText }} 后过期</span>
            <span v-else class="expired-text">绑定码已过期，请刷新</span>
          </div>
          <button class="btn btn-secondary btn-sm" :disabled="refreshing" @click="refreshCode">{{ refreshing ? '刷新中…' : '重新获取' }}</button>
        </section>

        <div class="auto-hint">
          <Check :size="16" class="check-icon" aria-hidden="true" />
          <span>发送指令后，将自动进入面板</span>
        </div>
      </template>

      <div class="logout-row">
        <button class="link-btn logout-btn" :disabled="loggingOut" @click="logout">{{ loggingOut ? '退出中…' : '退出登录' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* UHD 办事面重排（说明书 §2.6 步骤行变体 56px）：浅色办事面令牌为本面口径，
   缺省深色面、html[data-theme='light'] 换浅，跟随用户外观三态
   （与 LoginView / AccountMenuView 同口径）。只引用 --uhd-* 令牌。 */
.tg-guide-page {
  min-height: 100vh;
  min-height: 100dvh; /* 移动端地址栏收放高度不跳 */
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  padding-bottom: calc(24px + env(safe-area-inset-bottom)); /* iOS 底部安全区 */
  background: var(--uhd-d-bg);
  color: var(--uhd-d-text);
  font-family: var(--uhd-font-sans);
}

.tg-guide-card {
  width: 100%;
  max-width: 560px;
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding: 32px;
  background: var(--uhd-d-surface);
  border: 1px solid var(--uhd-d-border);
  border-radius: var(--uhd-radius-card);
}

.guide-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 12px;
}

.header-icon {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: var(--uhd-d-surface-2);
  color: var(--uhd-d-text);
  display: flex;
  align-items: center;
  justify-content: center;
}

.guide-header h1 {
  margin: 0;
  font-size: var(--uhd-fs-h3);
  font-weight: var(--uhd-fw-extrabold);
  line-height: 1.25;
}

.header-sub {
  margin: 0;
  font-size: var(--uhd-fs-caption);
  line-height: var(--uhd-lh-body);
  color: var(--uhd-d-text-2);
}

/* 加载态：说明书 1.6 统一口径——旋转圈＋「正在加载…」灰字（禁骨架屏占位） */
.loading-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 32px 0;
}

.loading-spinner {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: 2px solid var(--uhd-d-border-2);
  border-top-color: var(--uhd-d-text-2);
  animation: uhd-spin 0.8s linear infinite;
}

.loading-text {
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-d-text-2);
}

.warn-box {
  padding: 16px;
  border-radius: var(--uhd-radius-card-sm);
  background: rgba(220, 38, 38, 0.07);
  border: 1px solid rgba(220, 38, 38, 0.32);
  color: #fca5a5;
  font-size: var(--uhd-fs-caption);
}

.warn-box p {
  margin: 0 0 12px;
  line-height: var(--uhd-lh-body);
}

.warn-box .btn {
  width: 100%;
}

/* 步骤行（§2.6 变体）：序号圆＋标题 16 粗＋副文 14 灰，行间 1px 分隔 */
.steps {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
}

.step-row {
  display: flex;
  gap: 14px;
  padding: 18px 0;
}

.step-row:first-child {
  padding-top: 2px;
}

.step-row:last-child {
  padding-bottom: 2px;
}

.step-row + .step-row {
  border-top: 1px solid var(--uhd-d-border);
}

.step-num {
  flex-shrink: 0;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: var(--uhd-d-surface-raised);
  color: var(--uhd-d-text);
  font-size: 13px;
  font-weight: var(--uhd-fw-bold);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 1px;
}

.step-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.step-title {
  margin: 0;
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-bold);
  line-height: 1.4;
}

.step-desc {
  margin: 0;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-d-text-2);
  display: flex;
  align-items: center;
}

.mono-ellipsis {
  min-width: 0;
  font-family: var(--uhd-font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 胶囊按钮（§2.2）：主＝彩虹边框＋深色毛玻璃底（浅色面换淡彩底）；次＝描边胶囊；小胶囊 38px */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 48px;
  min-height: 44px;
  padding: 0 26px;
  border-radius: var(--uhd-radius-pill);
  font-family: var(--uhd-font-sans);
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  white-space: nowrap;
  cursor: pointer;
  transition: opacity var(--uhd-dur-ui) var(--uhd-ease-standard);
}

.btn-sm {
  height: 38px;
  min-height: 38px;
  padding: 0 18px;
  font-size: var(--uhd-fs-caption);
}

.btn:focus-visible,
.link-btn:focus-visible {
  outline: 2px solid var(--uhd-l-link);
  outline-offset: 2px;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-primary {
  width: 100%;
  color: var(--uhd-d-text);
  border: var(--uhd-rainbow-border-w) solid transparent;
  background:
    linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box,
    var(--uhd-rainbow) border-box;
}

.btn-secondary {
  color: var(--uhd-d-text);
  border: 1px solid var(--uhd-d-border-2);
  background: transparent;
}

.command-box {
  background: var(--uhd-d-surface-2);
  border: 1px solid var(--uhd-d-border);
  border-radius: var(--uhd-radius-input);
  font-family: var(--uhd-font-mono);
  font-size: var(--uhd-fs-lead);
  padding: 12px 16px;
  /* 长指令允许横向滚动看全，不截断 */
  overflow-x: auto;
  white-space: nowrap;
}

.step-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.copy-hint {
  margin: 0;
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-success);
}

.code-panel {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 24px;
  background: var(--uhd-d-surface-2);
  border: 1px solid var(--uhd-d-border);
  border-radius: var(--uhd-radius-card-sm);
}

.code-label {
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-d-text-2);
}

.code-big {
  font-family: var(--uhd-font-mono);
  font-size: 40px;
  font-weight: var(--uhd-fw-bold);
  letter-spacing: 10px;
  padding-left: 10px;
  text-align: center;
  white-space: nowrap;
  max-width: 100%;
  overflow-x: auto;
  line-height: 1.2;
}

.countdown-row {
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-d-text-2);
  white-space: nowrap;
}

.expired-text {
  color: var(--uhd-danger);
  white-space: nowrap;
}

.auto-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-d-text-2);
}

.check-icon {
  color: var(--uhd-success);
  flex-shrink: 0;
}

.logout-row {
  text-align: center;
}

.link-btn {
  min-height: 44px;
  padding: 0 12px;
  background: transparent;
  border: none;
  cursor: pointer;
  font-family: var(--uhd-font-sans);
  font-size: var(--uhd-fs-caption);
}

/* 退出＝红色文字按钮，不做填充（§2.2） */
.logout-btn {
  color: var(--uhd-danger);
  font-weight: var(--uhd-fw-medium);
}

.logout-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 浅色办事面：跟随用户外观（html[data-theme='light']） */
html[data-theme='light'] .tg-guide-page {
  background: var(--uhd-l-bg);
  color: var(--uhd-l-text);
}

html[data-theme='light'] .tg-guide-card {
  background: var(--uhd-l-surface);
  border-color: var(--uhd-l-border);
  box-shadow: var(--uhd-shadow-card-l);
}

html[data-theme='light'] .header-icon {
  background: var(--uhd-l-input-bg);
  color: var(--uhd-l-text);
}

html[data-theme='light'] .header-sub {
  color: var(--uhd-l-text-2);
}

html[data-theme='light'] .loading-spinner {
  border-color: var(--uhd-l-border);
  border-top-color: var(--uhd-l-text-2);
}

html[data-theme='light'] .loading-text {
  color: var(--uhd-l-text-2);
}

html[data-theme='light'] .warn-box {
  background: rgba(220, 38, 38, 0.05);
  border-color: rgba(220, 38, 38, 0.25);
  /* 浅色下加深文字色使对比度达标（沿用旧面实测值） */
  color: #b93a22;
}

html[data-theme='light'] .step-row + .step-row {
  border-top-color: var(--uhd-l-divider);
}

html[data-theme='light'] .step-num {
  background: var(--uhd-l-text);
  color: var(--uhd-l-surface);
}

html[data-theme='light'] .step-desc {
  color: var(--uhd-l-text-2);
}

html[data-theme='light'] .btn-primary {
  color: var(--uhd-l-text);
  background:
    var(--uhd-rainbow-soft) padding-box,
    var(--uhd-rainbow) border-box;
}

html[data-theme='light'] .btn-secondary {
  color: var(--uhd-l-text);
  border-color: var(--uhd-l-border);
  background: var(--uhd-l-surface);
}

html[data-theme='light'] .command-box {
  background: var(--uhd-l-input-bg);
  border-color: var(--uhd-l-border);
}

html[data-theme='light'] .code-panel {
  background: var(--uhd-l-input-bg);
  border-color: var(--uhd-l-border);
}

html[data-theme='light'] .code-label {
  color: var(--uhd-l-text-2);
}

html[data-theme='light'] .countdown-row {
  color: var(--uhd-l-text-2);
}

html[data-theme='light'] .auto-hint {
  color: var(--uhd-l-text-2);
}

@media (prefers-reduced-motion: reduce) {
  .loading-spinner {
    animation-duration: 1.6s;
  }
}

@media (max-width: 768px) {
  .tg-guide-page {
    padding: 16px;
    padding-bottom: calc(16px + env(safe-area-inset-bottom)); /* iOS 底部安全区 */
  }

  .tg-guide-card {
    padding: 24px 20px;
  }

  .guide-header h1 {
    font-size: 20px;
  }

  .code-panel {
    padding: 16px;
  }

  .code-big {
    font-size: 32px;
    letter-spacing: 8px;
    padding-left: 8px;
  }
}
</style>
