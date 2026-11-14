<script setup lang="ts">
/**
 * BindDriveSheet —— 绑定谷歌盘弹层（2026-11-14 用户亲点）
 *
 * 此前绑谷歌盘必须跳进账号设置页（DriveBindingsPanel 内嵌表单），
 * 用户亲点「绑定谷歌这个用线路一样弹窗吧」：改与线路弹层
 * （ServerLinesSheet）同款弹层，点绑定入口就地弹出、不跳页。
 * 壳、动效与主题变量口径照 ServerLinesSheet，本组件只做「新绑
 * 谷歌盘」一件事：
 *
 * - 只走新入口 oauth2（startDriveOAuth2；老入口已隐藏，不复活），
 *   入口可用性/资格以后端 providers 下发的 drive_oauth2 状态为准，
 *   与设置页表单同口径；不改后端、不改 OAuth 接口与 scope。
 * - 点绑定在点击手势内先开空窗保住手势、拿到授权地址再指过去
 *   （照 DriveBindingsPanel 做法，绝不带 noopener）；弹窗被拦时
 *   展示 driveAuthUrl 手动打开链接（同款兜底文案）。
 * - 打开授权后进入等待态：每 4 秒轮询绑定列表、回焦/回前台时
 *   补查，出现 active 谷歌绑定即成功（打勾提示后自动关弹层并
 *   emit 'bound'，父级据此刷新状态）；约 3 分钟未等到停轮询，
 *   提示关掉重来。错误原样展示在弹层内（formError 位置同设置页）。
 *
 * 设置页 DriveBindingsPanel 本体（解绑/重绑/验证/贡献池）不动，
 * 管理绑定仍走设置页；本弹层只负责新绑这一步。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Check, X } from 'lucide-vue-next'
import UhdButton from '@/components/uhd/UhdButton.vue'
import {
  storageBindingApi,
  type DriveOAuth2EntryStatus,
} from '@/api/economy'
import { humanizeError } from '@/composables/usePanelApi'
import { useToast } from '@/composables/useToast'
import './bind-drive-sheet.css'

const props = withDefaults(
  defineProps<{
    open: boolean
    /** 深色面语境（概览页按主题解析值传入）；片库面另由
     *  body.uhd-stream-dark 桥接，见 bind-drive-sheet.css */
    dark?: boolean
  }>(),
  { dark: false },
)

const emit = defineEmits<{ close: []; bound: [] }>()

const toast = useToast()

type Phase = 'form' | 'waiting' | 'success' | 'timeout'

const phase = ref<Phase>('form')
const oauth2 = ref<DriveOAuth2EntryStatus | null>(null)
const providersError = ref('')
const label = ref('')
const formError = ref('')
const authUrl = ref('')
const starting = ref(false)

const POLL_INTERVAL_MS = 4000
const WAIT_TIMEOUT_MS = 180_000
let pollTimer: ReturnType<typeof setInterval> | undefined
let waitDeadline = 0
let successTimer: ReturnType<typeof setTimeout> | undefined
let checking = false

const canBind = computed(
  () => !!oauth2.value?.available && !!oauth2.value?.eligible,
)

async function loadEntry() {
  providersError.value = ''
  try {
    const res = await storageBindingApi.providers()
    // 第二绑定入口状态随名单同响应下发：没这份数据就当入口不存在
    oauth2.value = res.drive_oauth2 || null
  } catch (e) {
    // 名单拿不到不猜，留错误态给用户重试（同设置页面板口径）
    providersError.value = humanizeError(e)
  }
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = undefined
  }
}

async function checkBound() {
  if (checking || phase.value !== 'waiting') return
  checking = true
  try {
    const res = await storageBindingApi.list()
    const bound = (res.bindings || []).some(
      (b) => b.provider === 'drive' && b.status === 'active',
    )
    if (bound) {
      stopPolling()
      phase.value = 'success'
      successTimer = setTimeout(() => {
        emit('bound')
        emit('close')
      }, 900)
      return
    }
  } catch {
    // 单次轮询失败不打断等待，下一轮再查
  } finally {
    checking = false
  }
  if (Date.now() >= waitDeadline) {
    stopPolling()
    phase.value = 'timeout'
  }
}

function startWaiting() {
  phase.value = 'waiting'
  waitDeadline = Date.now() + WAIT_TIMEOUT_MS
  stopPolling()
  pollTimer = setInterval(() => { void checkBound() }, POLL_INTERVAL_MS)
}

async function handleBind() {
  // 与设置页表单同一口径：共用名字输入框、走新应用 oauth2 start 口；
  // 无凭证时按钮已置灰，这里再兜一层后端 403 原样展示
  const name = label.value.trim()
  if (!name) {
    formError.value = '先给这个盘起个名字'
    return
  }
  formError.value = ''
  authUrl.value = ''
  const win = window.open('about:blank', '_blank')
  starting.value = true
  try {
    const res = await storageBindingApi.startDriveOAuth2(name)
    if (!res.authorization_url) {
      if (win && !win.closed) win.close()
      formError.value = '服务器没有返回授权地址，请稍后再重试'
      return
    }
    authUrl.value = res.authorization_url
    if (!win || win.closed) {
      formError.value =
        '弹窗被浏览器拦了，点下面“打开 Google 授权页”手动打开，同意后回到这里，绑定会自动完成'
    } else {
      win.location.href = res.authorization_url
      toast.success('已打开 Google 授权页，同意后回到这里，绑定会自动完成')
    }
    startWaiting()
  } catch (e) {
    if (win && !win.closed) win.close()
    formError.value = humanizeError(e)
  } finally {
    starting.value = false
  }
}

function onFocusReturn() {
  // 授权在新窗口完成、用户切回来时立即补查一次，不等下一轮轮询
  if (phase.value === 'waiting') void checkBound()
}
function onVisibilityReturn() {
  if (document.visibilityState === 'visible') onFocusReturn()
}

function reset() {
  stopPolling()
  if (successTimer) {
    clearTimeout(successTimer)
    successTimer = undefined
  }
  phase.value = 'form'
  formError.value = ''
  authUrl.value = ''
  starting.value = false
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}
watch(
  () => props.open,
  (open) => {
    if (open) {
      reset()
      void loadEntry()
      document.addEventListener('keydown', onKeydown)
      window.addEventListener('focus', onFocusReturn)
      document.addEventListener('visibilitychange', onVisibilityReturn)
    } else {
      reset()
      document.removeEventListener('keydown', onKeydown)
      window.removeEventListener('focus', onFocusReturn)
      document.removeEventListener('visibilitychange', onVisibilityReturn)
    }
  },
)
onBeforeUnmount(() => {
  reset()
  document.removeEventListener('keydown', onKeydown)
  window.removeEventListener('focus', onFocusReturn)
  document.removeEventListener('visibilitychange', onVisibilityReturn)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="bds-overlay"
      :class="{ 'bds--dark': dark }"
      @click.self="emit('close')"
    >
      <div class="bds-card" role="dialog" aria-modal="true" aria-label="绑定谷歌盘">
        <div class="bds-head">
          <h3 class="bds-title">绑定谷歌盘</h3>
          <button type="button" class="bds-close" aria-label="关闭" @click="emit('close')">
            <X :size="18" aria-hidden="true" />
          </button>
        </div>

        <!-- 表单态：说明＋起名＋绑定钮（口径照设置页绑定表单） -->
        <template v-if="phase === 'form'">
          <p class="bds-desc">
            绑了自己的谷歌盘，播放就走你自己的额度，不走共享池；没绑定只能浏览、不能点播。
          </p>

          <div v-if="providersError" class="bds-error" role="alert">
            <span>{{ providersError }}</span>
            <button type="button" class="bds-retry" @click="loadEntry">重试</button>
          </div>
          <template v-else-if="oauth2 && oauth2.available">
            <p v-if="oauth2.description" class="bds-tip">{{ oauth2.description }}</p>
            <div class="bds-field">
              <label class="bds-label" for="bds-drive-label">给这个盘起个名字</label>
              <input
                id="bds-drive-label"
                v-model="label"
                type="text"
                placeholder="比如：我的主力盘"
                autocomplete="off"
              />
            </div>
            <p v-if="!oauth2.eligible && oauth2.reason" class="bds-error" role="alert">
              {{ oauth2.reason }}
            </p>
            <p v-if="formError" class="bds-error" role="alert">{{ formError }}</p>
            <UhdButton
              variant="primary"
              block
              :dark="dark"
              :disabled="!canBind || starting"
              @click="handleBind"
            >
              {{ starting ? '正在打开授权…' : '绑定谷歌盘' }}
            </UhdButton>
            <p class="bds-tip">
              点绑定后会在新窗口打开 Google 授权页，点同意后关掉那个窗口、回到这里，绑定会自动完成。授权页本身不是本站页面，账号密码只交给 Google。
            </p>
          </template>
          <p v-else-if="oauth2" class="bds-desc">绑定入口暂时不可用，请稍后再试。</p>
          <p v-else class="bds-loading">
            <span class="bds-spin" aria-hidden="true"></span>正在加载…
          </p>
        </template>

        <!-- 等待态：授权在新窗口进行，轮询绑定列表等结果 -->
        <template v-else-if="phase === 'waiting'">
          <p class="bds-waiting">
            <span class="bds-spin" aria-hidden="true"></span>
            正在等授权完成…在 Google 授权页点同意后回到这里，绑定会自动完成。
          </p>
          <p v-if="formError" class="bds-error" role="alert">{{ formError }}</p>
          <p v-if="authUrl" class="bds-tip">
            点这里打开 Google 授权页：<a :href="authUrl" target="_blank" rel="noopener">打开 Google 授权页</a>，同意后关掉那个窗口、回到这里。
          </p>
        </template>

        <!-- 成功态：打勾提示，随即自动关弹层并通知父级刷新 -->
        <template v-else-if="phase === 'success'">
          <p class="bds-success">
            <span class="bds-success__icon"><Check :size="22" aria-hidden="true" /></span>
            绑定成功，这个盘可以用了。
          </p>
        </template>

        <!-- 超时态：停轮询，诚实提示关掉重来 -->
        <template v-else>
          <p class="bds-desc">
            等了几分钟还没等到绑定。已经在 Google 点了同意的话，关掉这个弹层再打开就会生效；还没同意，可以关掉重来。
          </p>
          <UhdButton variant="secondary" block :dark="dark" @click="emit('close')">
            关闭
          </UhdButton>
        </template>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* 壳与 ServerLinesSheet 同一套模具数值（说明书 2.8：遮罩黑 40%＋
   模糊 6px、卡 max 400、圆角 24、标题 24、右上 × 关闭）；--bds-*
   主题变量定义在同目录 bind-drive-sheet.css（契约只认被引入的
   .css），本块只引用。表单元素数值照设置页绑定表单（输入 46 高 /
   圆角 --uhd-radius-input、错误=浅红底圆角块）。 */
.bds-overlay {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  animation: uhd-fade-in var(--uhd-dur-fade) var(--uhd-ease-standard);
  font-family: var(--uhd-font-sans);
  -webkit-font-smoothing: antialiased;
}
.bds-card {
  position: relative;
  width: min(400px, calc(100vw - 32px));
  max-height: calc(100dvh - 48px);
  overflow-y: auto;
  box-sizing: border-box;
  background: var(--bds-card);
  color: var(--bds-text);
  border-radius: var(--uhd-radius-modal);
  box-shadow: var(--uhd-shadow-modal);
  padding: 28px;
  animation: uhd-fade-in var(--uhd-dur-fade) var(--uhd-ease-standard);
}
.bds-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.bds-title {
  margin: 0;
  font-size: var(--uhd-fs-h3);
  font-weight: var(--uhd-fw-bold);
  letter-spacing: -0.01em;
  color: var(--bds-text);
}
.bds-close {
  flex: none;
  width: 30px;
  height: 30px;
  min-height: 30px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--bds-sub);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.bds-close:hover {
  background: var(--bds-link-hover);
}
.bds-desc {
  margin: 16px 0 0;
  font-size: var(--uhd-fs-caption);
  line-height: 1.7;
  color: var(--bds-sub);
}
.bds-field {
  margin-top: 16px;
}
.bds-label {
  display: block;
  margin-bottom: 8px;
  font-size: var(--uhd-fs-caption);
  color: var(--bds-sub);
}
.bds-field input {
  width: 100%;
  height: 46px;
  padding: 0 14px;
  background: var(--bds-input-bg);
  border: var(--uhd-border-w) solid var(--bds-input-border);
  border-radius: var(--uhd-radius-input);
  color: var(--bds-text);
  font-size: 16px;
  outline: none;
  box-sizing: border-box;
}
.bds-field input:focus {
  border-color: var(--bds-sub);
}
.bds-error {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin: 12px 0 0;
  padding: 10px 14px;
  background: var(--bds-error-bg);
  border-radius: 12px;
  color: var(--bds-error-text);
  font-size: var(--uhd-fs-aux);
  line-height: 1.6;
}
.bds-retry {
  flex: none;
  height: 30px;
  min-height: 30px;
  padding: 0 14px;
  background: transparent;
  border: var(--uhd-border-w) solid currentColor;
  border-radius: 999px;
  color: inherit;
  font-size: var(--uhd-fs-aux);
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
}
.bds-tip {
  margin: 12px 0 0;
  font-size: var(--uhd-fs-aux);
  line-height: 1.6;
  color: var(--bds-sub);
}
.bds-tip a {
  color: var(--bds-text);
  font-weight: var(--uhd-fw-semibold);
  text-decoration: underline;
  text-underline-offset: 3px;
}
.bds-loading,
.bds-waiting {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 16px 0 0;
  font-size: var(--uhd-fs-caption);
  line-height: 1.6;
  color: var(--bds-sub);
}
.bds-spin {
  width: 18px;
  height: 18px;
  flex: none;
  border-radius: 50%;
  border: 2px solid var(--bds-divider);
  border-top-color: var(--bds-sub);
  animation: uhd-spin 0.8s linear infinite;
}
.bds-success {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 20px 0 4px;
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  color: var(--bds-text);
}
.bds-success__icon {
  flex: none;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: 2px solid var(--bds-ok);
  color: var(--bds-ok);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.bds-card :deep(.uhd-btn) {
  margin-top: 16px;
}
</style>
