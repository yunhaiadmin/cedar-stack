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
import { User, Lock, Mail, Eye, EyeOff, Clapperboard, Gift, Send, KeyRound, BadgeCheck } from 'lucide-vue-next'
// 人机验证挂件（能力：人机验证）：管理员未开启时该组件自己什么都不渲染
import CaptchaChallenge from '@/components/ui/CaptchaChallenge.vue'
// 站名来自「站点与品牌」能力（未配置时用默认值）
import { branding } from '@/composables/useBranding'
import { getRegisterConfig, verifyRegisterCode } from '@/api/user'
import { tgApi } from '@/api/tg'
import { fetchTgBotUsername } from '@/api/site'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const toast = useToast()

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

const loginForm = reactive({ username: '', password: '' })
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
    await userStore.login(loginForm.username.trim(), loginForm.password, captchaToken.value)
    toast.success('登录成功')
    router.push(safeRedirect(route.query.redirect))
  } catch (err) {
    error.value = friendlyError(err, '登录失败，请稍后重试')
    // 令牌是一次性的：失败后换一个新的，否则第二次点击会因「已使用」而失败
    captchaRef.value?.reset()
  } finally {
    loginLoading.value = false
  }
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
// 但本页视觉自 UHD 换皮批 1 起为固定浅色卡式（说明书逐页对照表第 1/2 行，
// 覆盖 2026-10-14「登录页跟随用户主题」的视觉口径）：样式只用 --uhd-l-* 浅色令牌，
// 不随 data-theme 变色；逻辑与主题机制一字未动。

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
    <div class="auth-column">
      <div class="auth-brand">
        <div class="brand-mark">
          <img decoding="async" loading="lazy" v-if="branding.logo_url" :src="branding.logo_url" :alt="branding.site_name" />
          <Clapperboard v-else :size="22" aria-hidden="true" />
        </div>
        <h1 class="brand-title">{{ branding.site_name }}</h1>
        <p class="brand-subtitle">自建 Emby 影视服务 · 一个账号，手机/电视/电脑都能看</p>
      </div>

      <!-- 模式切换 -->
      <div class="mode-switch" role="tablist" aria-label="登录或注册">
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

      <!-- 登录表单 -->
      <form v-if="mode === 'login'" id="panel-login" class="auth-form" role="tabpanel" aria-labelledby="tab-login" tabindex="0" @submit.prevent="handleLogin">
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

        <p v-if="error" id="login-form-error" class="form-error" role="alert">{{ error }}</p>

        <CaptchaChallenge ref="captchaRef" action="login" @update:token="captchaToken = $event" />

        <button type="submit" class="submit-btn" :disabled="loginLoading || !loginForm.username.trim() || !loginForm.password">
          {{ loginLoading ? '登录中…' : '登 录' }}
        </button>

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
      <form v-else-if="registrationCodeRequired && registerStep === 1" id="panel-register" class="auth-form" role="tabpanel" aria-labelledby="tab-register" tabindex="0" @submit.prevent="handleVerifyRegCode">
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
      <form v-else id="panel-register" class="auth-form" role="tabpanel" aria-labelledby="tab-register" tabindex="0" @submit.prevent="handleRegister">
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
</template>

<style scoped>
/* ============================================================
   UHD 换皮批 1 · 登录/注册固定浅色卡式（说明书逐页对照表第 1/2 行）
   粉彩光斑底（仅入口页）+ 居中白卡 445px / 圆角 20 / 柔和阴影；
   标签 14px、输入框 44px 高圆角 13px 浅灰底、主按钮 46px 全宽胶囊
   （彩虹边框 + 淡彩底，白名单位置）、模式切换为分段切换模具。
   只用 --uhd-* 令牌；业务逻辑、校验与两步流程均未改动。
   ============================================================ */
.auth-page {
  box-sizing: border-box;
  min-height: 100vh;
  min-height: 100svh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
  position: relative;
  isolation: isolate;
  overflow: hidden;
  background:
    radial-gradient(600px 420px at 12% 6%, var(--uhd-blob-peach), transparent 70%),
    radial-gradient(560px 400px at 88% 12%, var(--uhd-blob-sky), transparent 70%),
    radial-gradient(640px 460px at 80% 94%, var(--uhd-blob-lilac), transparent 70%),
    radial-gradient(520px 380px at 6% 90%, var(--uhd-blob-mint), transparent 70%),
    var(--uhd-l-bg);
  color: var(--uhd-l-text);
  font-family: var(--uhd-font-sans);
}

/* 居中白卡本体 */
.auth-column {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 445px;
  min-width: 0;
  box-sizing: border-box;
  padding: 38px 34px 30px;
  background: var(--uhd-l-surface);
  border: 1px solid var(--uhd-l-border);
  border-radius: 20px;
  box-shadow: var(--uhd-shadow-card-l);
  animation: authColumnIn var(--uhd-dur-blur-in) var(--uhd-ease-pop) both;
}

@keyframes authColumnIn {
  from { opacity: 0; transform: translateY(12px); filter: blur(6px); }
  to { opacity: 1; transform: none; filter: blur(0); }
}

.auth-brand {
  text-align: center;
  margin-bottom: 1.75rem;
}

.brand-mark {
  width: 46px;
  height: 46px;
  margin: 0 auto 1rem;
  border-radius: var(--uhd-radius-base);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--uhd-l-text-2);
}

.brand-mark img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  border-radius: inherit;
}

.brand-title {
  font-size: var(--uhd-fs-h3-lg);
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: 0.01em;
  color: var(--uhd-l-text);
  margin: 0 0 0.5rem;
  line-height: 1.25;
  overflow-wrap: anywhere;
}

.brand-subtitle {
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
  margin: 0;
  line-height: 1.6;
}

/* 模式切换：分段切换模具（浅灰底 + 白胶囊激活项） */
.mode-switch {
  box-sizing: border-box;
  position: relative;
  display: grid;
  grid-template-columns: 1fr 1fr;
  background: var(--uhd-l-seg-bg);
  border: none;
  border-radius: 22px;
  padding: 4px;
  margin-bottom: 1.5rem;
}

.mode-btn {
  position: relative;
  z-index: 1;
  min-width: 0;
  min-height: 36px;
  padding: 0 0.75rem;
  border: none;
  background: transparent;
  border-radius: var(--uhd-radius-pill);
  font: inherit;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  line-height: 1;
  color: var(--uhd-l-text-2);
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
  color: var(--uhd-l-text);
  font-weight: var(--uhd-fw-semibold);
}

.mode-indicator {
  position: absolute;
  top: 4px;
  left: 4px;
  width: calc(50% - 4px);
  height: calc(100% - 8px);
  border-radius: var(--uhd-radius-pill);
  background: var(--uhd-l-surface);
  box-shadow: var(--uhd-shadow-seg);
  pointer-events: none;
  transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}

.mode-indicator.right {
  transform: translateX(100%);
}

/* 表单与字段（卡内口径：标签 14px、输入 44px / 圆角 13px / 浅灰底） */
.auth-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  min-width: 0;
}

.field-label {
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  line-height: 1.5;
  color: var(--uhd-l-text);
}

.optional {
  margin-left: 0.25rem;
  font-size: var(--uhd-fs-aux-sm);
  font-style: normal;
  font-weight: var(--uhd-fw-normal);
  color: var(--uhd-l-text-3);
}

.field-box {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  min-height: 44px;
  background: var(--uhd-l-input-bg);
  border: 1px solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-input);
  padding: 0 14px;
  transition: box-shadow 0.2s ease, border-color 0.2s ease;
}

.field-box:focus-within {
  border-color: var(--uhd-l-link);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.14);
}

.field-icon {
  color: var(--uhd-l-text-3);
  margin-right: 0.625rem;
  flex-shrink: 0;
  transition: color 0.2s ease;
}

.field-box:focus-within .field-icon {
  color: var(--uhd-l-text-2);
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
  color: var(--uhd-l-text);
  font: inherit;
  font-size: var(--uhd-fs-body);
}

.field-box input::placeholder {
  color: var(--uhd-l-text-3);
  opacity: 1;
}

/* 浏览器自动填充默认底色会盖住输入框，强制跟随卡内浅灰底 */
.field-box input:-webkit-autofill,
.field-box input:-webkit-autofill:hover,
.field-box input:-webkit-autofill:focus,
.field-box input:-webkit-autofill:active {
  -webkit-text-fill-color: var(--uhd-l-text);
  -webkit-box-shadow: 0 0 0 1000px var(--uhd-l-input-bg) inset;
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
  color: var(--uhd-l-text-3);
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
    color: var(--uhd-l-text);
    background: var(--uhd-l-divider);
  }
}

.mode-btn:focus-visible,
.eye-btn:focus-visible,
.submit-btn:focus-visible {
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
  background: var(--uhd-l-divider);
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
  color: var(--uhd-l-text-3);
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
  color: var(--uhd-l-text-3);
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
  background: var(--uhd-l-active);
  color: var(--uhd-l-text-2);
  font-size: var(--uhd-fs-aux);
  line-height: 1.5;
}

.verified-code .retry-link {
  min-height: 32px;
  margin-left: auto;
}

/* 主按钮：46px 全宽胶囊，彩虹边框 + 淡彩底（白名单位置，同 UhdButton 主按钮口径） */
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
  border: var(--uhd-rainbow-border-w) solid transparent;
  border-radius: var(--uhd-radius-pill);
  background:
    var(--uhd-rainbow-soft) padding-box,
    var(--uhd-rainbow) border-box;
  background-color: var(--uhd-l-surface);
  color: var(--uhd-l-text);
  font: inherit;
  font-size: var(--uhd-fs-body);
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
  background: var(--uhd-l-input-bg);
  border-color: transparent;
  color: var(--uhd-l-text-3);
  opacity: 0.55;
  cursor: not-allowed;
}

.auth-footnote {
  margin: 1.5rem 0 0;
  text-align: center;
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-l-text-3);
  line-height: 1.6;
}

/* 登录页脚注里的隐私政策链接：未登录访客挂载后落到登录页时仍能看到入口 */
.auth-legal-link {
  color: inherit;
  text-decoration: underline;
  text-underline-offset: 2px;
}

@media (max-width: 767px) {
  .auth-page {
    padding: 24px 16px calc(24px + env(safe-area-inset-bottom));
  }

  .auth-column {
    max-width: 100%;
    padding: 30px 22px 24px;
  }

  .field-box {
    min-height: 44px;
  }

  .submit-btn {
    height: 46px;
  }

  /* iOS 输入框字号 <16px 会触发自动放大，保持 16px */
  .field-box input {
    font-size: 16px;
  }

  .auth-footnote {
    margin-bottom: 8px;
  }
}

/* TG 一键登录入口（白底灰边次按钮，与彩虹主按钮区分主次） */
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
  color: var(--uhd-l-text-3);
  font-size: var(--uhd-fs-aux);
}

.tg-login-divider::before,
.tg-login-divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--uhd-l-border);
}

.tg-login-btn {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  width: 100%;
  min-height: 46px;
  padding: 0 24px;
  background: var(--uhd-l-surface);
  border: 1px solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-pill);
  color: var(--uhd-l-text);
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
  color: var(--uhd-l-text-3);
  text-align: center;
}

@media (prefers-reduced-motion: reduce) {
  /* 自动填充的 5000s 背景过渡是防黄色底色的 hack，不是可见动画，排除在外 */
  *:not(:-webkit-autofill) {
    animation: none !important;
    transition: none !important;
  }
}
</style>
