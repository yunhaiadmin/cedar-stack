<template>
  <div class="pc-entry sec-trio">
    <!-- ── 动态验证码（登录与恢复） ─────────────────────────── -->
    <PcEntryRow
      :icon="ShieldCheck"
      name="动态验证码"
      :meta="totpEnabled ? '已启用' : '尚未启用'"
      expandable
      :expanded="totpOpen"
      @click="totpOpen = !totpOpen"
    />
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

    <!-- ── Passkeys ─────────────────────────────────────────── -->
    <div class="sec-subhead">
      <span class="sec-subhead-name"><Fingerprint :size="18" aria-hidden="true" /> Passkeys</span>
      <span class="sec-subhead-meta">{{ passkeys.length }} / {{ passkeyMax }} 枚</span>
    </div>
    <div class="sec-block">
      <div v-if="loadError" class="v4-err">{{ loadError }}<br><button class="v4-retry" @click="loadOverview">重试</button></div>
      <template v-else>
        <div v-for="pk in passkeys" :key="pk.id" class="sec-row">
          <div class="sec-main">
            <span class="sec-name">{{ pk.device_name }}</span>
            <span class="sec-meta">
              添加于 {{ fmtDate(pk.created_at) }}
              <template v-if="pk.last_used_at"> · 最近使用 {{ fmtDate(pk.last_used_at) }}</template>
            </span>
          </div>
          <button
            type="button"
            class="sec-remove"
            :class="{ confirming: pendingPasskeyDelete === pk.id }"
            :disabled="busy"
            @click="removePasskey(pk)"
          >
            {{ pendingPasskeyDelete === pk.id ? '确认删除' : '删除' }}
          </button>
        </div>
        <p v-if="!passkeys.length" class="sec-note">还没有 Passkey。用设备指纹/面容或安全密钥就能免密登录。</p>
        <div v-if="passkeys.length < passkeyMax" class="sec-form-row">
          <input v-model="newPasskeyName" class="sec-input" maxlength="64" placeholder="给这枚钥匙起个名（可空）" />
          <button type="button" class="v4-mini-btn" :disabled="busy || !webauthnOk" @click="addPasskey">
            {{ busy ? '等待设备…' : '添加 Passkey' }}
          </button>
        </div>
        <p v-if="!webauthnOk" class="sec-note">当前浏览器/设备不支持 Passkey（需要 HTTPS 与设备生物识别或安全密钥）。</p>
        <p v-if="passkeyError" class="sec-err">{{ passkeyError }}</p>
      </template>
    </div>

    <!-- ── 平台 API Key ─────────────────────────────────────── -->
    <div class="sec-subhead">
      <span class="sec-subhead-name"><KeyRound :size="18" aria-hidden="true" /> 平台 API Key</span>
      <span class="sec-subhead-meta">{{ apiKeys.length }} 个</span>
    </div>
    <div class="sec-block">
      <p class="sec-note">
        API Key 拥有平台接口权限（当前可调账户信息只读接口），请像密码一样保管。
        有效期 {{ apiKeyValidityDays }} 天，到期自动失效。
        <template v-if="!apiKeyCanCreate">创建前必须先启用动态验证码或添加一枚 Passkey。</template>
      </p>
      <div v-for="k in apiKeys" :key="k.id" class="sec-row">
        <div class="sec-main">
          <span class="sec-name">{{ k.name }}</span>
          <span class="sec-meta">
            创建 {{ fmtDate(k.created_at) }} · 到期 {{ fmtDate(k.expires_at) }}
            <template v-if="k.last_used_at"> · 最近使用 {{ fmtDate(k.last_used_at) }}</template>
            <template v-else> · 未使用</template>
          </span>
        </div>
        <button
          type="button"
          class="sec-remove"
          :class="{ confirming: pendingKeyDelete === k.id }"
          :disabled="busy"
          @click="revokeApiKey(k)"
        >
          {{ pendingKeyDelete === k.id ? '确认撤销' : '撤销' }}
        </button>
      </div>
      <div v-if="newKeyPlaintext" class="sec-once">
        <p class="sec-once-title">新 Key 已生成——只显示这一次，关掉就再也看不到：</p>
        <code class="sec-once-code">{{ newKeyPlaintext }}</code>
        <button type="button" class="v4-mini-btn" @click="copyNewKey">复制</button>
      </div>
      <div class="sec-form-row">
        <input
          v-model="newKeyName"
          class="sec-input"
          maxlength="64"
          placeholder="Key 名称（如：我的脚本）"
          :disabled="!apiKeyCanCreate"
        />
        <button type="button" class="v4-mini-btn" :disabled="busy || !apiKeyCanCreate" @click="createApiKey">
          {{ busy ? '创建中…' : '创建 API Key' }}
        </button>
      </div>
      <p v-if="apiKeyError" class="sec-err">{{ apiKeyError }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 账号安全三件套（2026-11-13 用户亲点，形态照 UHDNow 账号安全页）：
 * 动态验证码一行（登录与恢复）+ Passkeys 区块（上限走后端配置）+
 * 平台 API Key（创建按钮口径照友站活跃会话区；后端硬前置＝已绑 TOTP/Passkey）。
 * 数据一律走 securityApi 的 overview 一次拉齐，动作后局部刷新，不编造摘要。
 */
import { computed, nextTick, onMounted, ref } from 'vue'
import { Fingerprint, KeyRound, ShieldCheck } from 'lucide-vue-next'
import PcEntryRow from './PcEntryRow.vue'
import {
  securityApi,
  type PasskeyItem,
  type PlatformApiKeyItem,
  type SecurityOverview,
} from '@/api/security'
import { createPasskeyCredential, passkeySupported } from '@/utils/webauthn'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'
import { parseServerTime } from '@/utils/serverTime'

const toast = useToast()
const busy = ref(false)
const loadError = ref('')
const overview = ref<SecurityOverview | null>(null)

const totpEnabled = computed(() => overview.value?.totp.enabled ?? false)
const passkeys = computed<PasskeyItem[]>(() => overview.value?.passkeys.items ?? [])
const passkeyMax = computed(() => overview.value?.passkeys.max ?? 5)
const apiKeys = computed<PlatformApiKeyItem[]>(() => overview.value?.api_keys.items ?? [])
const apiKeyCanCreate = computed(() => overview.value?.api_keys.can_create ?? false)
const apiKeyValidityDays = computed(() => overview.value?.api_keys.validity_days ?? 365)
const webauthnOk = computed(() => passkeySupported())

// TOTP 状态
const totpOpen = ref(false)
const totpSetupData = ref<{ secret: string; otpauth_uri: string } | null>(null)
const totpCodeInput = ref('')
const totpDisablePassword = ref('')
const totpError = ref('')
const qrCanvas = ref<HTMLCanvasElement | null>(null)

// Passkey 状态
const newPasskeyName = ref('')
const passkeyError = ref('')
const pendingPasskeyDelete = ref<number | null>(null)

// API Key 状态
const newKeyName = ref('')
const newKeyPlaintext = ref('')
const apiKeyError = ref('')
const pendingKeyDelete = ref<number | null>(null)

function fmtDate(iso?: string | null): string {
  if (!iso) return '—'
  const d = parseServerTime(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
}

async function loadOverview() {
  loadError.value = ''
  try {
    overview.value = await securityApi.overview()
  } catch (e) {
    loadError.value = humanizeError(e)
  }
}

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

async function addPasskey() {
  if (busy.value) return
  busy.value = true
  passkeyError.value = ''
  try {
    const { options, challenge_token } = await securityApi.passkeyRegisterOptions()
    const credential = await createPasskeyCredential(options)
    await securityApi.passkeyRegisterVerify({
      credential,
      challenge_token,
      device_name: newPasskeyName.value.trim() || undefined,
    })
    toast.success('Passkey 已添加')
    newPasskeyName.value = ''
    await loadOverview()
  } catch (e: any) {
    // 用户在系统弹窗里取消时浏览器抛 NotAllowedError，不当成系统错误红字吓人
    passkeyError.value = e?.name === 'NotAllowedError'
      ? '已取消，或设备验证超时'
      : humanizeError(e)
  } finally {
    busy.value = false
  }
}

async function removePasskey(pk: PasskeyItem) {
  if (busy.value) return
  if (pendingPasskeyDelete.value !== pk.id) {
    pendingPasskeyDelete.value = pk.id
    window.setTimeout(() => {
      if (pendingPasskeyDelete.value === pk.id) pendingPasskeyDelete.value = null
    }, 4000)
    return
  }
  pendingPasskeyDelete.value = null
  busy.value = true
  try {
    await securityApi.passkeyDelete(pk.id)
    toast.success('Passkey 已删除')
    await loadOverview()
  } catch (e) {
    passkeyError.value = humanizeError(e)
  } finally {
    busy.value = false
  }
}

async function createApiKey() {
  if (busy.value) return
  busy.value = true
  apiKeyError.value = ''
  try {
    const res = await securityApi.apiKeyCreate(newKeyName.value.trim() || '未命名')
    newKeyPlaintext.value = res.plaintext
    newKeyName.value = ''
    await loadOverview()
  } catch (e) {
    apiKeyError.value = humanizeError(e)
  } finally {
    busy.value = false
  }
}

async function copyNewKey() {
  try {
    await navigator.clipboard.writeText(newKeyPlaintext.value)
    toast.success('已复制，请妥善保存')
  } catch {
    toast.error('复制失败，请手动选中复制')
  }
}

async function revokeApiKey(k: PlatformApiKeyItem) {
  if (busy.value) return
  if (pendingKeyDelete.value !== k.id) {
    pendingKeyDelete.value = k.id
    window.setTimeout(() => {
      if (pendingKeyDelete.value === k.id) pendingKeyDelete.value = null
    }, 4000)
    return
  }
  pendingKeyDelete.value = null
  busy.value = true
  try {
    await securityApi.apiKeyDelete(k.id)
    toast.success('API Key 已撤销')
    await loadOverview()
  } catch (e) {
    apiKeyError.value = humanizeError(e)
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  void loadOverview()
})
</script>

<style scoped>
.pc-expand {
  border-top: 1px dashed var(--line);
  padding: 12px 14px 14px;
}
.sec-subhead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 14px 4px;
  border-top: 1px solid var(--line);
}
.sec-subhead-name {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
}
.sec-subhead-meta {
  font-size: 12px;
  color: var(--ink2);
  font-variant-numeric: tabular-nums;
}
.sec-block { padding: 6px 14px 14px; }
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
.sec-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 0;
}
.sec-row + .sec-row { border-top: 1px solid var(--line); }
.sec-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.sec-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sec-meta {
  font-size: 12px;
  color: var(--ink2);
  font-variant-numeric: tabular-nums;
}
.sec-remove {
  flex-shrink: 0;
  padding: 10px;
  background: transparent;
  border: none;
  border-radius: var(--paper-r-sm, 8px);
  color: var(--ink2);
  font-size: 13px;
  white-space: nowrap;
  cursor: pointer;
}
.sec-remove.confirming { color: var(--red); font-weight: 600; }
.sec-remove:disabled { opacity: 0.5; cursor: not-allowed; }
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
.sec-once {
  margin: 10px 0;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--paper-r-sm, 8px);
  background: var(--paper2);
}
.sec-once-title { margin: 0 0 8px; font-size: 12.5px; color: var(--ink2); }
.sec-once-code {
  display: block;
  margin-bottom: 10px;
  font-size: 13px;
  word-break: break-all;
  color: var(--ink);
}
</style>
