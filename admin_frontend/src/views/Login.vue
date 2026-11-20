<script setup lang="ts">
/**
 * 管理员登录页：JWT 登录 → 恢复 ?redirect 或进入概览
 *
 * ax 口径（批 1 · 与用户端 LoginView 同一张「认证卡」，实测值照搬）：
 * 四角淡彩光晕底（深色低透明同色 / 浅色走 --ax-blob-* 令牌）、实色卡片 +
 * 20px 圆角 + panel 大柔影、彩虹品牌标、无衬线站名、带图标的输入框（46px 高、
 * 全胶囊圆角、聚焦主色光环）、彩虹主按钮（细彩虹环＋玻璃底、高 46、全胶囊）。
 * 副标题写明这是管理后台，不再另放一枚眉题。
 */
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Loader2, Lock, ShieldCheck, User } from 'lucide-vue-next'
import BrandMark from '@/components/BrandMark.vue'
import { login } from '@/api/admin'
import { useAuthStore } from '@/stores/auth'
// 站名与 Logo 来自「站点与品牌」能力（未配置时用默认值）
import { branding, initBranding } from '@/composables/branding'

void initBranding()

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const form = reactive({ username: '', password: '' })
const loading = ref(false)
const error = ref('')

async function submit() {
  if (!form.username || !form.password) {
    error.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const res = await login({
      username: form.username,
      password: form.password,
    })
    auth.setSession(res.access_token, res.user)
    const redirect = (route.query.redirect as string) || '/'
    router.push(redirect)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '登录失败'
  } finally {
    loading.value = false
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
        <h1 class="brand-title">{{ branding.site_name }}</h1>
        <p class="brand-subtitle">运营管理后台 · 仅限管理员账号登录</p>
      </div>

      <!-- 首次运行向导刚完成：告诉用户用刚建的账号登录 -->
      <p v-if="route.query.initialized === '1'" class="auth-hint">
        初始化完成，管理员账号已创建，请登录
      </p>

      <!-- 门户（用户端）已登录时的免登结果：能自动进就直接进，不能进也要说明原因 -->
      <p v-if="auth.ssoNotice" class="auth-hint">{{ auth.ssoNotice }}</p>

      <form class="auth-form" @submit.prevent="submit">
        <label class="field">
          <span class="field-label">用户名</span>
          <span class="field-box">
            <User :size="16" class="field-icon" />
            <input v-model="form.username" type="text" name="username" autocomplete="username" placeholder="管理员用户名" />
          </span>
        </label>
        <label class="field">
          <span class="field-label">密码</span>
          <span class="field-box">
            <Lock :size="16" class="field-icon" />
            <input v-model="form.password" type="password" name="password" autocomplete="current-password" placeholder="登录密码" />
          </span>
        </label>

        <p v-if="error" class="form-error" role="alert">{{ error }}</p>

        <button class="submit-btn" type="submit" :disabled="loading">
          <Loader2 v-if="loading" :size="16" class="spinning" />
          <span>{{ loading ? '登录中…' : '登录' }}</span>
        </button>
      </form>

      <p class="auth-foot"><ShieldCheck :size="13" /> 登录状态保存在本地，操作会记入管理日志</p>
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
  max-width: 400px;
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
  /* 主色淡底 + 主色发丝描边 */
  background: var(--au-primary-soft);
  border: 1px solid var(--au-primary-border);
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

/* 站名：衬线（base.css 的 h1），字距收进 0.04em 以内，不再 uppercase */
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

/* 手机：卡片少一点内边距，键盘弹起时不被顶出屏幕 */
@media (max-width: 480px) {
  .auth-page { padding: 14px; align-items: flex-start; padding-top: max(28px, env(safe-area-inset-top)); }
  .auth-card { padding: 1.625rem 1.25rem 1.25rem; }
  .brand-title { font-size: var(--ax-fs-title); }
}
</style>
