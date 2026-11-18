<template>
  <div class="pc-entry totp-panel">
    <!-- 动态验证码行（登录与恢复组）：名＋副文＋状态胶囊，点行展开设置/关闭流程 -->
    <button type="button" class="sec-rowbtn" :aria-expanded="totpOpen" @click="totpOpen = !totpOpen">
      <span class="sec-rowbtn-ic" aria-hidden="true"><ShieldCheck :size="22" /></span>
      <span class="sec-rowbtn-main">
        <span class="sec-rowbtn-name">动态验证码</span>
        <span class="sec-rowbtn-sub">密码登录时使用的 6 位动态码</span>
      </span>
      <span class="sec-capsule">{{ totpEnabled ? '已启用' : '启用' }}</span>
      <ChevronDown :size="18" class="sec-rowbtn-chev" :class="{ open: totpOpen }" aria-hidden="true" />
    </button>
    <div v-if="totpOpen" class="pc-expand">
      <template v-if="!totpEnabled">
        <p class="sec-note">
          启用后，登录时除了密码还要输入验证器 App（Google Authenticator、Microsoft
          Authenticator 等）里的 6 位动态码。用 App 扫下方二维码，或手动输入密钥，然后填一次码确认启用。
        </p>
        <div v-if="!totpSetupData" class="sec-actions">
          <button type="button" class="v4-mini-btn" :disabled="busy" @click="startTotpSetup">
            {{ busy ? '生成中…' : '生成密钥与二维码' }}
          </button>
        </div>
        <template v-else>
          <div class="sec-qr"><canvas ref="qrCanvas"></canvas></div>
          <p class="sec-secret">密钥（手动输入）：<code>{{ totpSetupData.secret }}</code></p>
          <div class="sec-form-row">
            <input
              v-model="totpCodeInput"
              class="sec-input"
              inputmode="numeric"
              autocomplete="one-time-code"
              maxlength="6"
              placeholder="输入 App 里的 6 位码"
            />
            <button type="button" class="v4-mini-btn" :disabled="busy || totpCodeInput.length < 6" @click="confirmTotpEnable">
              {{ busy ? '确认中…' : '确认启用' }}
            </button>
          </div>
        </template>
      </template>
      <template v-else>
        <p class="sec-note">已启用。登录时需要输入验证器 App 的 6 位动态码。关闭需要验证登录密码。</p>
        <div class="sec-form-row">
          <input
            v-model="totpDisablePassword"
            class="sec-input"
            type="password"
            autocomplete="current-password"
            placeholder="输入登录密码"
          />
          <button type="button" class="v4-mini-btn danger" :disabled="busy || !totpDisablePassword" @click="disableTotp">
            {{ busy ? '处理中…' : '关闭动态验证码' }}
          </button>
        </div>
      </template>
      <p v-if="totpError" class="sec-err">{{ totpError }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 动态验证码（TOTP）面板：自原 SecurityTrioPanel 拆分（2026-11-18 分组
 * 对齐批），行形态照友站账号安全页「登录与恢复」组——名＋副文＋状态
 * 胶囊钮；展开内的设置/关闭流程与接口口径原样保留，行为不变。
 * overview 数据与 Passkeys/ApiKeys 面板共享（useSecurityOverview）。
 */
import { computed, nextTick, onMounted, ref } from 'vue'
import { ChevronDown, ShieldCheck } from 'lucide-vue-next'
import { securityApi } from '@/api/security'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'
import { useSecurityOverview } from './useSecurityOverview'

const toast = useToast()
const { overview, busy, loadOverview } = useSecurityOverview()

const totpEnabled = computed(() => overview.value?.totp.enabled ?? false)

const totpOpen = ref(false)
const totpSetupData = ref<{ secret: string; otpauth_uri: string } | null>(null)
const totpCodeInput = ref('')
const totpDisablePassword = ref('')
const totpError = ref('')
const qrCanvas = ref<HTMLCanvasElement | null>(null)

async function startTotpSetup() {
  if (busy.value) return
  busy.value = true
  totpError.value = ''
  try {
    totpSetupData.value = await securityApi.totpSetup()
    await nextTick()
    if (qrCanvas.value && totpSetupData.value) {
      // 动态导入浏览器构建：避开 @types/qrcode 的 node 类型污染（见 types/qrcode-browser.d.ts）
      const QRCode = (await import('qrcode/lib/browser.js')).default
      await QRCode.toCanvas(qrCanvas.value, totpSetupData.value.otpauth_uri, { width: 176, margin: 1 })
    }
  } catch (e) {
    totpError.value = humanizeError(e)
  } finally {
    busy.value = false
  }
}

async function confirmTotpEnable() {
  if (busy.value) return
  busy.value = true
  totpError.value = ''
  try {
    await securityApi.totpEnable(totpCodeInput.value.trim())
    toast.success('动态验证码已启用')
    totpSetupData.value = null
    totpCodeInput.value = ''
    await loadOverview()
  } catch (e) {
    totpError.value = humanizeError(e)
  } finally {
    busy.value = false
  }
}

async function disableTotp() {
  if (busy.value) return
  busy.value = true
  totpError.value = ''
  try {
    await securityApi.totpDisable(totpDisablePassword.value)
    toast.success('动态验证码已关闭')
    totpDisablePassword.value = ''
    await loadOverview()
  } catch (e) {
    totpError.value = humanizeError(e)
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  void loadOverview()
})
</script>

<style scoped>
/* 行形态与 PcEntryRow 同模（图标 22 + 名 16/600），多一行副文与状态胶囊 */
.sec-rowbtn {
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
  min-height: 68px;
  padding: 10px 2px;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--uhd-l-text);
  font: inherit;
  font-family: var(--uhd-font-sans);
  text-align: left;
  box-sizing: border-box;
}
.sec-rowbtn:active { background: var(--uhd-l-input-bg); }
.sec-rowbtn-ic {
  width: 22px;
  height: 22px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--uhd-l-text);
}
.sec-rowbtn-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.sec-rowbtn-name { font-size: var(--uhd-fs-lead); font-weight: var(--uhd-fw-semibold); white-space: nowrap; }
.sec-rowbtn-sub {
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sec-capsule {
  flex: none;
  display: inline-flex;
  align-items: center;
  min-height: 36px;
  padding: 0 16px;
  border-radius: var(--uhd-radius-pill);
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  background: var(--uhd-l-surface);
  color: var(--uhd-l-text);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  white-space: nowrap;
}
.sec-rowbtn-chev { flex: none; color: var(--uhd-l-text-3); transition: transform var(--uhd-dur-ui) var(--uhd-ease-standard); }
.sec-rowbtn-chev.open { transform: rotate(180deg); }
.pc-expand {
  border-top: 1px dashed var(--line);
  padding: 12px 14px 14px;
}
.sec-note {
  margin: 6px 0 10px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--ink2);
}
.sec-err {
  margin: 8px 0 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--red);
}
.sec-actions { display: flex; gap: 8px; }
.sec-form-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  flex-wrap: wrap;
}
.sec-input {
  flex: 1;
  min-width: 180px;
  min-height: 40px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--paper-r-sm, 8px);
  background: var(--paper-card);
  color: var(--ink);
  font: inherit;
  font-size: 14px;
}
.sec-qr { display: flex; justify-content: center; padding: 8px 0; }
.sec-qr canvas {
  background: #fff;
  border-radius: 8px;
  padding: 6px;
}
.sec-secret {
  margin: 4px 0;
  font-size: 12.5px;
  color: var(--ink2);
  text-align: center;
  word-break: break-all;
}
.sec-secret code { font-size: 13px; color: var(--ink); letter-spacing: 0.5px; }
.v4-mini-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 10px 14px;
  background: var(--paper2);
  border: 1px solid var(--line);
  border-radius: var(--paper-r-full, 999px);
  color: var(--ink);
  font-size: 13px;
  cursor: pointer;
}
.v4-mini-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.v4-mini-btn.danger { color: var(--red); }
</style>
