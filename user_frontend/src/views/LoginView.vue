<script setup lang="ts">
/**
 * 登录 / 注册 — 全页认证视图
 *
 * 统一后端 JWT 认证：
 * - POST /api/user/auth/login     登录（access + refresh token）
 * - POST /api/user/auth/register  注册（自动生成自建 Emby 凭据）
 *
 * 登录成功后自动跳转 ?redirect= 指定的受保护页面。
 */
import { ref, reactive, computed, onMounted } from 'vue'
import { RouterLink, useRouter, useRoute } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useToast } from '@/composables/useToast'
import { toUserMessage } from '@/utils/toUserMessage'
import { toastFallback } from '@/constants/toastMessages'
import { User, Lock, Mail, Eye, EyeOff, Clapperboard, Gift, Send, KeyRound, BadgeCheck, Menu, Sun, Moon, Fingerprint, ShieldCheck } from 'lucide-vue-next'
import { useTheme } from '@/composables/useTheme'
// 人机验证挂件（能力：人机验证）：管理员未开启时该组件自己什么都不渲染
import CaptchaChallenge from '@/components/ui/CaptchaChallenge.vue'
// 站名来自「站点与品牌」能力（未配置时用默认值）
import { branding } from '@/composables/useBranding'
import { getRegisterConfig, verifyRegisterCode, requestPasswordReset, confirmPasswordReset } from '@/api/user'
import { loginSecurityApi, type LoginFactors } from '@/api/security'
import { passkeySupported } from '@/utils/webauthn'
import { tgApi } from '@/api/tg'
import { fetchTgBotUsername } from '@/api/site'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const toast = useToast()

// 页头玻璃胶囊（2026-11-13 1:1 返工）：外观切换＋营销菜单收进一颗胶囊。
// 外观走现行 useTheme 机制（本页视觉跟随用户外观，见文件下方注释），
// 菜单内容＝现有营销导航（首页 /welcome、定价 /pricing），行为照现有。
const { resolved, setPreference } = useTheme()
const menuOpen = ref(false)
function toggleTheme() {
  setPreference(resolved.value === 'dark' ? 'light' : 'dark')
}

// 注册页开关（管理端可配）：注册模式 + 邀请码开关
// registration_mode: open=开放注册 / code=凭码注册（两步） / closed=关闭注册
const registrationMode = ref<'open' | 'code' | 'closed'>('open')
const invitationEnabled = ref(true)
const registrationClosed = computed(() => registrationMode.value === 'closed')

// 凭码注册两步流程（2026-11-10 定版）：第一步只有注册码输入框，
// 验码通过才出第二步账号表单；两步不与账号表单同屏。
const registrationCodeRequired = ref(false)
const regCodeHint = ref({ text: '', url: '' })
const registerStep = ref<1 | 2>(1)
const regCodeInput = ref('')
const verifiedRegCode = ref('')
const verifyLoading = ref(false)
const verifyError = ref('')
const verifyBenefit = ref('')
 // 注册配置拉取失败标记：失败时注册区显示错误态+重试，不让用户填完表才被拒
 const configError = ref(false)

const mode = ref<'login' | 'register'>('login')

const loginForm = reactive({ username: '', password: '', totpCode: '' })

// 登录因素探测（账号安全三件套）：用户名失焦时问后端该账号启用了什么——
// 动态验证码字段仅在已启用时出现、Passkey 按钮仅在已绑定时出现（照 UHDNow 口径）。
// 探测失败静默（字段不出现，用户输完密码提交后后端会以明确文案提示补码）。
const loginFactors = ref<LoginFactors>({ totp_required: false, passkey_available: false })
const passkeyLoginLoading = ref(false)
const passkeyUsable = computed(() => loginFactors.value.passkey_available && passkeySupported())

async function probeLoginFactors() {
  const username = loginForm.username.trim()
  if (!username) {
    loginFactors.value = { totp_required: false, passkey_available: false }
    return
  }
  try {
    loginFactors.value = await loginSecurityApi.factors(username)
  } catch {
    /* 静默：不因探测失败打扰登录 */
  }
}

async function handlePasskeyLogin() {
  if (passkeyLoginLoading.value) return
  const username = loginForm.username.trim()
  if (!username) {
    error.value = '请先输入用户名，再使用 Passkey 登录'
    return
  }
  error.value = ''
  passkeyLoginLoading.value = true
  try {
    await userStore.loginWithPasskey(username)
    toast.success('登录成功')
    router.push(safeRedirect(route.query.redirect))
  } catch (err: any) {
    error.value = err?.name === 'NotAllowedError'
      ? '已取消 Passkey 登录，或设备验证超时'
      : friendlyError(err, 'Passkey 登录失败，请稍后重试')
  } finally {
    passkeyLoginLoading.value = false
  }
}
// 邀请码：此前只有从带参链接进来（?invite=）才拿得到，表单里根本没有输入框，
// 现在能手填，链接进来自动预填。
const registerForm = reactive({
  username: '', password: '', confirmPassword: '', email: '',
  inviteCode: '',
})

const showLoginPassword = ref(false)
const showRegisterPassword = ref(false)
const loginLoading = ref(false)
const registerLoading = ref(false)
const error = ref('')

// 人机验证令牌：由挂件回调写入；未开启保护时一直是空串（后端也不校验）
const captchaToken = ref('')
const captchaRef = ref<InstanceType<typeof CaptchaChallenge> | null>(null)

// 找回密码（后端 #94 已上线，本单补前端入口）：
// - 登录表单内「忘记密码？」展开小表单，填用户名或邮箱申请重置邮件；
//   后端防枚举统一响应，前端原样展示后端 message，不做区分账号是否存在的提示。
// - 邮件链接落点 /login?reset=<token>：登录页叠加新密码表单，走 confirm 端点。
const forgotOpen = ref(false)
const forgotIdentifier = ref('')
const forgotLoading = ref(false)
const forgotMessage = ref('')
const forgotError = ref('')
const resetToken = ref('')
const resetForm = reactive({ password: '', confirmPassword: '' })
const showResetPassword = ref(false)
const resetLoading = ref(false)
const resetError = ref('')

// 强度文案映射 ['弱','弱','中','强'][score]：score=0（1–5 位密码）时旧写法 ['弱','中','强'][score-1] 会取到 undefined 显示空文案
const passwordStrength = computed(() => {
  const pwd = registerForm.password
  if (!pwd) return 0
  let score = 0
  if (pwd.length >= 6) score++
  if (pwd.length >= 10) score++
  if (/[a-z]/.test(pwd) && /[A-Z]/.test(pwd)) score++
  if (/\d/.test(pwd)) score++
  if (/[^a-zA-Z0-9]/.test(pwd)) score++
  return Math.min(score, 3)
})

function toggleMode(next: 'login' | 'register') {
  mode.value = next
  error.value = ''
  // 人机验证令牌显式清空：即使挂件会随表单重建，也防止时序上复用旧令牌
  captchaToken.value = ''
}

function onTabKey(e: KeyboardEvent, current: 'login' | 'register') {
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
  e.preventDefault()
  const next = current === 'login' ? 'register' : 'login'
  toggleMode(next)
  document.getElementById(next === 'login' ? 'tab-login' : 'tab-register')?.focus()
}

function friendlyError(err: any, fallback: string): string {
  // 认证表单（登录/注册）中 401/403 表示的是凭据错误（如密码错误），
  // 而不是已登录后接口调用时的“会话过期”。因此这里必须先于 toUserMessage 的
  // 全局“状态码优先”规则单独处理，避免把“密码错了”误报成“登录已过期”。
  const status = err?.response?.status
  const detail = err?.response?.data?.detail
  if ((status === 401 || status === 403) && typeof detail === 'string' && detail.trim() !== '') {
    const trimmed = detail.trim()
    const isMachineCode = /^[A-Z][A-Z0-9_]{2,}$/.test(trimmed)
    const hasLeakKeyword = /数据库|sql|连接失败|超时|异常|堆栈|traceback/i.test(trimmed)
    const isServerErrorLeak = /internal server error|服务器内部错误/i.test(trimmed)
    if (!isMachineCode && !hasLeakKeyword && !isServerErrorLeak) {
      return trimmed
    }
  }

  // 统一走 toUserMessage 解析；只有落到通用兜底时才用调用方的场景文案
  const msg = toUserMessage(err, 'saveFailed')
  return msg === toastFallback.saveFailed ? fallback : msg
}

/** query 参数取首个字符串值（多值 query 取第一个，防止数组被直接提交） */
function firstQueryValue(v: unknown): string {
  if (Array.isArray(v)) return String(v[0] ?? '')
  return String(v ?? '')
}

/** redirect 白名单：只允许站内路径，防止开放重定向 */
function safeRedirect(v: unknown): string {
  const s = firstQueryValue(v)
  if (s.startsWith('/') && !s.startsWith('//') && !s.includes('://')) return s
  return '/'
}

async function handleLogin() {
  // 回车双提交防护：表单 @submit 与输入框回车各触发一次，此处用 loading 状态做守卫兜底
  if (loginLoading.value) return
  if (!loginForm.username || !loginForm.password) {
    error.value = '请输入用户名和密码'
    return
  }
  error.value = ''
  loginLoading.value = true
  try {
    await userStore.login(
      loginForm.username.trim(), loginForm.password, captchaToken.value,
      loginFactors.value.totp_required ? loginForm.totpCode.trim() : undefined,
    )
    toast.success('登录成功')
    router.push(safeRedirect(route.query.redirect))
  } catch (err: any) {
    error.value = friendlyError(err, '登录失败，请稍后重试')
    // 没来得及失焦探测就提交、后端说要码时，把验证码字段补出来让他重试
    if (String(err?.response?.data?.detail || '').includes('动态验证码')) {
      loginFactors.value.totp_required = true
    }
    // 令牌是一次性的：失败后换一个新的，否则第二次点击会因「已使用」而失败
    captchaRef.value?.reset()
  } finally {
    loginLoading.value = false
  }
}

/** 找回密码第一步：申请重置邮件（防枚举，前端只展示后端回的统一文案） */
async function handleForgot() {
  if (forgotLoading.value) return
  const identifier = forgotIdentifier.value.trim()
  if (!identifier) {
    forgotError.value = '请输入用户名或邮箱'
    return
  }
  forgotError.value = ''
  forgotMessage.value = ''
  forgotLoading.value = true
  try {
    // 人机验证与登录共用同一道闸（后端 action=login），复用当前令牌
    const res = await requestPasswordReset(identifier, captchaToken.value)
    forgotMessage.value = res.message
  } catch (err) {
    forgotError.value = friendlyError(err, '申请失败，请稍后重试')
    // 令牌是一次性的：失败后换一个新的（同 handleLogin 口径）
    captchaRef.value?.reset()
  } finally {
    forgotLoading.value = false
  }
}

/** 找回密码第二步：凭邮件链接令牌设置新密码，成功后清掉 URL 中的令牌回登录 */
async function handleReset() {
  if (resetLoading.value) return
  if (resetForm.password.length < 6 || resetForm.password.length > 64) {
    resetError.value = '新密码需 6-64 位'
    return
  }
  if (resetForm.password !== resetForm.confirmPassword) {
    resetError.value = '两次输入的密码不一致'
    return
  }
  resetError.value = ''
  resetLoading.value = true
  try {
    const res = await confirmPasswordReset(resetToken.value, resetForm.password)
    toast.success(res.message || '密码已重置，请使用新密码登录')
    resetToken.value = ''
    resetForm.password = ''
    resetForm.confirmPassword = ''
    router.replace({ path: '/login' })
  } catch (err) {
    resetError.value = friendlyError(err, '重置失败，请重新申请')
  } finally {
    resetLoading.value = false
  }
}

/** 放弃重置、返回登录（同时清掉 URL 中的令牌） */
function cancelReset() {
  resetToken.value = ''
  resetError.value = ''
  router.replace({ path: '/login' })
}

/** 两步注册第一步：验注册码（只校验不消耗）；通过才进入账号表单 */
async function handleVerifyRegCode() {
  if (verifyLoading.value) return
  const code = regCodeInput.value.trim()
  if (!code) {
    verifyError.value = '请输入注册码'
    return
  }
  verifyError.value = ''
  verifyLoading.value = true
  try {
    const res = await verifyRegisterCode(code)
    if (!res.valid) {
      verifyError.value = res.message || '注册码无效'
      return
    }
    verifiedRegCode.value = code
    verifyBenefit.value = res.realm_name
      ? `将开通「${res.realm_name}」${res.days_text || ''}`
      : ''
    registerStep.value = 2
    error.value = ''
  } catch (err) {
    verifyError.value = friendlyError(err, '验码失败，请稍后重试')
  } finally {
    verifyLoading.value = false
  }
}

/** 第二步返回第一步换一张码（已验证的码作废重验，尚未消耗） */
function backToCodeStep() {
  registerStep.value = 1
  verifiedRegCode.value = ''
  verifyBenefit.value = ''
  error.value = ''
}

async function handleRegister() {
  // 回车双提交防护（同 handleLogin）
  if (registerLoading.value) return
  if (registrationClosed.value) {
    error.value = '当前未开放注册'
    return
  }
  if (registrationCodeRequired.value && registerStep.value !== 2) {
    error.value = '请先输入注册码并验证'
    return
  }
  const f = registerForm
  if (!f.username || !f.password || !f.confirmPassword) {
    error.value = '请完整填写注册信息'
    return
  }
  if (f.username.length < 3 || f.username.length > 32 || !/^[a-zA-Z0-9_]+$/.test(f.username)) {
    error.value = '用户名需 3-32 位，仅限字母、数字、下划线'
    return
  }
  if (f.password.length < 6) {
    error.value = '密码至少 6 位'
    return
  }
  if (f.password !== f.confirmPassword) {
    error.value = '两次输入的密码不一致'
    return
  }
  error.value = ''
  registerLoading.value = true
  try {
    // 表单里填的优先；没填时沿用链接带来的（多值 query 取第一个）
    const inviteCode = f.inviteCode.trim() || firstQueryValue(route.query.invite)
    await userStore.register(
      f.username.trim(), f.password, f.email || undefined,
      inviteCode || undefined, captchaToken.value,
      registrationCodeRequired.value ? verifiedRegCode.value : undefined,
    )
    toast.success('注册成功，已自动开通观影账号')
    // 注册后 TG 绑定引导：仅注册流程触发，普通登录不动。
    // 状态接口失败时按默认放行进首页，绝不锁死用户。
    const redirect = safeRedirect(route.query.redirect)
    try {
      const st = await tgApi.status()
      if (st.required && !st.bound && st.guide_enabled) {
        router.push({ path: '/tg-bind', query: route.query.redirect ? { redirect: safeRedirect(route.query.redirect) } : {} })
        return
      }
    } catch {
      /* 拿不到绑定状态就按默认放行 */
    }
    router.push(redirect)
  } catch (err) {
    error.value = friendlyError(err, '注册失败，请稍后重试')
    captchaRef.value?.reset()
  } finally {
    registerLoading.value = false
  }
}

// 主题机制不变：本页不自己动 data-theme，路由守卫 afterEach 已按 localStorage
// 'aetrix-theme' 恢复用户偏好（作用于其余页面）。
// 视觉口径（2026-11-13 1:1 返工，覆盖批 1 的固定浅色）：本页跟随用户外观——
// 样式走组件内 --a-* 变量，html[data-theme='light'] 时为浅色面（原站截图口径），
// 缺省深色面用同色系低透明粉彩光晕；认证逻辑与主题机制一字未动。

// 注：旧皮的海报拼贴背景（PosterCollage + fetchLoginPosters 延迟拉取）已随
// UHD 换皮批 1 固定浅色卡式退场，相关状态与拉取一并移除；那是纯装饰背景，
// 不属于登录/注册业务逻辑，后端接口本身未动。

// TG 登录入口：站点机器人用户名（公开接口下发）；为空=未配置机器人，不渲染入口
const tgBotUsername = ref('')

function loadRegisterConfig() {
  configError.value = false
  // 拉取注册页开关（公开接口）：按管理端配置决定注册形态——
  // 开放=直接出账号表单 / 凭码=先验码再出表单 / 关闭=不开放注册
  getRegisterConfig().then((cfg) => {
    registrationMode.value = cfg.registration_mode
    registrationCodeRequired.value = cfg.registration_code_required
    regCodeHint.value = cfg.registration_code_hint
    invitationEnabled.value = cfg.invitation_enabled
    // 注册已关闭时忽略 ?mode=register / ?invite= 参数，强制留在登录页
    if (registrationMode.value === 'closed') mode.value = 'login'
  }).catch(() => { configError.value = true /* 拉取失败时注册区显示错误态+重试，不锁死 */ })
}

onMounted(() => {
  // 登录页不再强制深色：跟随用户偏好（见文件顶部注释）。

  loadRegisterConfig()
  // TG 登录入口与注册配置并行拉取，失败保持空串（不渲染入口、不阻塞页面）
  fetchTgBotUsername().then(name => { tgBotUsername.value = name }).catch(() => { tgBotUsername.value = '' })
  // 找回密码邮件链接落点 ?reset=<token>：叠加新密码表单（优先于 mode/invite 处理）
  const resetParam = firstQueryValue(route.query.reset)
  if (resetParam) {
    resetToken.value = resetParam
    mode.value = 'login'
    return
  }
  if (route.query.mode === 'register') mode.value = 'register'
  // 邀请链接 ?invite=CODE（多值 query 取第一个）：自动切到注册页、预填邀请码并提示
  if (route.query.invite) {
    mode.value = 'register'
    registerForm.inviteCode = firstQueryValue(route.query.invite)
    toast.info(`已收到好友邀请码，注册成功后双方都可获得能量奖励`, 5000)
  }
})
</script>

<template>
  <div class="auth-page">
    <!-- UHD 换皮批 1：固定浅色卡式——粉彩光斑底 + 居中白卡；
         原海报拼贴背景与网格层随旧皮退场（海报数据拉取逻辑保留在脚本中，未渲染） -->
    <!-- 页头行（原站形态）：左品牌字标，右外观＋菜单一颗玻璃胶囊 -->
    <header class="auth-top">
      <div class="auth-top__brand">
        <img v-if="branding.logo_url" class="auth-top__logo" :src="branding.logo_url" :alt="branding.site_name" />
        <Clapperboard v-else :size="25" aria-hidden="true" />
        <span class="auth-top__word">
          <span class="auth-top__name">{{ branding.site_name }}</span>
          <span class="auth-top__sub">SERVICE</span>
        </span>
      </div>
      <div class="auth-top__glass">
        <button
          type="button"
          class="auth-top__iconbtn"
          :aria-label="resolved === 'dark' ? '切换到浅色外观' : '切换到深色外观'"
          @click="toggleTheme"
        >
          <Sun v-if="resolved === 'dark'" :size="18" aria-hidden="true" />
          <Moon v-else :size="18" aria-hidden="true" />
        </button>
        <span class="auth-top__divider" aria-hidden="true"></span>
        <button
          type="button"
          class="auth-top__iconbtn"
          aria-label="菜单"
          :aria-expanded="menuOpen"
          @click="menuOpen = !menuOpen"
        >
          <Menu :size="18" aria-hidden="true" />
        </button>
        <div v-if="menuOpen" class="auth-menu">
          <RouterLink to="/welcome" @click="menuOpen = false">首页</RouterLink>
          <RouterLink to="/pricing" @click="menuOpen = false">定价</RouterLink>
        </div>
      </div>
    </header>

    <div class="auth-main">
    <div class="auth-column">
      <h1 class="auth-title">{{ resetToken ? '重置密码' : mode === 'login' ? '登录' : '注册' }} {{ branding.site_name }}</h1>

      <!-- 模式切换（重置密码叠加态不显示） -->
      <div v-if="!resetToken" class="mode-switch" role="tablist" aria-label="登录或注册">
        <button
          id="tab-login"
          class="mode-btn"
          :class="{ active: mode === 'login' }"
          role="tab"
          :aria-selected="mode === 'login'"
          aria-controls="panel-login"
          @click="toggleMode('login')"
          @keydown="onTabKey($event, 'login')"
        >
          <span class="mode-btn-label">登录</span>
        </button>
        <button
          id="tab-register"
          class="mode-btn"
          :class="{ active: mode === 'register' }"
          role="tab"
          :aria-selected="mode === 'register'"
          aria-controls="panel-register"
          @click="toggleMode('register')"
          @keydown="onTabKey($event, 'register')"
        >
          <span class="mode-btn-label">注册</span>
        </button>
        <div class="mode-indicator" :class="{ right: mode === 'register' }" aria-hidden="true"></div>
      </div>

      <!-- 找回密码第二步：邮件链接 ?reset=<token> 落点的新密码表单（叠加在登录页上） -->
      <form v-if="resetToken" class="auth-form" @submit.prevent="handleReset">
        <p class="reset-note">你正在通过邮件链接重置密码，链接 30 分钟内有效。设置新密码后，用新密码登录。</p>

        <label class="field">
          <span class="field-label">新密码</span>
          <div class="field-box">
            <Lock :size="16" class="field-icon" aria-hidden="true" />
            <input
              v-model="resetForm.password"
              :type="showResetPassword ? 'text' : 'password'"
              name="new-password"
              autocomplete="new-password"
              placeholder="6-64 位"
              :aria-invalid="!!resetError"
              aria-describedby="reset-form-error"
            />
            <button type="button" class="eye-btn" :aria-label="showResetPassword ? '隐藏密码' : '显示密码'" :aria-pressed="showResetPassword" @click="showResetPassword = !showResetPassword">
              <EyeOff v-if="showResetPassword" :size="15" aria-hidden="true" />
              <Eye v-else :size="15" aria-hidden="true" />
            </button>
          </div>
        </label>

        <label class="field">
          <span class="field-label">确认新密码</span>
          <div class="field-box">
            <Lock :size="16" class="field-icon" aria-hidden="true" />
            <input
              v-model="resetForm.confirmPassword"
              type="password"
              name="confirm-new-password"
              autocomplete="new-password"
              placeholder="再次输入新密码"
              :aria-invalid="!!resetError"
              aria-describedby="reset-form-error"
            />
          </div>
        </label>

        <p v-if="resetError" id="reset-form-error" class="form-error" role="alert">{{ resetError }}</p>

        <button type="submit" class="submit-btn" :disabled="resetLoading || !resetForm.password || !resetForm.confirmPassword">
          {{ resetLoading ? '提交中…' : '设置新密码' }}
        </button>
        <button type="button" class="forgot-link forgot-link--center" @click="cancelReset">返回登录</button>
      </form>

      <!-- 登录表单 -->
      <form v-if="!resetToken && mode === 'login'" id="panel-login" class="auth-form" role="tabpanel" aria-labelledby="tab-login" tabindex="0" @submit.prevent="handleLogin">
        <label class="field">
          <span class="field-label">用户名</span>
          <div class="field-box">
            <User :size="16" class="field-icon" aria-hidden="true" />
            <input
              v-model="loginForm.username"
              type="text"
              name="username"
              autocomplete="username"
              placeholder="输入用户名"
              :aria-invalid="!!error"
              aria-describedby="login-form-error"
              @blur="probeLoginFactors"
            />
          </div>
        </label>

        <label class="field">
          <span class="field-label">密码</span>
          <div class="field-box">
            <Lock :size="16" class="field-icon" aria-hidden="true" />
            <input
              v-model="loginForm.password"
              :type="showLoginPassword ? 'text' : 'password'"
              name="password"
              autocomplete="current-password"
              placeholder="输入密码"
              :aria-invalid="!!error"
              aria-describedby="login-form-error"
            />
            <button type="button" class="eye-btn" :aria-label="showLoginPassword ? '隐藏密码' : '显示密码'" :aria-pressed="showLoginPassword" @click="showLoginPassword = !showLoginPassword">
              <EyeOff v-if="showLoginPassword" :size="15" aria-hidden="true" />
              <Eye v-else :size="15" aria-hidden="true" />
            </button>
          </div>
        </label>

        <!-- 动态验证码：仅在该账号已启用时出现（失焦探测或提交被后端要求时补出） -->
        <label v-if="loginFactors.totp_required" class="field">
          <span class="field-label">动态验证码</span>
          <div class="field-box">
            <ShieldCheck :size="16" class="field-icon" aria-hidden="true" />
            <input
              v-model="loginForm.totpCode"
              type="text"
              name="totp-code"
              inputmode="numeric"
              autocomplete="one-time-code"
              maxlength="6"
              placeholder="验证器 App 里的 6 位码"
              :aria-invalid="!!error"
              aria-describedby="login-form-error"
            />
          </div>
        </label>

        <!-- 找回密码入口：展开小表单，填用户名或邮箱申请重置邮件 -->
        <div class="forgot-row">
          <button type="button" class="forgot-link" :aria-expanded="forgotOpen" @click="forgotOpen = !forgotOpen">忘记密码？</button>
        </div>
        <div v-if="forgotOpen" class="forgot-box">
          <label class="field">
            <span class="field-label">用户名或邮箱</span>
            <div class="field-box">
              <Mail :size="16" class="field-icon" aria-hidden="true" />
              <input
                v-model="forgotIdentifier"
                type="text"
                name="reset-identifier"
                autocomplete="username"
                placeholder="输入用户名或绑定的邮箱"
                :aria-invalid="!!forgotError"
                aria-describedby="forgot-form-error"
                @keydown.enter.prevent="handleForgot"
              />
            </div>
          </label>
          <p v-if="forgotMessage" class="forgot-ok" role="status">{{ forgotMessage }}</p>
          <p v-if="forgotError" id="forgot-form-error" class="form-error" role="alert">{{ forgotError }}</p>
          <button type="button" class="submit-btn" :disabled="forgotLoading || !forgotIdentifier.trim()" @click="handleForgot">
            {{ forgotLoading ? '提交中…' : '发送重置邮件' }}
          </button>
          <p class="forgot-hint">重置链接会发到账号绑定的邮箱，30 分钟内有效。</p>
        </div>

        <p v-if="error" id="login-form-error" class="form-error" role="alert">{{ error }}</p>

        <CaptchaChallenge ref="captchaRef" action="login" @update:token="captchaToken = $event" />

        <button type="submit" class="submit-btn" :disabled="loginLoading || !loginForm.username.trim() || !loginForm.password">
          {{ loginLoading ? '登录中…' : '登 录' }}
        </button>

        <!-- Passkey 登录：仅在该账号已绑定且浏览器支持时出现 -->
        <div v-if="passkeyUsable" class="tg-login">
          <div class="tg-login-divider" aria-hidden="true"><span>或</span></div>
          <button type="button" class="tg-login-btn" :disabled="passkeyLoginLoading" @click="handlePasskeyLogin">
            <Fingerprint :size="16" aria-hidden="true" /> {{ passkeyLoginLoading ? '等待设备验证…' : '使用 Passkey 登录' }}
          </button>
          <p class="tg-login-hint">用设备指纹 / 面容或安全密钥免密登录</p>
        </div>

        <!-- TG 一键登录入口：跳到站点机器人私聊，菜单首项即免密登录（后端未配置机器人时不渲染） -->
        <div v-if="tgBotUsername" class="tg-login">
          <div class="tg-login-divider" aria-hidden="true"><span>或</span></div>
          <a class="tg-login-btn" :href="`https://t.me/${tgBotUsername}`" target="_blank" rel="noopener noreferrer">
            <Send :size="16" aria-hidden="true" /> 用 Telegram 登录
          </a>
          <p class="tg-login-hint">在机器人私聊里点「一键免密进入控制面板」即可免密登录（需先绑定过 Telegram）</p>
        </div>
      </form>

      <!-- 凭码注册第一步：只有注册码输入框，不与账号表单同屏（2026-11-10 定版两步流程） -->
      <form v-else-if="!resetToken && registrationCodeRequired && registerStep === 1" id="panel-register" class="auth-form" role="tabpanel" aria-labelledby="tab-register" tabindex="0" @submit.prevent="handleVerifyRegCode">
        <label class="field">
          <span class="field-label">注册码</span>
          <div class="field-box">
            <KeyRound :size="16" class="field-icon" aria-hidden="true" />
            <input
              v-model="regCodeInput"
              type="text"
              name="registration-code"
              autocomplete="off"
              placeholder="输入注册码，验证通过后填写账号信息"
              :aria-invalid="!!verifyError"
              aria-describedby="regcode-verify-error"
            />
          </div>
        </label>

        <p class="regcode-hint">
          {{ regCodeHint.text }}
          <a v-if="regCodeHint.url" class="hint-link" :href="regCodeHint.url" target="_blank" rel="noopener">去获取</a>
        </p>
        <p v-if="configError" class="closed-notice">注册配置拉取失败，<button type="button" class="retry-link" @click="loadRegisterConfig">点重试</button></p>
        <p v-if="verifyError" id="regcode-verify-error" class="form-error" role="alert">{{ verifyError }}</p>

        <button type="submit" class="submit-btn" :disabled="verifyLoading || !regCodeInput.trim()">
          {{ verifyLoading ? '验证中…' : '验证注册码' }}
        </button>
      </form>

      <!-- 注册表单（凭码模式下是第二步：验码通过才渲染） -->
      <form v-else-if="!resetToken" id="panel-register" class="auth-form" role="tabpanel" aria-labelledby="tab-register" tabindex="0" @submit.prevent="handleRegister">
        <p v-if="registrationCodeRequired" class="verified-code">
          <BadgeCheck :size="15" aria-hidden="true" />
          <span>注册码 {{ verifiedRegCode }} 已验证<template v-if="verifyBenefit"> · {{ verifyBenefit }}</template></span>
          <button type="button" class="retry-link" @click="backToCodeStep">更换</button>
        </p>

        <label class="field">
          <span class="field-label">用户名</span>
          <div class="field-box">
            <User :size="16" class="field-icon" aria-hidden="true" />
            <input
              v-model="registerForm.username"
              type="text"
              name="username"
              autocomplete="username"
              placeholder="3-32 位字母、数字、下划线"
              :aria-invalid="!!error"
              aria-describedby="register-form-error"
            />
          </div>
        </label>

        <label class="field">
          <span class="field-label">密码</span>
          <div class="field-box">
            <Lock :size="16" class="field-icon" aria-hidden="true" />
            <input
              v-model="registerForm.password"
              :type="showRegisterPassword ? 'text' : 'password'"
              name="new-password"
              autocomplete="new-password"
              placeholder="至少 6 位"
              :aria-invalid="!!error"
              aria-describedby="register-form-error"
            />
            <button type="button" class="eye-btn" :aria-label="showRegisterPassword ? '隐藏密码' : '显示密码'" :aria-pressed="showRegisterPassword" @click="showRegisterPassword = !showRegisterPassword">
              <EyeOff v-if="showRegisterPassword" :size="15" aria-hidden="true" />
              <Eye v-else :size="15" aria-hidden="true" />
            </button>
          </div>
          <div v-if="registerForm.password" class="strength">
            <div class="strength-track">
              <div class="strength-fill" :class="`lv-${passwordStrength}`"></div>
            </div>
            <span class="strength-text" aria-live="polite">{{
              ['弱', '弱', '中', '强'][passwordStrength]
            }}</span>
          </div>
        </label>

        <label class="field">
          <span class="field-label">确认密码</span>
          <div class="field-box">
            <Lock :size="16" class="field-icon" aria-hidden="true" />
            <input
              v-model="registerForm.confirmPassword"
              type="password"
              name="confirm-password"
              autocomplete="new-password"
              placeholder="再次输入密码"
              :aria-invalid="!!error"
              aria-describedby="register-form-error"
            />
          </div>
        </label>

        <label class="field">
          <span class="field-label">邮箱 <em class="optional">选填</em></span>
          <div class="field-box">
            <Mail :size="16" class="field-icon" aria-hidden="true" />
            <input
              v-model="registerForm.email"
              type="email"
              name="email"
              autocomplete="email"
              placeholder="用于找回密码"
              :aria-invalid="!!error"
              aria-describedby="register-form-error"
            />
          </div>
        </label>

        <label v-if="invitationEnabled" class="field">
          <span class="field-label">邀请码 <em class="optional">选填，双方得能量</em></span>
          <div class="field-box">
            <Gift :size="16" class="field-icon" aria-hidden="true" />
            <input
              v-model="registerForm.inviteCode"
              type="text"
              name="invite-code"
              placeholder="好友的邀请码"
              :aria-invalid="!!error"
              aria-describedby="register-form-error"
            />
          </div>
        </label>

        <p v-if="registrationClosed" class="closed-notice">当前未开放注册</p>
        <p v-if="configError" class="closed-notice">注册配置拉取失败，<button type="button" class="retry-link" @click="loadRegisterConfig">点重试</button></p>
        <p v-if="error" id="register-form-error" class="form-error" role="alert">{{ error }}</p>

        <CaptchaChallenge ref="captchaRef" action="register" @update:token="captchaToken = $event" />

        <button type="submit" class="submit-btn" :disabled="registerLoading || registrationClosed || !registerForm.username.trim() || !registerForm.password || !registerForm.confirmPassword">
          {{ registerLoading ? '注册中…' : '注 册' }}
        </button>
      </form>

      <p class="auth-footnote">
        {{ mode === 'login' ? '密码加密存储' : '密码加密存储 · 注册成功自动开通观影账号' }}
        · <RouterLink class="auth-legal-link" to="/privacy">隐私政策</RouterLink>
      </p>
    </div>
    </div>
  </div>
</template>


<style scoped>
/* ============================================================
   UHD 换皮 · 登录/注册（2026-11-13 按原站 1:1 返工）
   页头行（品牌字标＋玻璃胶囊）＋居中卡：桌面圆角约 20（实测清单第 3 节；
   移动端未测到，保留圆角 30）；输入桌面高 46、圆角约 23（移动保留
   54px 全胶囊），标签 15px 在框上方；主按钮实测淡彩虹渐变填充＋
   1px 彩虹边框、高 46、圆角约 23、黑字（移动保留 53px）；
   TG 钮同尺寸白底描边（原站 Passkey 钮位）。
   页面跟随用户外观：--a-* 变量缺省深色，data-theme='light' 换浅；
   背景顶部粉彩光晕（深色低透明同色）。认证逻辑一字未动。
   ============================================================ */
.auth-page {
  box-sizing: border-box;
  min-height: 100vh;
  min-height: 100svh;
  display: flex;
  flex-direction: column;
  padding: 0 24px 40px;
  position: relative;
  isolation: isolate;
  overflow: hidden;
  background:
    radial-gradient(600px 420px at 12% 4%, rgba(246, 205, 190, 0.13), transparent 70%),
    radial-gradient(560px 400px at 88% 10%, rgba(160, 195, 245, 0.13), transparent 70%),
    radial-gradient(640px 460px at 80% 96%, rgba(205, 190, 245, 0.11), transparent 70%),
    radial-gradient(520px 380px at 6% 92%, rgba(175, 230, 205, 0.1), transparent 70%),
    #070708;
  color: #ffffff;
  font-family: var(--uhd-font-sans);
}

/* 页头行 */
.auth-top {
  position: relative;
  z-index: 2;
  width: 100%;
  max-width: 1120px;
  margin: 0 auto;
  padding: 20px 0 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.auth-top__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #ffffff;
  min-width: 0;
}
.auth-top__logo {
  width: 28px;
  height: 28px;
  object-fit: contain;
}
.auth-top__word {
  display: flex;
  flex-direction: column;
  line-height: 1.1;
}
.auth-top__name {
  font-size: 17px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: 0.01em;
  white-space: nowrap;
}
.auth-top__sub {
  font-size: 9px;
  font-weight: var(--uhd-fw-semibold);
  letter-spacing: 0.32em;
  color: rgba(255, 255, 255, 0.4);
}
/* 玻璃胶囊（底坞配方缩小版：半透＋blur＋边缘光＋顶部高光＋柔影） */
.auth-top__glass {
  position: relative;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 5px 8px;
  border-radius: 999px;
  background: rgba(28, 28, 31, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.16);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.14),
    0 10px 28px rgba(0, 0, 0, 0.16);
}
.auth-top__iconbtn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: #ffffff;
  cursor: pointer;
}
.auth-top__iconbtn:hover {
  background: rgba(140, 140, 150, 0.16);
}
.auth-top__divider {
  width: 1px;
  height: 18px;
  background: rgba(255, 255, 255, 0.1);
}
.auth-menu {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  min-width: 148px;
  padding: 6px;
  border-radius: 16px;
  background: #141416;
  border: 1px solid rgba(255, 255, 255, 0.1);
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.5);
  display: flex;
  flex-direction: column;
}
.auth-menu a {
  padding: 10px 12px;
  border-radius: 10px;
  color: #ffffff;
  font-size: 15px;
  font-weight: var(--uhd-fw-medium);
  text-decoration: none;
}
.auth-menu a:hover {
  background: rgba(255, 255, 255, 0.08);
}

/* 卡片居中区 */
.auth-main {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  padding-top: 28px;
}
.auth-column {
  position: relative;
  z-index: 1;
  width: 100%;
  /* 实测：卡宽约 422、内边距约 33 → 内容宽 362（输入框/主钮与之同宽） */
  max-width: 428px;
  min-width: 0;
  box-sizing: border-box;
  padding: 36px 33px 32px;
  background: #141416;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 20px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.5);
  animation: authColumnIn var(--uhd-dur-blur-in) var(--uhd-ease-pop) both;
}
@keyframes authColumnIn {
  /* 入场只做淡入＋上浮，不对整卡做 blur：卡里全是文字与输入框，blur 动画期间
     每个字都被涂抹成重影（2026-11-13 用户录屏实证，手机上读作「文字双份」），
     落定才恢复。原站的 blur 揭示用在大标题展示字上，不整卡套用。 */
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: none; }
}
.auth-title {
  margin: 0 0 1.6rem;
  font-family: var(--uhd-font-sans);
  font-size: 30px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
  line-height: 1.2;
  color: #ffffff;
  overflow-wrap: anywhere;
}

/* 模式切换：分段切换（底槽＋填充胶囊激活项） */
.mode-switch {
  box-sizing: border-box;
  position: relative;
  display: grid;
  grid-template-columns: 1fr 1fr;
  background: rgba(255, 255, 255, 0.08);
  border: none;
  border-radius: 999px;
  padding: 4px;
  margin-bottom: 1.6rem;
}
.mode-btn {
  position: relative;
  z-index: 1;
  min-width: 0;
  min-height: 38px;
  padding: 0 0.75rem;
  border: none;
  background: transparent;
  border-radius: var(--uhd-radius-pill);
  font: inherit;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  line-height: 1;
  color: rgba(255, 255, 255, 0.62);
  cursor: pointer;
  transition: color 0.2s ease;
  display: flex;
  align-items: center;
  justify-content: center;
}
.mode-btn-label {
  line-height: 1;
  transform: translateY(-0.05em);
}
.mode-btn.active {
  color: #ffffff;
  font-weight: var(--uhd-fw-semibold);
}
.mode-indicator {
  position: absolute;
  top: 4px;
  left: 4px;
  width: calc(50% - 4px);
  height: calc(100% - 8px);
  border-radius: var(--uhd-radius-pill);
  background: #141416;
  box-shadow: var(--uhd-shadow-seg);
  pointer-events: none;
  transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
.mode-indicator.right {
  transform: translateX(100%);
}

/* 表单与字段：标签 15px 在框上方，输入全胶囊 54px */
.auth-form {
  display: flex;
  flex-direction: column;
  gap: 1.05rem;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  min-width: 0;
}
.field-label {
  font-size: 15px;
  font-weight: var(--uhd-fw-medium);
  line-height: 1.5;
  color: #ffffff;
}
.optional {
  margin-left: 0.25rem;
  font-size: var(--uhd-fs-aux-sm);
  font-style: normal;
  font-weight: var(--uhd-fw-normal);
  color: rgba(255, 255, 255, 0.4);
}
.field-box {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  min-height: 46px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 23px;
  padding: 0 18px;
  transition: box-shadow 0.2s ease, border-color 0.2s ease;
}
.field-box:focus-within {
  border-color: var(--uhd-l-link);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.14);
}
.field-icon {
  color: rgba(255, 255, 255, 0.4);
  margin-right: 0.625rem;
  flex-shrink: 0;
  transition: color 0.2s ease;
}
.field-box:focus-within .field-icon {
  color: rgba(255, 255, 255, 0.62);
}
.field-box input {
  box-sizing: border-box;
  flex: 1;
  min-width: 0;
  height: 100%;
  padding: 0;
  background: transparent;
  border: none;
  outline: none;
  color: #ffffff;
  font: inherit;
  font-size: 16px;
}
.field-box input::placeholder {
  color: rgba(255, 255, 255, 0.4);
  opacity: 1;
}
.field-box input:-webkit-autofill,
.field-box input:-webkit-autofill:hover,
.field-box input:-webkit-autofill:focus,
.field-box input:-webkit-autofill:active {
  -webkit-text-fill-color: #ffffff;
  -webkit-box-shadow: 0 0 0 1000px rgba(255, 255, 255, 0.06) inset;
  transition: background-color 5000s ease-in-out 0s;
}
.eye-btn {
  position: relative;
  box-sizing: border-box;
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  margin-left: 0.375rem;
  background: transparent;
  border: none;
  color: rgba(255, 255, 255, 0.4);
  cursor: pointer;
  border-radius: 50%;
  transition: color 0.2s ease, background-color 0.2s ease;
}
.eye-btn::after {
  content: "";
  position: absolute;
  inset: -6px;
}
@media (hover: hover) {
  .eye-btn:hover {
    color: #ffffff;
    background: rgba(255, 255, 255, 0.08);
  }
}
.mode-btn:focus-visible,
.eye-btn:focus-visible,
.submit-btn:focus-visible,
.auth-top__iconbtn:focus-visible {
  outline: 2px solid var(--uhd-l-link);
  outline-offset: 3px;
}

/* 密码强度 */
.strength {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  margin-top: 0.125rem;
}
.strength-track {
  flex: 1;
  height: 3px;
  background: rgba(255, 255, 255, 0.08);
  border-radius: var(--uhd-radius-pill);
  overflow: hidden;
}
.strength-fill {
  height: 100%;
  width: 100%;
  border-radius: var(--uhd-radius-pill);
  transform: scaleX(0);
  transform-origin: left center;
  transition: transform 0.3s ease, background-color 0.3s ease;
}
.strength-fill.lv-1 { transform: scaleX(0.33); background: var(--uhd-danger); }
.strength-fill.lv-2 { transform: scaleX(0.66); background: var(--uhd-dot-doing); }
.strength-fill.lv-3 { transform: scaleX(1); background: var(--uhd-success); }
.strength-text {
  font-size: var(--uhd-fs-aux);
  color: rgba(255, 255, 255, 0.4);
  min-width: 16px;
}

/* 错误（红）与关闭提示（琥珀）保留独立语义色 */
.form-error {
  margin: 0;
  padding: 0.75rem 1rem;
  border-radius: var(--uhd-radius-base);
  background: rgba(220, 38, 38, 0.06);
  border: 1px solid rgba(220, 38, 38, 0.28);
  color: var(--uhd-danger);
  font-size: var(--uhd-fs-aux);
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.closed-notice {
  margin: 0;
  padding: 0.75rem 1rem;
  border-radius: var(--uhd-radius-base);
  background: var(--uhd-warn-bg-2);
  border: 1px solid rgba(217, 119, 6, 0.35);
  color: var(--uhd-warn-text);
  font-size: var(--uhd-fs-caption);
  line-height: 1.5;
  text-align: center;
}
.retry-link {
  background: none;
  border: none;
  padding: 0 8px;
  min-height: 44px;
  color: var(--uhd-l-link);
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}

/* 凭码注册第一步的获取提示 + 第二步的已验码条 */
.regcode-hint {
  margin: 0;
  font-size: var(--uhd-fs-aux);
  line-height: 1.6;
  color: rgba(255, 255, 255, 0.4);
}
.hint-link {
  color: var(--uhd-l-link);
  text-decoration: underline;
  white-space: nowrap;
}
.verified-code {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  padding: 0.625rem 1rem;
  border-radius: var(--uhd-radius-navitem);
  background: rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.62);
  font-size: var(--uhd-fs-aux);
  line-height: 1.5;
}
.verified-code .retry-link {
  min-height: 32px;
  margin-left: auto;
}

/* 主按钮：高 46、圆角约 23，淡彩虹渐变填充＋1px 彩虹边框＋黑字
   渐变底层＝实测清单第 3 节登录钮中心行采样串（逐停点照抄，色序/位置不变）；
   采样串是原色光谱，满版平铺会成饱和色带（用户录屏打回的「糖果条纹」），
   原站定性为「淡」彩虹——上覆白色雾面层把渲染拉回粉彩，修后中心行采样另附对照。 */
.submit-btn {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  width: 100%;
  height: 46px;
  flex-shrink: 0;
  margin-top: 0.25rem;
  padding: 0 24px;
  border: 1px solid transparent;
  border-radius: 23px;
  background:
    linear-gradient(90deg, rgba(255, 255, 255, 0.52), rgba(255, 255, 255, 0.52)) padding-box,
    linear-gradient(90deg, #fcfcfb 0%, #edebe5 4%, #bc9c00 9%, #b8a207 13%, #b4a80e 17%, #b0a919 22%, #a9aa25 26%, #a0aa30 30%, #95ab3b 35%, #86ab46 39%, #72ac52 43%, #50ac5f 48%, #0fac75 52%, #00ac9b 57%, #00a3c4 61%, #6180de 65%, #b28dd9 70%, #edfafb 74%, #e1f5f7 78%, #d9f2f6 83%, #d2f0f3 87%, #c8eef1 91%, #c1eaef 96%, #b9e7ed 100%) padding-box,
    var(--uhd-rainbow) border-box;
  color: #111111;
  font: inherit;
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
  transition: transform 0.15s ease, filter 0.2s ease, opacity 0.2s ease;
}
.submit-btn:hover:not(:disabled) {
  filter: brightness(1.03);
}
.submit-btn:active:not(:disabled) {
  transform: scale(0.98);
}
.submit-btn:disabled {
  background: rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.4);
  opacity: 0.55;
  cursor: not-allowed;
}

/* 找回密码：入口链 + 展开小表单 + 重置说明（跟随页面深浅两套变量口径） */
.forgot-row {
  display: flex;
  justify-content: flex-end;
  margin-top: -0.55rem;
}
.forgot-link {
  background: none;
  border: none;
  padding: 4px 2px;
  min-height: 32px;
  color: var(--uhd-l-link);
  font: inherit;
  font-size: var(--uhd-fs-aux);
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
}
.forgot-link--center {
  align-self: center;
}
.forgot-box {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  padding: 1rem;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.04);
}
.forgot-ok {
  margin: 0;
  padding: 0.75rem 1rem;
  border-radius: var(--uhd-radius-base);
  background: rgba(34, 197, 94, 0.08);
  border: 1px solid rgba(34, 197, 94, 0.32);
  color: #4ade80;
  font-size: var(--uhd-fs-aux);
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.forgot-hint,
.reset-note {
  margin: 0;
  font-size: var(--uhd-fs-aux);
  line-height: 1.5;
  color: rgba(255, 255, 255, 0.4);
}

.auth-footnote {
  margin: 1.5rem 0 0;
  text-align: center;
  font-size: var(--uhd-fs-aux);
  color: rgba(255, 255, 255, 0.4);
  line-height: 1.6;
}
.auth-legal-link {
  color: inherit;
  text-decoration: underline;
  text-underline-offset: 2px;
}

@media (max-width: 767px) {
  .auth-page {
    padding: 0 16px calc(24px + env(safe-area-inset-bottom));
  }
  .auth-top {
    padding-top: 14px;
  }
  .auth-column {
    max-width: 100%;
    padding: 32px 24px 26px;
    /* 2026-11-13 录屏打回后改令：移动端同样对齐实测（卡圆角约 20、
       输入/主钮 44–46 高、圆角约 23），不再保留上一轮的放大移动值 */
    border-radius: 20px;
  }
  .field-box {
    min-height: 46px;
    border-radius: 23px;
  }
  .submit-btn {
    height: 46px;
    border-radius: 23px;
  }
  .tg-login-btn {
    height: 46px;
    border-radius: 23px;
  }
}

/* TG 一键登录入口：与主钮同尺寸（高 46、圆角约 23）的白底描边钮（原站 Passkey 钮位） */
.tg-login {
  margin-top: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.tg-login-divider {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  color: rgba(255, 255, 255, 0.4);
  font-size: var(--uhd-fs-aux);
}
.tg-login-divider::before,
.tg-login-divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: rgba(255, 255, 255, 0.1);
}
.tg-login-btn {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  width: 100%;
  height: 46px;
  padding: 0 24px;
  background: #141416;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 23px;
  color: #ffffff;
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  text-decoration: none;
  transition: filter 0.2s ease, border-color 0.2s ease;
}
.tg-login-btn:hover {
  filter: brightness(0.98);
}
.tg-login-btn:focus-visible {
  outline: 2px solid var(--uhd-l-link);
  outline-offset: 2px;
}
.tg-login-hint {
  margin: 0;
  font-size: var(--uhd-fs-aux);
  line-height: 1.5;
  color: rgba(255, 255, 255, 0.4);
  text-align: center;
}

@media (prefers-reduced-motion: reduce) {
  /* 自动填充的 5000s 背景过渡是防黄色底色的 hack，不是可见动画，排除在外 */
  *:not(:-webkit-autofill) {
    animation: none !important;
    transition: none !important;
  }
}

/* 浅色覆盖：跟随用户外观（html[data-theme='light']） */
html[data-theme='light'] .auth-page {
  background:
    radial-gradient(600px 420px at 12% 4%, var(--uhd-blob-peach), transparent 70%),
    radial-gradient(560px 400px at 88% 10%, var(--uhd-blob-sky), transparent 70%),
    radial-gradient(640px 460px at 80% 96%, var(--uhd-blob-lilac), transparent 70%),
    radial-gradient(520px 380px at 6% 92%, var(--uhd-blob-mint), transparent 70%),
    var(--uhd-l-bg);
  color: var(--uhd-l-text);
}
html[data-theme='light'] .auth-top__brand { color: var(--uhd-l-text); }
html[data-theme='light'] .auth-top__sub { color: var(--uhd-l-text-3); }
html[data-theme='light'] .auth-top__glass {
  background: rgba(255, 255, 255, 0.6);
  border-color: rgba(255, 255, 255, 0.65);
}
html[data-theme='light'] .auth-top__iconbtn { color: var(--uhd-l-text); }
html[data-theme='light'] .auth-top__divider { background: var(--uhd-l-border); }
html[data-theme='light'] .auth-menu {
  background: var(--uhd-l-surface);
  border-color: var(--uhd-l-border);
  box-shadow: var(--uhd-shadow-card-l);
}
html[data-theme='light'] .auth-menu a { color: var(--uhd-l-text); }
html[data-theme='light'] .auth-menu a:hover { background: var(--uhd-l-seg-bg); }
html[data-theme='light'] .auth-column {
  background: var(--uhd-l-surface);
  border-color: var(--uhd-l-border);
  box-shadow: var(--uhd-shadow-card-l);
}
html[data-theme='light'] .auth-title { color: var(--uhd-l-text); }
html[data-theme='light'] .mode-switch { background: var(--uhd-l-seg-bg); }
html[data-theme='light'] .mode-btn { color: var(--uhd-l-text-2); }
html[data-theme='light'] .mode-btn.active { color: var(--uhd-l-text); }
html[data-theme='light'] .mode-indicator { background: var(--uhd-l-surface); }
html[data-theme='light'] .field-label { color: var(--uhd-l-text); }
html[data-theme='light'] .optional { color: var(--uhd-l-text-3); }
/* 输入框底/边按实测清单第 3 节字面值（#f4f4f5 / #e4e4e7），不用共享令牌近似值 */
html[data-theme='light'] .field-box { background: #f4f4f5; border-color: #e4e4e7; }
html[data-theme='light'] .field-icon { color: var(--uhd-l-text-3); }
html[data-theme='light'] .field-box:focus-within .field-icon { color: var(--uhd-l-text-2); }
html[data-theme='light'] .field-box input { color: var(--uhd-l-text); }
html[data-theme='light'] .field-box input::placeholder { color: var(--uhd-l-text-3); }
html[data-theme='light'] .field-box input:-webkit-autofill {
  -webkit-text-fill-color: var(--uhd-l-text);
  -webkit-box-shadow: 0 0 0 1000px #f4f4f5 inset;
}
html[data-theme='light'] .eye-btn { color: var(--uhd-l-text-3); }
html[data-theme='light'] .eye-btn:hover { color: var(--uhd-l-text); background: var(--uhd-l-seg-bg); }
html[data-theme='light'] .strength-track { background: var(--uhd-l-divider); }
html[data-theme='light'] .strength-text { color: var(--uhd-l-text-3); }
html[data-theme='light'] .regcode-hint { color: var(--uhd-l-text-3); }
html[data-theme='light'] .verified-code { background: var(--uhd-l-active); color: var(--uhd-l-text-2); }
html[data-theme='light'] .submit-btn:disabled { background: var(--uhd-l-input-bg); color: var(--uhd-l-text-3); }
html[data-theme='light'] .forgot-box { background: var(--uhd-l-seg-bg); border-color: var(--uhd-l-border); }
html[data-theme='light'] .forgot-ok { color: #15803d; }
html[data-theme='light'] .forgot-hint,
html[data-theme='light'] .reset-note { color: var(--uhd-l-text-3); }
html[data-theme='light'] .auth-footnote { color: var(--uhd-l-text-3); }
html[data-theme='light'] .tg-login-divider { color: var(--uhd-l-text-3); }
html[data-theme='light'] .tg-login-divider::before,
html[data-theme='light'] .tg-login-divider::after { background: var(--uhd-l-border); }
html[data-theme='light'] .tg-login-btn {
  background: var(--uhd-l-surface);
  border-color: var(--uhd-l-border);
  color: var(--uhd-l-text);
}
html[data-theme='light'] .tg-login-hint { color: var(--uhd-l-text-3); }
</style>
