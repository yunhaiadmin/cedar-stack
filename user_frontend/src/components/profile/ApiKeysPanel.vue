<template>
  <div class="pc-entry api-keys-panel">
    <!-- 组头创建胶囊（活跃会话组）：点开展开创建表单与已有 Key 列表 -->
    <div class="ak-head">
      <button
        type="button"
        class="ak-capsule"
        :aria-expanded="panelOpen"
        @click="panelOpen = !panelOpen"
      >
        <KeyRound :size="15" aria-hidden="true" /> 创建 API Key
      </button>
      <span class="ak-count">{{ apiKeys.length }} 个</span>
    </div>
    <div v-if="panelOpen" class="sec-block">
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
 * 平台 API Key 面板：自原 SecurityTrioPanel 拆分（2026-11-18 分组对齐
 * 批），按友站形态移入设置页「活跃会话」组——组头胶囊钮「创建 API
 * Key」；创建前置（须先启用动态验证码或 Passkey）、有效期、一次性
 * 明文展示与撤销两步确认等口径原样保留，行为不变（前置门槛保留与否
 * 随活跃会话数据模型 G-60 一并定，本批不动）。
 * overview 数据与 Totp/Passkeys 面板共享（useSecurityOverview）。
 */
import { computed, onMounted, ref } from 'vue'
import { KeyRound } from 'lucide-vue-next'
import { securityApi, type PlatformApiKeyItem } from '@/api/security'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'
import { fmtSecurityDate as fmtDate, useSecurityOverview } from './useSecurityOverview'

const toast = useToast()
const { overview, busy, loadOverview } = useSecurityOverview()

const apiKeys = computed<PlatformApiKeyItem[]>(() => overview.value?.api_keys.items ?? [])
const apiKeyCanCreate = computed(() => overview.value?.api_keys.can_create ?? false)
const apiKeyValidityDays = computed(() => overview.value?.api_keys.validity_days ?? 365)

// 默认折叠：友站首屏只露创建胶囊（S-2），点开展开 Key 列表与创建表单；
// 此前默认展开（拆分前 Key 列表与创建表单常显），现按友站收起。
const panelOpen = ref(false)
const newKeyName = ref('')
const newKeyPlaintext = ref('')
const apiKeyError = ref('')
const pendingKeyDelete = ref<number | null>(null)

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
.ak-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 2px 4px;
}
.ak-capsule {
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
.ak-count {
  font-size: var(--ax-fs-caption);
  color: var(--ax-l-text-2);
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
