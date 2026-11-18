<template>
  <div v-if="open" class="acct-mask" @click.self="close" @keydown="onKeydown">
    <div class="acct-modal" role="dialog" aria-modal="true" aria-label="修改登录密码">
      <h3 class="acct-modal-title">修改登录密码</h3>
      <div class="acct-field">
        <label class="acct-label" for="pwd-old">当前密码</label>
        <input id="pwd-old" ref="oldPwdInput" v-model="form.oldPassword" type="password" autocomplete="current-password" />
      </div>
      <!-- G-53：有恢复邮箱时改密要过邮箱验证码；无邮箱不渲染本字段，只验当前密码 -->
      <div v-if="emailMode" class="acct-field">
        <label class="acct-label" for="pwd-code">邮箱验证码</label>
        <div class="acct-code-row">
          <input id="pwd-code" v-model="form.emailCode" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="6 位验证码" />
          <button type="button" class="acct-btn ghost acct-code-btn" :disabled="codeSending || countdown > 0" @click="sendCode">
            {{ countdown > 0 ? `${countdown} 秒后重发` : '发送验证码' }}
          </button>
        </div>
        <p class="acct-hint">验证码会发到你的恢复邮箱 {{ maskedEmail }}，10 分钟内有效。</p>
      </div>
      <div class="acct-field">
        <label class="acct-label" for="pwd-new">新密码</label>
        <input id="pwd-new" v-model="form.newPassword" type="password" autocomplete="new-password" />
      </div>
      <div class="acct-field">
        <label class="acct-label" for="pwd-confirm">确认新密码</label>
        <input id="pwd-confirm" v-model="form.confirmPassword" type="password" autocomplete="new-password" />
      </div>
      <p v-if="error" class="acct-error" role="alert">{{ error }}</p>
      <div class="acct-modal-actions">
        <button type="button" class="acct-btn ghost" :disabled="loading" @click="close">取消</button>
        <button type="button" class="acct-btn primary" :disabled="loading" @click="submit">
          {{ loading ? '提交中' : '确认修改' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 修改登录密码弹窗（自旧 AccountSettingsView 原样抽出）：
 * authApi.changePassword 全仓唯一改密入口；焦点陷阱 / Esc / 关闭后焦点
 * 回触发按钮 / 提交在途不许关，全部沿用原实现。
 * G-53（2026-11-18 定案）：新密码 8–64 位；账号有恢复邮箱时改密须带
 * 邮箱验证码（sendChangePasswordCode 发到现邮箱），无邮箱只验当前
 * 密码、不渲染验证码字段、不锁死。
 */
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { authApi } from '@/api'
import { useToast } from '@/composables/useToast'
import { useUserStore } from '@/stores/user'
import { toastCopy } from '@/constants/toastMessages'
import { toUserMessage } from '@/utils/toUserMessage'
import { maskEmail } from '@/utils/maskEmail'
import '../../styles/modal-glass.css'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()

const toast = useToast()
const userStore = useUserStore()
const form = ref({ oldPassword: '', newPassword: '', confirmPassword: '', emailCode: '' })
const loading = ref(false)
const error = ref('')
const oldPwdInput = ref<HTMLInputElement | null>(null)

/** 有恢复邮箱＝邮箱验证码模式；无邮箱＝只验当前密码（后端同口径） */
const emailMode = computed(() => !!(userStore.user?.email || '').trim())
const maskedEmail = computed(() => maskEmail(userStore.user?.email))
const codeSending = ref(false)
const countdown = ref(0)
let countdownTimer: ReturnType<typeof setInterval> | null = null

function startCountdown() {
  countdown.value = 60
  if (countdownTimer) clearInterval(countdownTimer)
  countdownTimer = setInterval(() => {
    countdown.value -= 1
    if (countdown.value <= 0 && countdownTimer) {
      clearInterval(countdownTimer)
      countdownTimer = null
    }
  }, 1000)
}

async function sendCode() {
  if (codeSending.value || countdown.value > 0) return
  codeSending.value = true
  error.value = ''
  try {
    await authApi.sendChangePasswordCode()
    toast.success('验证码已发送，去邮箱里看一下')
    startCountdown()
  } catch (err: any) {
    error.value = toUserMessage(err, 'settingFailed')
  } finally {
    codeSending.value = false
  }
}
/** 关闭后焦点回到触发按钮 */
const lastTrigger = ref<HTMLElement | null>(null)

function onKeydown(e: KeyboardEvent) {
  if (e.key !== 'Tab' || !props.open) return
  const card = (e.currentTarget as HTMLElement).querySelector('.acct-modal')
  if (!card) return
  const els = Array.from(
    card.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'),
  ).filter((el) => el.offsetParent !== null)
  if (els.length === 0) return
  const first = els[0]
  const last = els[els.length - 1]
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault()
    last.focus()
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault()
    first.focus()
  }
}
function onEsc(e: KeyboardEvent) {
  if (e.key === 'Escape' && props.open) close()
}
function openModal() {
  error.value = ''
  lastTrigger.value = document.activeElement as HTMLElement | null
  document.body.style.overflow = 'hidden'
  window.addEventListener('keydown', onEsc)
  nextTick(() => oldPwdInput.value?.focus())
}
function close() {
  // 提交在途时不允许关闭，否则在途请求的失败原因会被清空后静默丢失
  if (loading.value) return
  emit('update:open', false)
}
function teardown() {
  document.body.style.overflow = ''
  form.value = { oldPassword: '', newPassword: '', confirmPassword: '', emailCode: '' }
  if (countdownTimer) {
    clearInterval(countdownTimer)
    countdownTimer = null
  }
  countdown.value = 0
  window.removeEventListener('keydown', onEsc)
  lastTrigger.value?.focus?.()
}
watch(
  () => props.open,
  (v) => {
    if (v) openModal()
    else teardown()
  },
)
onUnmounted(() => {
  window.removeEventListener('keydown', onEsc)
  document.body.style.overflow = ''
  if (countdownTimer) clearInterval(countdownTimer)
})

async function submit() {
  const { oldPassword, newPassword, confirmPassword, emailCode } = form.value
  if (!oldPassword || !newPassword || !confirmPassword) {
    error.value = '请填写完整'
    return
  }
  if (newPassword.length < 8 || newPassword.length > 64) {
    error.value = '新密码要 8-64 位'
    return
  }
  if (newPassword !== confirmPassword) {
    error.value = '两次输入的新密码不一致'
    return
  }
  if (emailMode.value && !emailCode.trim()) {
    error.value = '请输入邮箱验证码'
    return
  }
  error.value = ''
  loading.value = true
  try {
    await authApi.changePassword({
      old_password: oldPassword,
      new_password: newPassword,
      ...(emailMode.value ? { email_code: emailCode.trim() } : {}),
    })
    toast.success(toastCopy.passwordChanged())
    emit('update:open', false)
    form.value = { oldPassword: '', newPassword: '', confirmPassword: '', emailCode: '' }
  } catch (err: any) {
    error.value = toUserMessage(err, 'settingFailed')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
/* 改密码弹窗（纸色）：自旧 AccountSettingsView 原样迁入 */
.acct-mask {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 24px;
  padding: max(24px, env(safe-area-inset-top)) max(24px, env(safe-area-inset-right)) max(24px, env(safe-area-inset-bottom)) max(24px, env(safe-area-inset-left));
  overflow-y: auto;
  background: var(--scrim);
  z-index: 100;
}
.acct-modal {
  width: 100%;
  max-width: 340px;
  margin: auto;
  max-height: calc(100% - 48px);
  max-height: calc(100dvh - 48px);
  overflow-y: auto;
  padding: 20px;
  /* 面板实色（2026-11-18 用户亲令；2026-11-15 曾玻璃化）：底用
     实色面，令牌 styles/modal-glass.css（--modal-glass-bg 现引
     实色 surface）；本页是 v4 路由、data-theme 恒 light，取浅色
     实色档。去 backdrop 与内高光；圆角 14/尺寸不动，卡内纸色
     表单元素照旧。 */
  background: var(--modal-glass-bg);
  border: 1px solid var(--modal-glass-edge);
  border-radius: 14px;
  box-shadow: var(--modal-glass-shadow);
}
.acct-modal-title {
  margin: 0 0 14px;
  font-size: 17px;
  font-weight: 600;
  color: var(--ink);
}
.acct-field { margin-bottom: 12px; }
.acct-label {
  display: block;
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--ink2);
}
.acct-field input {
  width: 100%;
  height: 44px;
  padding: 0 12px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--paper-r-sm, 8px);
  color: var(--ink);
  font-size: 16px;
  outline: none;
  box-sizing: border-box;
}
.acct-field input:focus { border-color: var(--ink); }
.acct-field input:focus-visible { outline: 2px solid var(--ink); outline-offset: 1px; }
/* G-53 验证码行：输入框＋发送钮同排，几何沿用本弹窗既有 44px 控件高 */
.acct-code-row { display: flex; gap: 10px; }
.acct-code-row input { flex: 1; min-width: 0; }
.acct-code-btn { flex-shrink: 0; white-space: nowrap; }
.acct-hint { margin: 6px 0 0; font-size: 12px; color: var(--ink2); }
.acct-error {
  margin: 0 0 10px;
  padding: 8px 12px;
  background: var(--au-danger-soft);
  border-radius: var(--paper-r-sm, 8px);
  color: var(--red);
  font-size: 13px;
}
.acct-modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}
.acct-btn {
  height: 44px;
  padding: 0 16px;
  border-radius: var(--paper-r-md, 10px);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}
.acct-btn.ghost {
  background: var(--paper2);
  border: 1px solid var(--line);
  color: var(--ink2);
}
/* 主钮 UHD 口径（2026-11-13 补齐七彩＋玻璃）：彩虹边框 + 淡彩粉彩底 + 玻璃栈。
   本面板只挂账号设置页（.v4-scope 恒浅色纸面），只需浅色配方 */
.acct-btn.primary {
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
.acct-btn.primary:disabled {
  background: var(--paper2);
  color: var(--ink2);
  box-shadow: inset 0 0 0 1px var(--line);
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
.acct-btn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
