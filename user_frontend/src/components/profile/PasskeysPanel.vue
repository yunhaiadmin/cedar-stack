<template>
  <div class="pc-entry passkeys-panel">
    <!-- 组副文（Passkeys 独立组；上限走后端下发，缺省 5） -->
    <p class="pk-gsub">使用设备生物识别或安全密钥登录，最多可保存 {{ passkeyMax }} 枚。</p>

    <!-- 组头添加胶囊（满额隐藏，与原表单行口径一致） -->
    <div v-if="passkeys.length < passkeyMax" class="pk-head">
      <button
        type="button"
        class="pk-capsule"
        :disabled="busy || !webauthnOk"
        :aria-expanded="addOpen"
        @click="addOpen = !addOpen"
      >
        <Plus :size="15" aria-hidden="true" /> 添加 Passkey
      </button>
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

        <!-- 空状态四件套：指纹图标块＋粗体标题＋说明文＋专属钮 -->
        <div v-if="!passkeys.length" class="pk-empty">
          <span class="pk-empty-ic" aria-hidden="true"><Fingerprint :size="30" /></span>
          <p class="pk-empty-title">尚未添加 Passkey</p>
          <p class="pk-empty-sub">添加后可使用 Touch ID、Face ID 或安全密钥登录。</p>
          <button
            type="button"
            class="pk-capsule"
            :disabled="busy || !webauthnOk"
            :aria-expanded="addOpen"
            @click="addOpen = !addOpen"
          >
            添加第一枚 Passkey
          </button>
        </div>

        <!-- 添加表单：名称输入保留在展开区（组头胶囊/空状态钮共用） -->
        <div v-if="addOpen && passkeys.length < passkeyMax" class="sec-form-row">
          <input v-model="newPasskeyName" class="sec-input" maxlength="64" placeholder="给这枚钥匙起个名（可空）" />
          <button type="button" class="v4-mini-btn" :disabled="busy || !webauthnOk" @click="addPasskey">
            {{ busy ? '等待设备…' : '添加 Passkey' }}
          </button>
        </div>
        <p v-if="!webauthnOk" class="sec-note">当前浏览器/设备不支持 Passkey（需要 HTTPS 与设备生物识别或安全密钥）。</p>
        <p v-if="passkeyError" class="sec-err">{{ passkeyError }}</p>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * Passkeys 面板：自原 SecurityTrioPanel 拆分（2026-11-18 分组对齐批），
 * 升为设置页独立组——组副文＋组头添加胶囊＋四件套空状态照友站账号安全
 * 页；列表、删除两步确认、注册流程与接口口径原样保留，行为不变。
 * overview 数据与 Totp/ApiKeys 面板共享（useSecurityOverview）。
 */
import { computed, onMounted, ref } from 'vue'
import { Fingerprint, Plus } from 'lucide-vue-next'
import { securityApi, type PasskeyItem } from '@/api/security'
import { createPasskeyCredential, passkeySupported } from '@/utils/webauthn'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'
import { fmtSecurityDate as fmtDate, useSecurityOverview } from './useSecurityOverview'

const toast = useToast()
const { overview, loadError, busy, loadOverview } = useSecurityOverview()

const passkeys = computed<PasskeyItem[]>(() => overview.value?.passkeys.items ?? [])
const passkeyMax = computed(() => overview.value?.passkeys.max ?? 5)
const webauthnOk = computed(() => passkeySupported())

const addOpen = ref(false)
const newPasskeyName = ref('')
const passkeyError = ref('')
const pendingPasskeyDelete = ref<number | null>(null)

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
    addOpen.value = false
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

onMounted(() => {
  void loadOverview()
})
</script>

<style scoped>
.pk-gsub {
  margin: 0 0 6px;
  padding: 0 2px;
  font-size: var(--ax-fs-caption);
  line-height: 1.55;
  color: var(--ax-l-text-2);
}
.pk-head { padding: 2px 2px 4px; }
.pk-capsule {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 36px;
  padding: 0 16px;
  border-radius: var(--ax-radius-pill);
  border: var(--ax-border-w) solid var(--ax-l-border);
  background: var(--ax-l-surface);
  color: var(--ax-l-text);
  font-size: var(--ax-fs-caption);
  font-weight: var(--ax-fw-medium);
  white-space: nowrap;
  cursor: pointer;
}
.pk-capsule:disabled { opacity: 0.5; cursor: not-allowed; }
.pk-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 22px 12px 14px;
}
.pk-empty-ic {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 60px;
  height: 60px;
  border-radius: var(--ax-radius-card, 16px);
  border: var(--ax-border-w) solid var(--ax-l-border);
  background: var(--ax-l-input-bg);
  color: var(--ax-l-text-2);
}
.pk-empty-title {
  margin: 14px 0 0;
  font-size: var(--ax-fs-lead);
  font-weight: var(--ax-fw-bold, 700);
  color: var(--ax-l-text);
}
.pk-empty-sub {
  margin: 6px 0 16px;
  font-size: var(--ax-fs-caption);
  line-height: 1.55;
  color: var(--ax-l-text-2);
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
</style>
