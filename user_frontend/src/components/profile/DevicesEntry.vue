<template>
  <div class="pc-entry">
    <PcEntryRow
      :icon="Smartphone"
      name="我的设备"
      :meta="metaText"
      expandable
      :expanded="open"
      @click="open = !open"
    />
    <div v-if="open" class="pc-expand">
      <div class="dev-head">
        <span v-if="deviceInfo && deviceInfo.limit" class="dev-count">
          {{ deviceInfo.active_count ?? 0 }} / {{ deviceInfo.limit }} 台
        </span>
        <span v-else class="dev-count"></span>
        <button type="button" class="sx-mini-btn" title="刷新" :disabled="deviceLoading" :aria-busy="deviceLoading" @click="loadDevices">
          <RefreshCw :size="14" /> {{ deviceLoading ? '刷新中' : '刷新' }}
        </button>
      </div>
      <div v-if="deviceLoading && !devices.length" class="sx-loading"><span class="sx-spin" aria-hidden="true"></span>正在加载…</div>
      <div v-else-if="deviceError" class="sx-err">设备列表加载失败，请稍后重试<button type="button" class="sx-mini-btn" @click="loadDevices">重试</button></div>
      <div v-else-if="!devices.length" class="sx-empty">
        还没有设备记录 —— 用 Emby 客户端（Infuse / Forward 等）登录后会出现在这里
      </div>
      <template v-else>
        <div v-for="d in devices" :key="d.device_id" class="dev-row">
          <div class="dev-main">
            <span class="dev-name">{{ d.name || '未命名设备' }}</span>
            <span class="dev-meta">
              {{ d.client || '未知客户端' }}
              <template v-if="d.app_version"> v{{ d.app_version }}</template>
              · {{ deviceAgo(d.last_seen_at, d.is_blocked) }}
              <template v-if="d.ip"> · {{ d.ip }}</template>
            </span>
          </div>
          <span v-if="d.is_blocked" class="sx-status sx-status--bad"><span class="sx-dot" aria-hidden="true"></span>已禁用</span>
          <span v-else-if="!d.is_online_recent" class="sx-status sx-status--idle"><span class="sx-dot" aria-hidden="true"></span>已闲置</span>
          <span v-else class="sx-status sx-status--ok"><span class="sx-dot" aria-hidden="true"></span>活跃</span>
          <button
            type="button"
            class="dev-remove"
            :class="{ confirming: pendingRemove === d.device_id }"
            :disabled="removingDevice === d.device_id"
            @click="handleRemoveDevice(d)"
          >
            {{ removingDevice === d.device_id ? '移除中' : (pendingRemove === d.device_id ? '确认移除' : '移除') }}
          </button>
        </div>
      </template>
      <p class="dev-tip">
        <template v-if="deviceInfo && deviceInfo.limit">
          账号最多同时使用 {{ deviceInfo.limit }} 台设备（近 {{ deviceInfo.active_days }} 天内登录过）。
          {{ deviceInfo.auto_evict ? '超限时会自动停用最久未用的设备。' : '超限时请先移除不再使用的设备。' }}
        </template>
        <template v-else>移除后该设备登录状态立即失效，需重新输入账号密码。</template>
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 我的设备（自旧 AccountSettingsView 原样抽出）：设备列表 / 两步确认移除，
 * 顶上限自救通道。挂载即加载（入口行右侧小字要显示 N / M 台，属现有接口
 * 拿得到的摘要）；加载失败不装成"没设备"，展开里留错误态重试。
 */
import { computed, onMounted, ref } from 'vue'
import { RefreshCw, Smartphone } from 'lucide-vue-next'
import PcEntryRow from './PcEntryRow.vue'
import { deviceApi, type MyDevice, type MyDevicesResponse } from '@/api/economy'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'
import { toastCopy } from '@/constants/toastMessages'
import { toUserMessage } from '@/utils/toUserMessage'
import { parseServerTime } from '@/utils/serverTime'

const toast = useToast()
const open = ref(false)
const devices = ref<MyDevice[]>([])
const deviceInfo = ref<MyDevicesResponse | null>(null)
const deviceLoading = ref(false)
const deviceError = ref('')
const removingDevice = ref('')
const pendingRemove = ref('')

const metaText = computed(() => {
  if (!deviceInfo.value || !deviceInfo.value.limit) return ''
  return `${deviceInfo.value.active_count ?? 0} / ${deviceInfo.value.limit} 台`
})

async function loadDevices() {
  deviceLoading.value = true
  deviceError.value = ''
  try {
    const res = await deviceApi.mine()
    devices.value = res.devices
    deviceInfo.value = res
  } catch (e) {
    deviceError.value = humanizeError(e)
  } finally {
    deviceLoading.value = false
  }
}

async function handleRemoveDevice(device: MyDevice) {
  if (removingDevice.value) return
  if (pendingRemove.value !== device.device_id) {
    pendingRemove.value = device.device_id
    window.setTimeout(() => {
      if (pendingRemove.value === device.device_id) pendingRemove.value = ''
    }, 4000)
    return
  }
  pendingRemove.value = ''
  removingDevice.value = device.device_id
  try {
    const res = await deviceApi.remove(device.device_id)
    toast.success(res.message || toastCopy.deviceRemoved())
    await loadDevices()
  } catch (err: any) {
    toast.error(toUserMessage(err, 'removeFailed'))
  } finally {
    removingDevice.value = ''
  }
}

function deviceAgo(iso?: string | null, isBlocked = false) {
  if (isBlocked) return '已禁用'
  if (!iso) return '—'
  // PA-047：last_seen_at 是 UTC naive 串，须按 UTC 解析，否则时长多算 8 小时
  const diff = Date.now() - parseServerTime(iso).getTime()
  if (Number.isNaN(diff)) return '—'
  if (diff < 0) return '刚刚'
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}

onMounted(() => {
  void loadDevices()
})
</script>

<style scoped>
/* 展开面板与行：状态行模具（说明书 2.6）——1px --uhd-l-divider 分隔、
   状态=圆点+文字、操作=白底灰边小胶囊；加载态=旋转圈+灰字（1.6）、空态=虚线卡（2.7） */
.pc-expand {
  border-top: var(--uhd-border-w) solid var(--uhd-l-divider);
  padding: 14px 2px 18px;
  font-family: var(--uhd-font-sans);
}
.dev-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.dev-count {
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-l-text-2);
  font-variant-numeric: tabular-nums;
}
.sx-mini-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  min-height: 36px;
  padding: 0 16px;
  background: var(--uhd-l-surface);
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-pill);
  color: var(--uhd-l-text);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  cursor: pointer;
}
.sx-mini-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.sx-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 0;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
}
.sx-spin {
  width: 18px;
  height: 18px;
  flex: none;
  border-radius: 50%;
  border: 2px solid var(--uhd-l-divider);
  border-top-color: var(--uhd-l-text-2);
  animation: uhd-spin 0.8s linear infinite;
}
.sx-err {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 14px 16px;
  border: 1px dashed #d4d4d8;
  border-radius: 16px;
  font-size: var(--uhd-fs-caption);
  line-height: 1.6;
  color: var(--uhd-l-text-2);
}
.sx-empty {
  padding: 26px 20px;
  border: 1px dashed #d4d4d8;
  border-radius: 16px;
  text-align: center;
  font-size: var(--uhd-fs-body);
  line-height: 1.6;
  color: var(--uhd-l-text-2);
}
.dev-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 64px;
  padding: 10px 0;
}
.dev-row + .dev-row { border-top: var(--uhd-border-w) solid var(--uhd-l-divider); }
.dev-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.dev-name {
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-l-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dev-meta {
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-l-text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.sx-status {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  flex: none;
  font-size: var(--uhd-fs-caption);
  white-space: nowrap;
}
.sx-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.sx-status--ok { color: var(--uhd-success); }
.sx-status--ok .sx-dot { background: var(--uhd-dot-ok); }
.sx-status--idle { color: var(--uhd-l-text-2); }
.sx-status--idle .sx-dot { background: var(--uhd-dot-idle); }
.sx-status--bad { color: var(--uhd-danger); }
.sx-status--bad .sx-dot { background: var(--uhd-dot-bad); }
.dev-remove {
  flex: none;
  height: 36px;
  min-height: 36px;
  padding: 0 16px;
  background: var(--uhd-l-surface);
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-pill);
  color: var(--uhd-l-text);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  white-space: nowrap;
  cursor: pointer;
}
.dev-remove:disabled { opacity: 0.5; cursor: not-allowed; }
.dev-remove.confirming { color: var(--uhd-danger); border-color: var(--uhd-danger); font-weight: var(--uhd-fw-semibold); }
.dev-tip {
  margin: 12px 0 0;
  padding-top: 12px;
  border-top: var(--uhd-border-w) solid var(--uhd-l-divider);
  font-size: var(--uhd-fs-aux);
  line-height: 1.6;
  color: var(--uhd-l-text-2);
}
</style>
