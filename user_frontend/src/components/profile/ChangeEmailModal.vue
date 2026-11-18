<template>
  <div v-if="open" class="acct-mask" @click.self="close" @keydown="onKeydown">
    <div class="acct-modal" role="dialog" aria-modal="true" aria-label="更换恢复邮箱">
      <h3 class="acct-modal-title">{{ hasCurrentEmail ? '更换恢复邮箱' : '添加恢复邮箱' }}</h3>
      <div class="acct-field">
        <label class="acct-label" for="em-pwd">当前密码</label>
        <input id="em-pwd" ref="pwdInput" v-model="form.currentPassword" type="password" autocomplete="current-password" />
      </div>
      <div class="acct-field">
        <label class="acct-label" for="em-new">新邮箱</label>
        <input id="em-new" v-model="form.newEmail" type="email" autocomplete="email" placeholder="以后找回密码就靠它" />
      </div>
      <div class="acct-field">
        <label class="acct-label" for="em-code">验证码</label>
        <div class="acct-code-row">
          <input id="em-code" v-model="form.code" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="6 位验证码" />
          <button type="button" class="acct-btn ghost acct-code-btn" :disabled="codeSending || countdown > 0" @click="sendCode">
            {{ countdown > 0 ? `${countdown} 秒后重发` : '发送验证码' }}
          </button>
        </div>
        <p class="acct-hint">验证码会发到新邮箱，10 分钟内有效；换好后旧邮箱会收到一封提醒。</p>
      </div>
      <p v-if="error" class="acct-error" role="alert">{{ error }}</p>
      <div class="acct-modal-actions">
        <button type="button" class="acct-btn ghost" :disabled="loading" @click="close">取消</button>
        <button type="button" class="acct-btn primary" :disabled="loading" @click="submit">
          {{ loading ? '提交中' : '确认更换' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 更换恢复邮箱弹窗（G-54，2026-11-18 定案）：验当前密码 → 向新邮箱发
 * 6 位验证码 → 输入确认即更换；后端同时向旧邮箱发变更通知。成功后
 * updateUser 同步本地邮箱，设置页恢复邮箱行随之刷新。形态与焦点管理
 * 沿用 ChangePasswordModal 同一套（全仓改密/换邮箱弹窗口径一致）。
 */
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { authApi } from '@/api'
import { useToast } from '@/composables/useToast'
import { useUserStore } from '@/stores/user'
import { toUserMessage } from '@/utils/toUserMessage'
import '../../styles/modal-glass.css'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()

const toast = useToast()
const userStore = useUserStore()
const form = ref({ currentPassword: '', newEmail: '', code: '' })
const loading = ref(false)
const error = ref('')
const pwdInput = ref<HTMLInputElement | null>(null)
const lastTrigger = ref<HTMLElement | null>(null)

const hasCurrentEmail = computed(() => !!(userStore.user?.email || '').trim())
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
  nextTick(() => pwdInput.value?.focus())
}
function close() {
  if (loading.value) return
  emit('update:open', false)
}
function teardown() {
  document.body.style.overflow = ''
  form.value = { currentPassword: '', newEmail: '', code: '' }
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

async function sendCode() {
  const { currentPassword, newEmail } = form.value
  if (!currentPassword) {
    error.value = '先填当前密码'
    return
  }
  if (!newEmail.trim() || !newEmail.includes('@')) {
    error.value = '新邮箱格式不正确'
    return
  }
  if (codeSending.value || countdown.value > 0) return
  codeSending.value = true
  error.value = ''
  try {
    await authApi.sendChangeEmailCode({ current_password: currentPassword, new_email: newEmail.trim() })
    toast.success('验证码已发送，去新邮箱里看一下')
    startCountdown()
  } catch (err: any) {
    error.value = toUserMessage(err, 'settingFailed')
  } finally {
    codeSending.value = false
  }
}

async function submit() {
  const { currentPassword, newEmail, code } = form.value
  if (!currentPassword || !newEmail.trim() || !code.trim()) {
    error.value = '请填写完整'
    return
  }
  if (!newEmail.includes('@')) {
    error.value = '新邮箱格式不正确'
    return
  }
  error.value = ''
  loading.value = true
  try {
    const res = await authApi.changeEmail({
      current_password: currentPassword,
      new_email: newEmail.trim(),
      code: code.trim(),
    })
    userStore.updateUser({ email: res.email })
    toast.success('恢复邮箱已更换')
    emit('update:open', false)
    form.value = { currentPassword: '', newEmail: '', code: '' }
  } catch (err: any) {
    error.value = toUserMessage(err, 'settingFailed')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
/* 换邮箱弹窗：与 ChangePasswordModal 同一套纸色玻璃弹窗样式（口径一致） */
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
  /* 面板实色（2026-11-18 用户亲令）：底用实色面，令牌
     styles/modal-glass.css（--modal-glass-bg 现引实色 surface）；
     去 backdrop 与内高光，圆角/尺寸不动。 */
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
.acct-btn.primary:disabled {
  background: var(--paper2);
  color: var(--ink2);
  box-shadow: inset 0 0 0 1px var(--line);
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
.acct-btn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
