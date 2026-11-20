<script setup lang="ts">
/**
 * 首次运行向导：全新部署建第一个管理员。
 * 进 /admin/ 时路由守卫已确认 setup_completed 为 false 才放行到这里；
 * 页面加载时再向后端确认一次（已完成则直接去登录页）。
 * 成功后跳登录页（?initialized=1），向导入口永久关闭。
 *
 * ax 口径（批 1 · 与 Login.vue / 用户端 LoginView 同一张「认证卡」）：
 * 四角淡彩光晕底、实色卡片 + 20px 圆角 + panel 大柔影、彩虹品牌标、
 * 无衬线站名、带图标的输入框（全胶囊圆角、聚焦主色光环）、彩虹主按钮
 * （细彩虹环＋玻璃底、高 46、全胶囊）；眉题写「初始化向导」，并多一条
 * 两步的步骤条。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { CheckCircle2, KeyRound, Loader2, Lock, ShieldCheck, User } from 'lucide-vue-next'
import BrandMark from '@/components/BrandMark.vue'
import { createFirstAdmin, setupStatus } from '@/api/admin'
import { branding, initBranding } from '@/composables/branding'

void initBranding()

const router = useRouter()

const form = reactive({ username: '', password: '', confirm: '', bootstrap_token: '' })
const checking = ref(true)
const submitting = ref(false)
const error = ref('')
const done = ref(false)

const USERNAME_RE = /^[a-zA-Z0-9_]{3,32}$/

const usernameHint = computed(() => {
  if (!form.username) return ''
  return USERNAME_RE.test(form.username) ? '' : '只允许字母 / 数字 / 下划线，长度 3–32'
})
const passwordHint = computed(() => {
  if (!form.password) return ''
  if (form.password.length < 6) return '密码至少 6 位'
  if (form.password.length < 12) return '建议 12 位以上，混用大小写、数字与符号'
  return ''
})
const confirmHint = computed(() => {
  if (!form.confirm) return ''
  return form.confirm === form.password ? '' : '两次输入的密码不一致'
})
const canSubmit = computed(
  () =>
    !submitting.value &&
    USERNAME_RE.test(form.username) &&
    form.password.length >= 6 &&
    form.confirm === form.password,
)

onMounted(async () => {
  try {
    const st = await setupStatus()
    if (st.setup_completed) {
      router.replace('/login')
      return
    }
  } catch {
    // 状态接口异常时不挡路：路由守卫已有缓存结论，POST 本身也会 403 兜底
  } finally {
    checking.value = false
  }
})

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  error.value = ''
  try {
    await createFirstAdmin({
      username: form.username.trim(),
      password: form.password,
      // P0-4：部署时若配置了 SETUP_BOOTSTRAP_TOKEN，这里必须填一致的 token
      ...(form.bootstrap_token.trim()
        ? { bootstrap_token: form.bootstrap_token.trim() }
        : {}),
    })
    done.value = true
    setTimeout(() => router.replace({ path: '/login', query: { initialized: '1' } }), 1200)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '创建失败，请重试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <!-- ax 口径：背景是四角淡彩光晕（见下方 .auth-page，与用户端 LoginView 同配方） -->
    <div class="auth-card">
      <div class="auth-brand">
        <div class="brand-mark">
          <img v-if="branding.logo_url" :src="branding.logo_url" :alt="branding.site_name" />
          <span v-else class="brand-mark__fallback"><BrandMark /></span>
        </div>
        <p class="au-eyebrow brand-eyebrow">初始化向导</p>
        <h1 class="brand-title">{{ branding.site_name }}</h1>
        <p class="brand-subtitle">第一次使用：先创建一个管理员账号，之后这个入口会永久关闭</p>
      </div>

      <!-- 步骤条 -->
      <ol class="setup-steps" aria-label="初始化步骤">
        <li :class="done ? 'is-done' : 'is-active'" :aria-current="done ? undefined : 'step'">
          <span class="dot"><CheckCircle2 v-if="done" :size="12" /><template v-else>1</template></span>创建管理员
        </li>
        <li :class="{ 'is-active': done }" :aria-current="done ? 'step' : undefined">
          <span class="dot">2</span>完成
        </li>
      </ol>

      <div v-if="checking" class="setup-loading" aria-busy="true">
        <Loader2 :size="18" class="spinning" />
        <span>正在确认初始化状态…</span>
      </div>

      <div v-else-if="done" class="setup-done" role="status">
        <span class="done-icon"><CheckCircle2 :size="26" /></span>
        <h2>初始化完成</h2>
        <p>管理员账号 <strong>{{ form.username }}</strong> 已创建，向导入口已永久关闭。<br />正在前往登录页…</p>
      </div>

      <form v-else class="auth-form" @submit.prevent="submit">
        <label class="field">
          <span class="field-label">管理员用户名</span>
          <span class="field-box" :class="{ 'is-warn': usernameHint }">
            <User :size="16" class="field-icon" />
            <input v-model="form.username" type="text" name="username" autocomplete="username" placeholder="例如 admin" />
          </span>
          <span v-if="usernameHint" class="field-hint warn">{{ usernameHint }}</span>
        </label>
        <label class="field">
          <span class="field-label">密码</span>
          <span class="field-box" :class="{ 'is-warn': form.password && form.password.length < 6 }">
            <Lock :size="16" class="field-icon" />
            <input v-model="form.password" type="password" name="password" autocomplete="new-password" placeholder="至少 6 位" />
          </span>
          <span v-if="passwordHint" class="field-hint" :class="{ warn: form.password.length < 6 }">
            {{ passwordHint }}
          </span>
        </label>
        <label class="field">
          <span class="field-label">确认密码</span>
          <span class="field-box" :class="{ 'is-warn': confirmHint }">
            <KeyRound :size="16" class="field-icon" />
            <input v-model="form.confirm" type="password" name="confirm" autocomplete="new-password" placeholder="再输一次密码" />
          </span>
          <span v-if="confirmHint" class="field-hint warn">{{ confirmHint }}</span>
        </label>
        <label class="field">
          <span class="field-label">部署者凭据 <span class="field-optional">（部署时若配置了 bootstrap token 则必填）</span></span>
          <span class="field-box">
            <ShieldCheck :size="16" class="field-icon" />
            <input v-model="form.bootstrap_token" type="password" name="bootstrap_token" autocomplete="off" placeholder="部署时注入的一次性 token" />
          </span>
        </label>

        <p v-if="error" class="form-error" role="alert">{{ error }}</p>

        <button class="submit-btn" type="submit" :disabled="!canSubmit">
          <Loader2 v-if="submitting" :size="16" class="spinning" />
          <span>{{ submitting ? '创建中…' : '创建管理员并完成初始化' }}</span>
        </button>
      </form>

      <p class="auth-foot"><ShieldCheck :size="13" /> 网站账号与客户端共用同一套账号密码</p>
    </div>
  </div>
</template>

<style scoped>
.auth-page {
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.5rem;
  /* 四角淡彩光晕（与用户端 LoginView .auth-page 同配方）：深色低透明同色；
     浅色走 --ax-blob-* 令牌、底色 --ax-l-bg。 */
  background:
    radial-gradient(600px 420px at 12% 4%, rgba(246, 205, 190, 0.13), transparent 70%),
    radial-gradient(560px 400px at 88% 10%, rgba(160, 195, 245, 0.13), transparent 70%),
    radial-gradient(640px 460px at 80% 96%, rgba(205, 190, 245, 0.11), transparent 70%),
    radial-gradient(520px 380px at 6% 92%, rgba(175, 230, 205, 0.1), transparent 70%),
    var(--au-bg);
}
html[data-theme='light'] .auth-page {
  background:
    radial-gradient(600px 420px at 12% 4%, var(--ax-blob-peach), transparent 70%),
    radial-gradient(560px 400px at 88% 10%, var(--ax-blob-sky), transparent 70%),
    radial-gradient(640px 460px at 80% 96%, var(--ax-blob-lilac), transparent 70%),
    radial-gradient(520px 380px at 6% 92%, var(--ax-blob-mint), transparent 70%),
    var(--au-bg);
}

.auth-card {
  width: 100%;
  max-width: 420px;
  background: var(--au-surface);
  border: 1px solid var(--au-border);
  /* 用户端认证卡实测圆角约 20 + panel 大柔影 */
  border-radius: 20px;
  padding: 2rem 1.75rem 1.5rem;
  box-shadow: var(--ax-shadow-panel-lg);
  animation: au-fade-up var(--au-fade-in) var(--au-ease) both;
}

.auth-brand {
  text-align: center;
  margin-bottom: 1.5rem;
}

.brand-mark {
  width: 46px;
  height: 46px;
  margin: 0 auto 0.75rem;
  border-radius: var(--au-r-md);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--au-primary);
}

.brand-mark img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  border-radius: inherit;
}

.brand-mark__fallback {
  width: 24px;
  height: 23px;
  flex: none;
  /* 品牌标定版口径：深色面上 A 为白色，不随管理端主色染色 */
  color: #ffffff;
}

.brand-eyebrow { margin: 0 0 0.375rem; }

/* 站名：与登录页同一口径，字距 ≤0.04em，不再 uppercase */
.brand-title {
  font-size: var(--ax-fs-h3);
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--au-text);
  margin: 0 0 0.5rem;
}

.brand-subtitle {
  font-size: var(--ax-fs-aux);
  color: var(--au-text-3);
  margin: 0;
  line-height: 1.5;
}

.auth-hint {
  font-size: var(--ax-fs-aux);
  color: var(--au-text-2);
  background: var(--au-primary-soft);
  border: 1px solid var(--au-primary-border);
  border-radius: var(--au-r-sm);
  padding: 0.5rem 0.75rem;
  margin: 0 0 1rem;
  line-height: 1.5;
}

.auth-form {
  display: flex;
  flex-direction: column;
  gap: 0.875rem;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.field-label {
  font-size: var(--ax-fs-aux);
  font-weight: 500;
  color: var(--au-text-3);
}

.field-box {
  display: flex;
  align-items: center;
  height: 44px;
  background: var(--au-input-bg);
  border: 1px solid var(--au-input-border);
  /* 用户端登录输入实测全胶囊圆角（约 23px = 高 46 的一半）；本框高 44，pill 同效 */
  border-radius: var(--ax-radius-pill);
  padding: 0 0.75rem;
  transition: border-color var(--au-fast) var(--au-ease), box-shadow var(--au-fast) var(--au-ease),
    background var(--au-fast) var(--au-ease);
}

.field-box:focus-within {
  background: var(--au-input-bg-focus);
  border-color: var(--au-border-focus);
  box-shadow: 0 0 0 3px var(--au-primary-soft);
}

.field-icon {
  color: var(--au-text-3);
  margin-right: 0.5rem;
  flex-shrink: 0;
}

.field-box:focus-within .field-icon { color: var(--au-primary); }

.field-box input {
  flex: 1;
  min-width: 0;
  height: 100%;
  background: transparent;
  border: none;
  outline: none;
  color: var(--au-text);
  font-size: var(--ax-fs-caption);
}

.field-box input::placeholder { color: var(--au-text-4); }

.form-error {
  margin: 0;
  padding: 0.5rem 0.75rem;
  border-radius: var(--au-r-sm);
  background: var(--au-danger-soft);
  border: 1px solid var(--au-danger-border);
  color: var(--au-danger);
  font-size: var(--ax-fs-aux);
  line-height: 1.4;
}

/* 主按钮 —— ax 口径（与用户端 .au-btn-primary 同一套外观）：细彩虹环＋玻璃底；
   高 46、全胶囊圆角（用户端登录主钮实测约 23px）；深色白字、浅色淡彩粉彩底＋黑字。 */
.submit-btn {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  height: 46px;
  margin-top: 0.25rem;
  border: 1.5px solid transparent;
  border-radius: var(--ax-radius-pill);
  background: linear-gradient(var(--ax-glass-btn-d), var(--ax-glass-btn-d));
  color: #ffffff;
  box-shadow: var(--ax-shadow-sheet);
  backdrop-filter: var(--ax-glass-filter);
  -webkit-backdrop-filter: var(--ax-glass-filter);
  font-size: var(--ax-fs-body);
  font-weight: 600;
  cursor: pointer;
  transition: filter var(--au-fast) var(--au-ease), transform var(--au-fast) var(--au-ease);
}
.submit-btn::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: var(--ax-rainbow-border-w);
  background: var(--ax-rainbow);
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  mask-composite: exclude;
  pointer-events: none;
}
html[data-theme='light'] .submit-btn {
  background: var(--ax-rainbow-soft);
  color: #111111;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.65),
    var(--ax-shadow-card-l);
}

.submit-btn:hover:not(:disabled) { filter: brightness(1.08); }
.submit-btn:active:not(:disabled) { transform: scale(var(--ax-press-scale)); }

/* 禁用态：中性表面 + 三级字（不做「褪色主色」），彩虹环随之隐藏 */
.submit-btn:disabled {
  background: var(--au-surface-2);
  color: var(--au-text-3);
  box-shadow: inset 0 0 0 1px var(--au-border);
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
  cursor: not-allowed;
}
.submit-btn:disabled::before {
  display: none;
}

.submit-btn:focus-visible { outline: 2px solid var(--au-border-focus); outline-offset: 2px; }

.spinning { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.auth-foot {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: var(--ax-fs-aux-sm);
  color: var(--au-text-4);
  margin: 1.25rem 0 0;
}

/* 手机：卡片少一点内边距，键盘弹起时不被顶出屏幕（同 Login.vue） */
@media (max-width: 480px) {
  .auth-page { padding: 14px; align-items: flex-start; padding-top: max(28px, env(safe-area-inset-top)); }
  .auth-card { padding: 1.625rem 1.25rem 1.25rem; }
  .brand-title { font-size: var(--ax-fs-title); }
}

/* ---------- 初始化向导独有：步骤条 / 检查中 / 完成态 / 字段提示 ---------- */
.setup-steps {
  display: flex;
  gap: 8px;
  list-style: none;
  margin: 0 0 1.25rem;
  padding: 0;
}

.setup-steps li {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 8px 10px;
  font-size: var(--ax-fs-aux);
  color: var(--au-text-4);
  background: var(--au-surface-2);
  border: 1px solid var(--au-border);
  border-radius: var(--au-r-sm);
}

.setup-steps li.is-active { color: var(--au-text); border-color: var(--au-primary-border); background: var(--au-primary-soft); }
.setup-steps li.is-done { color: var(--au-text-2); }

.setup-steps .dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  flex: none;
  border-radius: var(--au-r-full);
  background: var(--au-surface-3);
  color: var(--au-text-3);
  font-size: var(--ax-fs-micro);
  font-weight: 700;
}

.setup-steps li.is-active .dot { background: var(--au-primary); color: var(--au-on-primary); }
.setup-steps li.is-done .dot { background: var(--au-success-soft); color: var(--au-success); }

.setup-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 2.5rem 0;
  font-size: var(--ax-fs-aux);
  color: var(--au-text-3);
}

.setup-done { text-align: center; padding: 0.75rem 0 0.25rem; }

.done-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  border-radius: var(--au-r-full);
  background: var(--au-success-soft);
  border: 1px solid var(--au-success-border);
  color: var(--au-success);
}

.setup-done h2 { font-size: var(--ax-fs-title); margin: 0.75rem 0 0.5rem; color: var(--au-text); }
.setup-done p { font-size: var(--ax-fs-aux); color: var(--au-text-3); line-height: 1.7; margin: 0; }
.setup-done strong { color: var(--au-text); }

.field-box.is-warn { border-color: var(--au-warning-border); }
.field-hint { font-size: var(--ax-fs-aux-sm); line-height: 1.5; color: var(--au-text-3); }
.field-hint.warn { color: var(--au-warning); }
</style>
