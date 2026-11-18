<template>
  <div class="pc-entry active-sessions-panel">
    <PcEntryRow
      :icon="MonitorSmartphone"
      name="我的会话"
      :meta="metaText"
      expandable
      :expanded="open"
      @click="open = !open"
    />
    <div v-if="open" class="pc-expand">
      <p class="as-sub">{{ subText }}</p>
      <div class="as-filters" role="tablist" aria-label="会话筛选">
        <button
          v-for="f in filters"
          :key="f.key"
          type="button"
          role="tab"
          :aria-selected="activeFilter === f.key"
          class="as-filter"
          :class="{ 'is-on': activeFilter === f.key }"
          @click="activeFilter = f.key"
        >
          {{ f.label }} {{ f.count }}
        </button>
      </div>
      <div v-if="loading && !items.length" class="sx-loading"><span class="sx-spin" aria-hidden="true"></span>正在加载…</div>
      <div v-else-if="loadError" class="sx-err">会话列表加载失败，请稍后重试<button type="button" class="sx-mini-btn" @click="loadSessions">重试</button></div>
      <div v-else-if="!filtered.length" class="sx-empty">
        {{ activeFilter === 'all' ? '还没有活跃会话 —— 登录后会出现在这里' : '这一类暂无会话' }}
      </div>
      <template v-else>
        <div v-for="s in filtered" :key="s.id" class="as-row">
          <span class="as-ic" aria-hidden="true">
            <component :is="rowIcon(s)" :size="20" />
          </span>
          <div class="as-main">
            <div class="as-head">
              <span class="as-name">{{ rowName(s) }}</span>
              <span class="as-tag">{{ kindLabel(s.kind) }}</span>
              <span v-if="s.can_lock" class="as-lock" :class="{ 'is-locked': s.locked }">
                {{ s.locked ? '已锁定' : '未锁定' }}
              </span>
              <span v-if="s.is_blocked" class="sx-status sx-status--bad"><span class="sx-dot" aria-hidden="true"></span>已禁用</span>
            </div>
            <span class="as-meta">{{ rowMeta(s) }}</span>
          </div>
          <div class="as-ops">
            <button
              v-if="s.can_lock"
              type="button"
              class="sx-mini-btn"
              :disabled="actingId === s.id"
              @click="toggleLock(s)"
            >
              {{ s.locked ? '解锁' : '锁定' }}
            </button>
            <button type="button" class="sx-mini-btn" :disabled="actingId === s.id" @click="openRename(s)">编辑</button>
            <button
              type="button"
              class="sx-mini-btn as-del"
              :class="{ confirming: pendingDelete === s.id }"
              :disabled="actingId === s.id"
              @click="handleDelete(s)"
            >
              {{ actingId === s.id ? '处理中' : (pendingDelete === s.id ? '确认删除' : '删除') }}
            </button>
          </div>
        </div>
      </template>
      <p class="as-tip">
        超限时会自动淘汰最久未活动的未锁定会话；锁定的不会被新登录挤掉。删除会立即吊销该会话，网页会话会让那台设备重新登录。
      </p>
    </div>

    <!-- 改名小弹窗：网页会话改备注名、设备改设备名、Token 改 Key 名 -->
    <div v-if="renameTarget" class="as-modal-backdrop" @click.self="renameTarget = null">
      <div class="as-modal" role="dialog" aria-modal="true" aria-label="改名">
        <h3 class="as-modal-title">改名</h3>
        <input
          v-model="renameValue"
          type="text"
          class="as-modal-input"
          maxlength="100"
          placeholder="给这个会话起个名字"
          @keyup.enter="submitRename"
        />
        <div class="as-modal-ops">
          <button type="button" class="sx-mini-btn" @click="renameTarget = null">取消</button>
          <button type="button" class="sx-mini-btn" :disabled="actingId === renameTarget?.id || !renameValue.trim()" @click="submitRename">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 活跃会话合并列表（2026-11-18 UHDNow 差异定案 G-60/G-62~G-66）：
 * 网页登录（refresh 家族）＋媒体客户端设备＋弹幕 Token（平台 API Key）
 * 三类一表；筛选「全部／网页登录／弹幕 Token」带计数（媒体客户端只进全部）；
 * 行头＝平台图标＋系统/浏览器＋类型标签＋锁定虚线胶囊；最近活动/到期均
 * 绝对时间 YYYY-MM-DD HH:mm；锁定＝防淘汰标记、编辑＝改名、删除＝立即吊销。
 * 取代旧 DevicesEntry（设备行已并入本表，移除动作同口径保留）。
 */
import { computed, onMounted, ref } from 'vue'
import { KeyRound, Monitor, MonitorSmartphone, Smartphone } from 'lucide-vue-next'
import PcEntryRow from './PcEntryRow.vue'
import { securityApi, type ActiveSessionItem } from '@/api/security'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'
import { formatSessionTime, parseUserAgent, sessionFilterCounts } from '@/utils/userAgent'

const toast = useToast()
const open = ref(false)
const items = ref<ActiveSessionItem[]>([])
const limit = ref(0)
const total = ref(0)
const counts = ref({ all: 0, web: 0, danmaku: 0 })
const loading = ref(false)
const loadError = ref('')
const actingId = ref('')
const pendingDelete = ref('')
const renameTarget = ref<ActiveSessionItem | null>(null)
const renameValue = ref('')

type FilterKey = 'all' | 'web' | 'danmaku'
const activeFilter = ref<FilterKey>('all')

const metaText = computed(() => {
  if (!counts.value.all && !limit.value) return ''
  return limit.value
    ? `${total.value} / ${limit.value} 个`
    : `${total.value} 个`
})
const subText = computed(() =>
  limit.value ? `合计最多 ${limit.value} 个` : '当前不限制会话数量',
)
const filters = computed(() => [
  { key: 'all' as FilterKey, label: '全部', count: counts.value.all },
  { key: 'web' as FilterKey, label: '网页登录', count: counts.value.web },
  { key: 'danmaku' as FilterKey, label: '弹幕 Token', count: counts.value.danmaku },
])
const filtered = computed(() => {
  if (activeFilter.value === 'web') return items.value.filter((it) => it.kind === 'web')
  if (activeFilter.value === 'danmaku') return items.value.filter((it) => it.kind === 'danmaku')
  return items.value
})

async function loadSessions() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await securityApi.sessionsList()
    items.value = res.items
    limit.value = res.limit
    total.value = res.total
    counts.value = sessionFilterCounts(res.items)
  } catch (e) {
    loadError.value = humanizeError(e)
  } finally {
    loading.value = false
  }
}

function kindLabel(kind: ActiveSessionItem['kind']) {
  return kind === 'web' ? '网页登录' : kind === 'device' ? '媒体客户端' : '弹幕 Token'
}

function rowIcon(s: ActiveSessionItem) {
  if (s.kind === 'danmaku') return KeyRound
  if (s.kind === 'device') return Smartphone
  return parseUserAgent(s.user_agent).kind === 'mobile' ? Smartphone : Monitor
}

function rowName(s: ActiveSessionItem) {
  if (s.display_name) return s.display_name
  if (s.kind === 'web') {
    const ua = parseUserAgent(s.user_agent)
    return `${ua.os} · ${ua.browser}`
  }
  if (s.kind === 'device') return '未命名设备'
  return '未命名 Token'
}

function rowMeta(s: ActiveSessionItem) {
  const parts: string[] = []
  if (s.kind === 'web') {
    const ua = parseUserAgent(s.user_agent)
    parts.push(`${ua.os} / ${ua.browser}`)
  } else if (s.kind === 'device') {
    parts.push(s.client ? `${s.client}${s.app_version ? ` v${s.app_version}` : ''}` : '未知客户端')
  } else {
    parts.push('填入播放器的弹幕设置使用')
  }
  if (s.ip) parts.push(s.ip)
  parts.push(`最近活动 ${formatSessionTime(s.last_seen_at)}`)
  parts.push(s.expires_at ? `到期 ${formatSessionTime(s.expires_at)}` : '到期 长期有效')
  return parts.join(' · ')
}

async function toggleLock(s: ActiveSessionItem) {
  if (actingId.value) return
  actingId.value = s.id
  try {
    const res = await securityApi.sessionLock(s.id, !s.locked)
    s.locked = res.locked
    toast.success(res.locked ? '已锁定，不会被新登录挤掉' : '已解锁')
  } catch (e) {
    toast.error(humanizeError(e))
  } finally {
    actingId.value = ''
  }
}

function openRename(s: ActiveSessionItem) {
  renameTarget.value = s
  renameValue.value = s.display_name || ''
}

async function submitRename() {
  const target = renameTarget.value
  const name = renameValue.value.trim()
  if (!target || !name) return
  actingId.value = target.id
  try {
    const res = await securityApi.sessionRename(target.id, name)
    target.display_name = res.display_name
    toast.success('已改名')
    renameTarget.value = null
  } catch (e) {
    toast.error(humanizeError(e))
  } finally {
    actingId.value = ''
  }
}

async function handleDelete(s: ActiveSessionItem) {
  if (actingId.value) return
  if (pendingDelete.value !== s.id) {
    pendingDelete.value = s.id
    window.setTimeout(() => {
      if (pendingDelete.value === s.id) pendingDelete.value = ''
    }, 4000)
    return
  }
  pendingDelete.value = ''
  actingId.value = s.id
  try {
    await securityApi.sessionRevoke(s.id)
    toast.success('已吊销该会话')
    await loadSessions()
  } catch (e) {
    toast.error(humanizeError(e))
  } finally {
    actingId.value = ''
  }
}

onMounted(() => {
  void loadSessions()
})
</script>

<style scoped>
/* 行模具与状态行同源（说明书 2.6）：展开面板＋1px 分隔＋白底灰边小胶囊；
   锁定胶囊用虚线边框区分于类型标签（G-63）。 */
.pc-expand {
  border-top: var(--uhd-border-w) solid var(--uhd-l-divider);
  padding: 14px 2px 18px;
  font-family: var(--uhd-font-sans);
}
.as-sub {
  margin: 0 0 10px;
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-l-text-2);
}
.as-filters {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
}
.as-filter {
  min-height: 36px;
  padding: 0 16px;
  border-radius: var(--uhd-radius-pill);
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  background: var(--uhd-l-surface);
  color: var(--uhd-l-text);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  cursor: pointer;
  font-variant-numeric: tabular-nums;
}
.as-filter.is-on {
  background: var(--uhd-l-text);
  border-color: var(--uhd-l-text);
  color: var(--uhd-l-surface);
}
.as-row {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 64px;
  padding: 10px 0;
}
.as-row + .as-row { border-top: var(--uhd-border-w) solid var(--uhd-l-divider); }
.as-ic {
  width: 22px;
  height: 22px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--uhd-l-text);
}
.as-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.as-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
}
.as-name {
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-l-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.as-tag {
  flex: none;
  padding: 2px 8px;
  border-radius: var(--uhd-radius-pill);
  background: var(--uhd-l-input-bg);
  color: var(--uhd-l-text-2);
  font-size: var(--uhd-fs-aux);
  font-weight: var(--uhd-fw-medium);
  white-space: nowrap;
}
.as-lock {
  flex: none;
  padding: 2px 8px;
  border-radius: var(--uhd-radius-pill);
  border: 1px dashed var(--uhd-l-border);
  color: var(--uhd-l-text-2);
  font-size: var(--uhd-fs-aux);
  font-weight: var(--uhd-fw-medium);
  white-space: nowrap;
}
.as-lock.is-locked {
  border-style: solid;
  border-color: var(--uhd-l-text);
  color: var(--uhd-l-text);
}
.as-meta {
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-l-text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.as-ops {
  display: flex;
  gap: 8px;
  flex: none;
}
.as-del.confirming {
  color: var(--uhd-danger);
  border-color: var(--uhd-danger);
  font-weight: var(--uhd-fw-semibold);
}
.as-tip {
  margin: 12px 0 0;
  padding-top: 12px;
  border-top: var(--uhd-border-w) solid var(--uhd-l-divider);
  font-size: var(--uhd-fs-aux);
  line-height: 1.6;
  color: var(--uhd-l-text-2);
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
.sx-status {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  flex: none;
  font-size: var(--uhd-fs-caption);
  white-space: nowrap;
}
.sx-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.sx-status--bad { color: var(--uhd-danger); }
.sx-status--bad .sx-dot { background: var(--uhd-dot-bad); }
/* 改名小弹窗：居中浮层＋半透明遮罩 */
.as-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(0, 0, 0, 0.45);
}
.as-modal {
  width: 100%;
  max-width: 360px;
  padding: 20px;
  border-radius: 20px;
  background: var(--uhd-l-surface);
  box-shadow: var(--uhd-shadow-seg);
}
.as-modal-title {
  margin: 0 0 12px;
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-l-text);
}
.as-modal-input {
  width: 100%;
  height: 44px;
  padding: 0 14px;
  border-radius: 12px;
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  background: var(--uhd-l-input-bg);
  color: var(--uhd-l-text);
  font-size: var(--uhd-fs-body);
  box-sizing: border-box;
}
.as-modal-ops {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}
</style>
