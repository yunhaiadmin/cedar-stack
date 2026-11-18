<script setup lang="ts">
/**
 * 线路管理（2026-11-19 用户亲点独立成页）：全站播放线路一页看完。
 *
 * 两层形态（照友站）：大类 = 服（如「国际线路」），每个大类下面挂
 * 多条线路（如一条「主线路」＋一条「副线路」）。本页按大类分组，
 * 每组列出该大类的全部线路——名称（主/副）、地址、已用/配额/剩余/
 * 状态；线路的增、删、改（名称/地址/配额/排序/启停/所属大类）与
 * 逐线重置用量都在这页办。改选所属大类 = 把线路挪到那个大类：
 * 线路 id 不变，流量记录与配额随线路走。服管理页只留线路摘要
 * 与到本页的入口。
 *
 * 配额口径（后端 backend/line_quota）：配额、用量、重置都按线路
 * 计；用量按请求实际进来的入口 host 归属（线路地址的 host 相同
 * 即归该线），匹配不到任何线路的流量不记线路账。用尽的线路在
 * 用户端显示「暂停服务」、新播放自动避开；全部线路用尽时自动
 * 回落正常分配，不断播。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Gauge, Pencil, Plus, RefreshCw, RotateCcw, Route, Trash2,
} from 'lucide-vue-next'
import { EmptyState, PageHeader, SectionCard, StatTile } from '@/components/ui'
import {
  createRealmLine, deleteRealmLine, resetRealmLineTraffic, updateRealmLine,
} from '@/api/admin'
import { useRealmStore } from '@/stores/realm'
import type { RealmLine, RealmRow } from '@/types'

const realm = useRealmStore()
const realms = computed(() => realm.realms)
const activeId = computed(() => realm.activeId)
const loading = computed(() => realm.loading)

const GB = 1024 ** 3
function bytesToGb(bytes: number): number {
  return Math.round((bytes / GB) * 100) / 100
}
function gbToBytes(gb: number): number {
  return Math.round(Math.max(0, gb || 0) * GB)
}

/** 字节 → 人话（1024 进制，与后端 line_stats.format_bytes 同口径） */
function fmtBytes(bytes: number | null | undefined): string {
  let value = Math.max(0, Number(bytes || 0))
  for (const unit of ['B', 'KB', 'MB', 'GB', 'TB']) {
    if (value < 1024 || unit === 'TB') {
      return unit === 'B' ? `${Math.round(value)} B` : `${value.toFixed(1)} ${unit}`
    }
    value /= 1024
  }
  return `${value.toFixed(1)} TB`
}

async function load() {
  await realm.refresh()
}
onMounted(load)

// ==================== 总览 ====================

const allLines = computed(() => realms.value.flatMap((r) => r.lines ?? []))
const exhaustedCount = computed(
  () => allLines.value.filter((l) => l.traffic?.exhausted).length)
const totalUsed = computed(
  () => allLines.value.reduce((sum, l) => sum + (l.traffic?.used_bytes ?? 0), 0))

// ==================== 线路编辑 ====================

const dialogVisible = ref(false)
const editing = ref<{ realmId: number; lineId: number | null }>({
  realmId: 0, lineId: null,
})
const form = reactive({
  realm_id: 0,
  name: '',
  url: '',
  quota_gb: 0,
  sort_order: 0,
  is_active: true,
})
const saving = ref(false)

function openCreate(row: RealmRow) {
  editing.value = { realmId: row.id, lineId: null }
  Object.assign(form, {
    realm_id: row.id,
    name: '',
    url: '',
    quota_gb: 0,
    sort_order: (row.lines?.length ?? 0),
    is_active: true,
  })
  dialogVisible.value = true
}

function openEdit(row: RealmRow, line: RealmLine) {
  editing.value = { realmId: row.id, lineId: line.id }
  Object.assign(form, {
    realm_id: row.id,
    name: line.name || '',
    url: line.url || '',
    quota_gb: bytesToGb(line.traffic?.quota_bytes ?? 0),
    sort_order: line.sort_order ?? 0,
    is_active: line.is_active !== false,
  })
  dialogVisible.value = true
}

async function save() {
  const url = form.url.trim()
  if (!url) {
    ElMessage.warning('请填写线路地址')
    return
  }
  if (!form.realm_id) {
    ElMessage.warning('请选择所属大类')
    return
  }
  saving.value = true
  try {
    if (editing.value.lineId) {
      const moved = form.realm_id !== editing.value.realmId
      await updateRealmLine(editing.value.realmId, editing.value.lineId, {
        name: form.name.trim(),
        url,
        quota_bytes: gbToBytes(form.quota_gb),
        sort_order: form.sort_order,
        is_active: form.is_active,
        realm_id: form.realm_id,
      })
      ElMessage.success(moved
        ? `线路已保存，并已移到「${realms.value.find((r) => r.id === form.realm_id)?.name ?? '目标大类'}」`
        : '线路已保存')
    } else {
      await createRealmLine(form.realm_id, {
        name: form.name.trim(),
        url,
        quota_bytes: gbToBytes(form.quota_gb),
        sort_order: form.sort_order,
        is_active: form.is_active,
      })
      ElMessage.success('线路已新增')
    }
    dialogVisible.value = false
    await realm.refresh()
  } catch {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}

// ==================== 重置 / 删除 ====================

const busyLineId = ref<number | null>(null)

async function resetTraffic(row: RealmRow, line: RealmLine) {
  try {
    await ElMessageBox.confirm(
      `「${line.name || line.url}」的流量用量将从现在重新计数（已用 ${fmtBytes(line.traffic?.used_bytes)} 清零重计），只动这一条线，历史记录保留在日账里可对账。`,
      '重置用量',
      { type: 'warning', confirmButtonText: '重置用量', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  busyLineId.value = line.id
  try {
    await resetRealmLineTraffic(row.id, line.id)
    ElMessage.success(`「${line.name || line.url}」的流量用量已重置`)
    await realm.refresh()
  } catch {
    /* 拦截器已提示 */
  } finally {
    busyLineId.value = null
  }
}

async function removeLine(row: RealmRow, line: RealmLine) {
  try {
    await ElMessageBox.confirm(
      `删除线路「${line.name || line.url}」（${line.url}）？删后用户端不再下发这条线路，已连上这个地址的客户端不受影响；这条线的用量日账保留可对账。`,
      '删除线路',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  busyLineId.value = line.id
  try {
    await deleteRealmLine(row.id, line.id)
    ElMessage.success('线路已删除')
    await realm.refresh()
  } catch {
    /* 拦截器已提示 */
  } finally {
    busyLineId.value = null
  }
}

function trafficPct(line: RealmLine): number {
  const t = line.traffic
  if (!t || !t.quota_bytes) return 0
  return Math.min(100, Math.round((t.used_bytes / t.quota_bytes) * 100))
}
</script>

<template>
  <div class="admin-page lines-page">
    <PageHeader
      eyebrow="媒体"
      title="线路管理"
      description="全站播放线路一页看完：按大类（服）分组，每个大类下面挂多条线路。配额、用量、重置都按线路计——用量按用户实际进来的入口域名归属，用尽的线路在用户端显示「暂停服务」、新播放自动避开，全部用尽时自动回落正常分配，不断播。"
    >
      <template #actions>
        <el-button :icon="RefreshCw" :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <section class="stat-row" aria-label="线路总览">
      <StatTile label="线路" :value="allLines.length" :icon="Route" :hint="`分布在 ${realms.length} 个大类下`" />
      <StatTile label="已用尽" :value="exhaustedCount" :icon="Gauge" :tone="exhaustedCount ? 'warn' : 'plain'" hint="这些线路在用户端显示「暂停服务」" />
      <StatTile label="全站线路已用" :value="fmtBytes(totalUsed)" :icon="Gauge" hint="各线路当前周期已用合计（不限额线路也计）" />
    </section>

    <EmptyState
      v-if="!loading && !realms.length"
      title="还没有大类（服）"
      description="先去服管理新建一个服，再回到这里给它加线路。"
    />

    <div v-else class="line-groups">
      <SectionCard
        v-for="row in realms"
        :key="row.id"
        as="article"
        class="line-group"
        :class="{ 'is-off': !row.is_active }"
        :tone="row.id === activeId ? 'accent' : 'default'"
      >
        <template #title>
          <span class="group-icon"><Route :size="16" /></span>
          <span class="group-title">
            <span class="group-name">{{ row.name }}</span>
            <span class="group-slug mono">{{ row.slug }}</span>
          </span>
        </template>
        <template #actions>
          <div class="group-badges">
            <span v-if="row.id === activeId" class="au-badge au-badge-amber">当前服</span>
            <span v-if="row.is_default" class="au-badge au-badge-info">默认服</span>
            <span v-if="row.is_free" class="au-badge au-badge-green">免费服</span>
            <span v-else class="au-badge au-badge-muted">付费服</span>
            <span v-if="!row.is_active" class="au-badge au-badge-rose">已停用</span>
            <el-button size="small" type="primary" :icon="Plus" @click="openCreate(row)">
              新增线路
            </el-button>
          </div>
        </template>

        <p v-if="!(row.lines && row.lines.length)" class="group-empty">
          这个大类还没有线路：用户端拿不到这个服的连接地址。点「新增线路」加第一条（主线路）。
        </p>

        <div v-else class="line-list">
          <div
            v-for="line in row.lines"
            :key="line.id"
            class="line-row"
            :class="{ 'is-exhausted': line.traffic?.exhausted, 'is-off': !line.is_active }"
          >
            <div class="line-main">
              <div class="line-head">
                <span
                  class="au-badge"
                  :class="line.is_primary ? 'au-badge-info' : 'au-badge-muted'"
                >{{ line.is_primary ? '主线路' : '副线路' }}</span>
                <span class="line-name">{{ line.name || '未命名线路' }}</span>
                <span v-if="line.traffic?.exhausted" class="au-badge au-badge-rose">流量已用尽</span>
                <span v-if="!line.is_active" class="au-badge au-badge-muted">已停用</span>
                <span class="line-state">
                  <template v-if="line.traffic?.exhausted">已用尽 · 用户端显示「暂停服务」</template>
                  <template v-else-if="line.traffic && line.traffic.quota_bytes > 0">剩余 {{ fmtBytes(line.traffic.remaining_bytes) }}</template>
                  <template v-else>不限额</template>
                </span>
              </div>
              <p class="line-url mono">{{ line.url }}</p>
              <p class="line-nums">
                已用 {{ fmtBytes(line.traffic?.used_bytes) }}
                <template v-if="line.traffic && line.traffic.quota_bytes > 0">
                  / 配额 {{ fmtBytes(line.traffic.quota_bytes) }}（{{ trafficPct(line) }}%）
                </template>
                <span v-if="line.traffic?.period_started_at" class="line-since">
                  · 自 {{ line.traffic.period_started_at.slice(0, 10) }} 起计
                </span>
              </p>
              <div v-if="line.traffic && line.traffic.quota_bytes > 0" class="line-bar" aria-hidden="true">
                <span
                  class="line-bar__fill"
                  :class="{ 'is-exhausted': line.traffic.exhausted }"
                  :style="{ width: `${trafficPct(line)}%` }"
                />
              </div>
            </div>
            <div class="line-actions">
              <el-button size="small" text :icon="Pencil" @click="openEdit(row, line)">编辑</el-button>
              <el-button
                size="small"
                text
                :icon="RotateCcw"
                :loading="busyLineId === line.id"
                @click="resetTraffic(row, line)"
              >
                重置用量
              </el-button>
              <el-button
                size="small"
                text
                type="danger"
                :icon="Trash2"
                :loading="busyLineId === line.id"
                @click="removeLine(row, line)"
              >
                删除
              </el-button>
            </div>
          </div>
        </div>
      </SectionCard>
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="editing.lineId ? '编辑线路' : '新增线路'"
      width="520px"
      append-to-body
    >
      <el-form label-width="110px">
        <el-form-item label="所属大类">
          <el-select v-model="form.realm_id" placeholder="选择这条线路属于哪个大类" style="width: 100%">
            <el-option v-for="r in realms" :key="r.id" :label="r.name" :value="r.id" />
          </el-select>
          <p class="field-help">
            这条线路挂在哪个大类（服）下面。编辑时改选就是把线路挪到
            那个大类：线路不变，流量记录、配额与已用都跟着线路走。
          </p>
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="留空自动给：首条主线路、其后副线路" maxlength="80" />
        </el-form-item>
        <el-form-item label="线路地址">
          <el-input v-model="form.url" placeholder="https://cdn.example.com" maxlength="500" />
          <p class="field-help">
            用户在客户端里填的入口地址。用量按这个地址的域名归属：
            用户从哪个域名进来，流量就记到哪条线路。
          </p>
        </el-form-item>
        <el-form-item label="流量配额（GB）">
          <el-input-number
            v-model="form.quota_gb"
            :min="0"
            :precision="2"
            :step="100"
            controls-position="right"
            style="width: 100%"
          />
          <p class="field-help">
            这条线路累计能跑多少播放流量，0 = 不限额。用尽后新用户不再分到这条线
            （用户端显示「暂停服务」），已在播的不受影响；所有线路都用尽时自动回落正常分配。
            用量按真实出字节计（直链不经服务器的不计），在列表里点「重置用量」重新计数。
          </p>
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number
            v-model="form.sort_order"
            :min="0"
            :step="1"
            controls-position="right"
            style="width: 100%"
          />
          <p class="field-help">启用线路里排序最靠前的一条是主线路（用户端默认选中），其余是副线路。</p>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.is_active" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.line-groups { display: flex; flex-direction: column; gap: 16px; }
.group-icon {
  display: inline-flex; align-items: center; justify-content: center;
  width: 28px; height: 28px; border-radius: var(--au-r-md);
  background: var(--au-surface-2); color: var(--au-text-2);
}
.group-title { display: inline-flex; align-items: baseline; gap: 8px; }
.group-name { font-size: 15px; font-weight: 600; color: var(--au-text); }
.group-slug { font-size: 12px; color: var(--au-text-4); }
.group-badges { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.group-empty { margin: 0; font-size: 13px; color: var(--au-text-3); }

.line-list { display: flex; flex-direction: column; }
.line-row {
  display: flex; align-items: flex-start; gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid var(--au-border);
}
.line-row:last-child { border-bottom: none; }
.line-row.is-off .line-main { opacity: 0.55; }
.line-main { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 5px; }
.line-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.line-name { font-size: 13.5px; font-weight: 600; color: var(--au-text); }
.line-state { font-size: 12px; color: var(--au-text-3); }
.line-row.is-exhausted .line-state { color: var(--au-danger); font-weight: 600; }
.line-url {
  margin: 0; font-size: 12.5px; color: var(--au-text-2);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.line-nums { margin: 0; font-size: 12.5px; font-variant-numeric: tabular-nums; color: var(--au-text); }
.line-since { color: var(--au-text-4); font-size: 12px; }
.line-bar { height: 6px; border-radius: 999px; background: var(--au-border); overflow: hidden; }
.line-bar__fill { display: block; height: 100%; border-radius: inherit; background: var(--au-primary); transition: width 0.3s ease; }
.line-bar__fill.is-exhausted { background: var(--au-danger); }
.line-actions { flex: none; display: flex; align-items: center; gap: 2px; }
.field-help { margin: 6px 0 0; font-size: 12px; line-height: 1.6; color: var(--au-text-3); }
</style>
