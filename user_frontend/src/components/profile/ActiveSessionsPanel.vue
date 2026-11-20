<template>
  <!-- S-2（2026-11-18 用户中心自查）：照友站账号安全页平铺——组副文案
       ＋创建 API Key 胶囊（#api-keys 插槽，由父页传入 ApiKeysPanel）＋
       筛选胶囊＋会话行全部首屏可见，「我的会话」手风琴已摘除 -->
  <div class="pc-entry active-sessions-panel">
    <p class="as-sub">{{ subText }}</p>
    <slot name="api-keys" />
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
      <!-- S-3：行＝平台图标＋首行（平台名＋类型标签＋锁定虚线胶囊）＋
           IP／最近活动／到期各占一行；操作移到底部右侧：锁定文字钮＋
           铅笔／垃圾桶图标钮，390 宽下设备名不截断 -->
      <div v-for="s in filtered" :key="s.id" class="as-row">
        <div class="as-row-top">
          <span class="as-ic" aria-hidden="true">
            <component :is="rowIcon(s)" :size="20" />
          </span>
          <div class="as-main">
            <div class="as-head">
              <span class="as-name">{{ rowName(s) }}</span>
              <span class="as-tag">{{ kindLabel(s.kind) }}</span>
              <span v-if="s.can_lock" class="as-lock" :class="{ 'is-locked': s.locked }">
                <component :is="s.locked ? Lock : LockOpen" :size="12" aria-hidden="true" />{{ s.locked ? '已锁定' : '未锁定' }}
              </span>
              <span v-if="s.is_blocked" class="sx-status sx-status--bad"><span class="sx-dot" aria-hidden="true"></span>已禁用</span>
            </div>
            <span v-for="line in rowMetaLines(s)" :key="line" class="as-meta">{{ line }}</span>
          </div>
        </div>
        <div class="as-ops">
          <button
            v-if="s.can_lock"
            type="button"
            class="as-op-text"
            :disabled="actingId === s.id"
            @click="toggleLock(s)"
          >
            <component :is="s.locked ? Lock : LockOpen" :size="15" aria-hidden="true" />{{ s.locked ? '解锁' : '锁定' }}
          </button>
          <button type="button" class="as-op-ic" :disabled="actingId === s.id" aria-label="改名" @click="openRename(s)">
            <Pencil :size="16" aria-hidden="true" />
          </button>
          <button
            type="button"
            class="as-op-ic as-del"
            :class="{ confirming: pendingDelete === s.id }"
            :disabled="actingId === s.id"
            :aria-label="pendingDelete === s.id ? '确认删除' : '删除'"
            @click="handleDelete(s)"
          >
            <Trash2 v-if="pendingDelete !== s.id" :size="16" aria-hidden="true" />
            <span v-else>确认删除</span>
          </button>
        </div>
      </div>
    </template>
    <p class="as-tip">
      删除会立即吊销该会话，网页会话会让那台设备重新登录。
    </p>

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
 * 活跃会话合并列表（2026-11-18 参考站 差异定案 G-60/G-62~G-66）：
 * 网页登录（refresh 家族）＋媒体客户端设备＋弹幕 Token（平台 API Key）
 * 三类一表；筛选「全部／网页登录／弹幕 Token」带计数（媒体客户端只进全部）；
 * 行头＝平台图标＋系统/浏览器＋类型标签＋锁定虚线胶囊；最近活动/到期均
 * 绝对时间 YYYY-MM-DD HH:mm；锁定＝防淘汰标记、编辑＝改名、删除＝立即吊销。
 * 取代旧 DevicesEntry（设备行已并入本表，移除动作同口径保留）。
 */
import { computed, onMounted, ref } from 'vue'
import { KeyRound, Lock, LockOpen, Monitor, Pencil, Smartphone, Trash2 } from 'lucide-vue-next'
import { securityApi, type ActiveSessionItem } from '@/api/security'
import { useToast } from '@/composables/useToast'
import { humanizeError } from '@/composables/usePanelApi'
import { formatSessionTime, parseUserAgent, sessionFilterCounts } from '@/utils/userAgent'

const toast = useToast()
const items = ref<ActiveSessionItem[]>([])
const limit = ref(0)
const counts = ref({ all: 0, web: 0, danmaku: 0 })
const loading = ref(false)
const loadError = ref('')
const actingId = ref('')
const pendingDelete = ref('')
const renameTarget = ref<ActiveSessionItem | null>(null)
const renameValue = ref('')

type FilterKey = 'all' | 'web' | 'danmaku'
const activeFilter = ref<FilterKey>('all')

/* S-2：组副文案平铺（上限句＋淘汰／锁定语义，均取既有文案原句拼合，
   不另造新词）；不限量时只留不限量一句 */
const subText = computed(() =>
  limit.value
    ? `合计最多 ${limit.value} 个；超限时会自动淘汰最久未活动的未锁定会话；锁定的不会被新登录挤掉。`
    : '当前不限制会话数量',
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

/* S-3：IP／最近活动／到期各占一行。网页会话名已是「系统 / 浏览器」
   （友站同款），不再重复拼一行 UA；设备行保留客户端行、弹幕行保留
   用法提示行（友站行内无此两类，沿用既有文案原句）。 */
function rowMetaLines(s: ActiveSessionItem): string[] {
  const lines: string[] = []
  if (s.kind === 'device') {
    lines.push(s.client ? `${s.client}${s.app_version ? ` v${s.app_version}` : ''}` : '未知客户端')
  } else if (s.kind === 'danmaku') {
    lines.push('填入播放器的弹幕设置使用')
  }
  if (s.ip) lines.push(s.ip)
  lines.push(`最近活动 ${formatSessionTime(s.last_seen_at)}`)
  lines.push(s.expires_at ? `到期 ${formatSessionTime(s.expires_at)}` : '到期 长期有效')
  return lines
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
/* S-2 平铺面板：副文案＋筛选胶囊＋会话行直接展开，无手风琴外壳。
   行模具与状态行同源（说明书 2.6）：1px 分隔＋白底灰边小胶囊；
   锁定胶囊用虚线边框区分于类型标签（G-63）。 */
.as-sub {
  margin: 0 0 10px;
  font-size: var(--ax-fs-aux);
  line-height: 1.6;
  color: var(--ax-l-text-2);
}
.as-filters {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
}
/* 筛选胶囊照友站：选中＝深色实心胶囊，未选中＝无边框纯文字 */
.as-filter {
  min-height: 36px;
  padding: 0 12px;
  border: none;
  border-radius: var(--ax-radius-pill);
  background: none;
  color: var(--ax-l-text-2);
  font-size: var(--ax-fs-caption);
  font-weight: var(--ax-fw-medium);
  cursor: pointer;
  font-variant-numeric: tabular-nums;
}
.as-filter.is-on {
  padding: 0 16px;
  background: var(--ax-l-text);
  border-color: var(--ax-l-text);
  color: var(--ax-l-surface);
}
/* S-3 行：上部＝图标＋名/标签/锁定＋逐行 meta，下部＝操作行右对齐 */
.as-row {
  padding: 12px 0 10px;
}
.as-row + .as-row { border-top: var(--ax-border-w) solid var(--ax-l-divider); }
.as-row-top {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.as-ic {
  width: 22px;
  height: 22px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--ax-l-text);
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
  font-size: var(--ax-fs-body);
  font-weight: var(--ax-fw-semibold);
  color: var(--ax-l-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.as-tag {
  flex: none;
  padding: 2px 8px;
  border-radius: var(--ax-radius-pill);
  background: var(--ax-l-input-bg);
  color: var(--ax-l-text-2);
  font-size: var(--ax-fs-aux);
  font-weight: var(--ax-fw-medium);
  white-space: nowrap;
}
.as-lock {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 8px;
  border-radius: var(--ax-radius-pill);
  border: 1px dashed var(--ax-l-border);
  color: var(--ax-l-text-2);
  font-size: var(--ax-fs-aux);
  font-weight: var(--ax-fw-medium);
  white-space: nowrap;
}
.as-lock.is-locked {
  border-style: solid;
  border-color: var(--ax-l-text);
  color: var(--ax-l-text);
}
/* S-3：IP／最近活动／到期各占一行，不再拼一行截断 */
.as-meta {
  display: block;
  font-size: var(--ax-fs-aux);
  line-height: 1.6;
  color: var(--ax-l-text-2);
  font-variant-numeric: tabular-nums;
}
/* S-3：操作行——锁定文字钮＋铅笔／垃圾桶图标钮，右对齐在行底部 */
.as-ops {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  margin-top: 8px;
}
.as-op-text {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 36px;
  padding: 0 10px;
  background: none;
  border: none;
  border-radius: var(--ax-radius-pill);
  color: var(--ax-l-text);
  font-size: var(--ax-fs-caption);
  font-weight: var(--ax-fw-medium);
  cursor: pointer;
}
.as-op-ic {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-width: 36px;
  height: 36px;
  min-height: 36px;
  padding: 0 8px;
  background: none;
  border: none;
  border-radius: var(--ax-radius-pill);
  color: var(--ax-l-text-2);
  font-size: var(--ax-fs-caption);
  font-weight: var(--ax-fw-medium);
  cursor: pointer;
}
.as-op-text:disabled, .as-op-ic:disabled { opacity: 0.5; cursor: not-allowed; }
.as-op-ic.as-del.confirming {
  color: var(--ax-danger);
  font-weight: var(--ax-fw-semibold);
}
.as-tip {
  margin: 12px 0 0;
  padding-top: 12px;
  border-top: var(--ax-border-w) solid var(--ax-l-divider);
  font-size: var(--ax-fs-aux);
  line-height: 1.6;
  color: var(--ax-l-text-2);
}
.sx-mini-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  min-height: 36px;
  padding: 0 16px;
  background: var(--ax-l-surface);
  border: var(--ax-border-w) solid var(--ax-l-border);
  border-radius: var(--ax-radius-pill);
  color: var(--ax-l-text);
  font-size: var(--ax-fs-caption);
  font-weight: var(--ax-fw-medium);
  cursor: pointer;
}
.sx-mini-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.sx-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 0;
  font-size: var(--ax-fs-caption);
  color: var(--ax-l-text-2);
}
.sx-spin {
  width: 18px;
  height: 18px;
  flex: none;
  border-radius: 50%;
  border: 2px solid var(--ax-l-divider);
  border-top-color: var(--ax-l-text-2);
  animation: ax-spin 0.8s linear infinite;
}
.sx-err {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 14px 16px;
  border: 1px dashed #d4d4d8;
  border-radius: 16px;
  font-size: var(--ax-fs-caption);
  line-height: 1.6;
  color: var(--ax-l-text-2);
}
.sx-empty {
  padding: 26px 20px;
  border: 1px dashed #d4d4d8;
  border-radius: 16px;
  text-align: center;
  font-size: var(--ax-fs-body);
  line-height: 1.6;
  color: var(--ax-l-text-2);
}
.sx-status {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  flex: none;
  font-size: var(--ax-fs-caption);
  white-space: nowrap;
}
.sx-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.sx-status--bad { color: var(--ax-danger); }
.sx-status--bad .sx-dot { background: var(--ax-dot-bad); }
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
  background: var(--ax-l-surface);
  box-shadow: var(--ax-shadow-seg);
}
.as-modal-title {
  margin: 0 0 12px;
  font-size: var(--ax-fs-lead);
  font-weight: var(--ax-fw-semibold);
  color: var(--ax-l-text);
}
.as-modal-input {
  width: 100%;
  height: 44px;
  padding: 0 14px;
  border-radius: 12px;
  border: var(--ax-border-w) solid var(--ax-l-border);
  background: var(--ax-l-input-bg);
  color: var(--ax-l-text);
  font-size: var(--ax-fs-body);
  box-sizing: border-box;
}
.as-modal-ops {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}
</style>
