<script setup lang="ts">
/**
 * API Key 管理（Service Token，v2.58.0）
 *
 * - 第三方系统用长期有效的 API Key 调用受保护接口（X-API-Key 请求头）
 * - 明文只在创建时返回一次：大弹窗展示，红色提示立即复制
 * - 列表只展示 key_id 掩码，绝不返回明文
 * - 品牌约束：只用 --au-* 变量，零新增色值；暗房影院深色主题
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { toAdminError } from '@/utils/errorMessage'
import { parseServerTime } from '@/utils/serverTime'
import { KeyRound, Plus, RefreshCw, Copy, Trash2, Pencil, Info } from 'lucide-vue-next'
import { PageHeader, SectionCard, StatTile } from '@/components/ui'
import DataTable from '@/components/DataTable.vue'
import type { DataColumn } from '@/components/DataTable.vue'
import NoticePanel from '@/components/NoticePanel.vue'
import {
  createApiKey,
  fetchApiKeys,
  revokeApiKey,
  updateApiKey,
  type ApiKeyRow,
  type ApiKeyCreateResponse,
} from '@/api/admin'

const keys = ref<ApiKeyRow[]>([])
const loading = ref(false)
const loadError = ref('')

// 创建弹窗
const showCreate = ref(false)
const creating = ref(false)
const form = ref({ name: '', scopes: ['read'] as string[], expires_days: 365 as number | null })
const noExpire = ref(false)

// 明文展示弹窗（只显示一次）
const showPlaintext = ref(false)
const plaintextKey = ref('')
const plaintextName = ref('')

// 编辑弹窗
const showEdit = ref(false)
const savingEdit = ref(false)
const editing = ref<ApiKeyRow | null>(null)
const editForm = ref({ name: '', scopes: [] as string[], is_active: true })

const SCOPES = [
  { value: 'read', label: '只读', desc: '读数据、查状态' },
  { value: 'write', label: '读写', desc: '包含只读，可写数据' },
  { value: 'admin', label: '管理', desc: '包含读写，全部权限' },
]

const activeCount = computed(() => keys.value.filter((k) => k.is_active).length)

/** 列表列定义（统一标准：DataTable 为唯一表格载体，不分页、一次全量展示） */
const columns: DataColumn[] = [
  { key: 'name', label: '名称', minWidth: 180, mobile: 'title' },
  { key: 'scopes', label: '权限范围', minWidth: 150 },
  { key: 'status', label: '状态', width: 96 },
  { key: 'expires_at', label: '过期时间', minWidth: 160 },
  { key: 'last_used_at', label: '最后使用', minWidth: 160 },
  { key: 'actions', label: '操作', width: 190, fixed: 'right', align: 'right' },
]

function keyStatus(k: ApiKeyRow): { type: 'success' | 'danger'; text: string } {
  if (!k.is_active) return { type: 'danger', text: '已吊销' }
  if (isExpired(k)) return { type: 'danger', text: '已过期' }
  return { type: 'success', text: '生效中' }
}

function scopeLabel(s: string) {
  return SCOPES.find((x) => x.value === s)?.label || s
}

function scopeClass(s: string) {
  if (s === 'admin') return 'is-down'
  if (s === 'write') return 'is-warn'
  return 'is-ok'
}

function fmtTime(t: string | null) {
  if (!t) return '—'
  try {
    return parseServerTime(t).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return t
  }
}

function isExpired(k: ApiKeyRow) {
  if (!k.expires_at) return false
  return parseServerTime(k.expires_at).getTime() < Date.now()
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const r = await fetchApiKeys()
    keys.value = r.items || []
  } catch (e: any) {
    loadError.value = e?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.value = { name: '', scopes: ['read'], expires_days: 365 }
  noExpire.value = false
  showCreate.value = true
}

async function doCreate() {
  if (!form.value.name.trim()) {
    ElMessage.warning('请填写名称，比如"XX 系统对接"')
    return
  }
  if (!form.value.scopes.length) {
    ElMessage.warning('至少选一个权限范围')
    return
  }
  creating.value = true
  try {
    const r: ApiKeyCreateResponse = await createApiKey({
      name: form.value.name.trim(),
      scopes: form.value.scopes,
      expires_days: noExpire.value ? null : form.value.expires_days,
    })
    showCreate.value = false
    plaintextKey.value = r.key
    plaintextName.value = r.name
    showPlaintext.value = true
    await load()
  } catch (e: any) {
    ElMessage.error(toAdminError(e, '创建失败'))
  } finally {
    creating.value = false
  }
}

async function copyPlaintext() {
  try {
    await navigator.clipboard.writeText(plaintextKey.value)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.warning('复制失败，请手动选中复制')
  }
}

function closePlaintext() {
  showPlaintext.value = false
  plaintextKey.value = ''
  ElMessage.info('明文已关闭，之后无法再查看')
}

function openEdit(k: ApiKeyRow) {
  editing.value = k
  editForm.value = { name: k.name, scopes: [...k.scopes], is_active: k.is_active }
  showEdit.value = true
}

async function doEdit() {
  if (!editing.value) return
  savingEdit.value = true
  try {
    await updateApiKey(editing.value.key_id, {
      name: editForm.value.name,
      scopes: editForm.value.scopes,
      is_active: editForm.value.is_active,
    })
    ElMessage.success('已保存')
    showEdit.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(toAdminError(e, '保存失败'))
  } finally {
    savingEdit.value = false
  }
}

async function doRevoke(k: ApiKeyRow) {
  try {
    await ElMessageBox.confirm(
      `确定吊销「${k.name}」（${k.key_id_masked}）吗？吊销后使用该 Key 的第三方将立即无法访问，不可恢复。`,
      '吊销 API Key',
      { confirmButtonText: '确认吊销', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await revokeApiKey(k.key_id)
    ElMessage.success('已吊销')
    await load()
  } catch (e: any) {
    ElMessage.error(toAdminError(e, '吊销失败'))
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-page">
    <PageHeader
      eyebrow="系统"
      title="API Key"
      description="第三方系统的长期访问凭证（Service Token），用 X-API-Key 请求头鉴权"
    >
      <template #actions>
        <el-button :icon="RefreshCw" @click="load" :loading="loading">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">创建 Key</el-button>
      </template>
    </PageHeader>

    <div v-if="!loading && !loadError" class="tile-grid">
      <StatTile label="全部 Key" :value="keys.length" />
      <StatTile label="生效中" :value="activeCount" />
      <StatTile label="已吊销 / 停用" :value="keys.length - activeCount" />
    </div>

    <SectionCard title="凭证列表" :icon="KeyRound" flush>
      <DataTable
        :rows="keys"
        :columns="columns"
        :loading="loading"
        :error="loadError"
        row-key="id"
        empty="还没有 API Key"
        empty-description="点右上角「创建 Key」生成第一个。"
        @retry="load"
      >
        <template #cell-name="{ row }">
          <div class="key-name">{{ row.name }}</div>
          <div class="key-id"><KeyRound class="ic" />{{ row.key_id_masked }}</div>
        </template>
        <template #cell-scopes="{ row }">
          <div class="key-scopes">
            <span v-for="s in row.scopes" :key="s" class="scope-pill" :class="scopeClass(s)">{{ scopeLabel(s) }}</span>
          </div>
        </template>
        <template #cell-status="{ row }">
          <el-tag :type="keyStatus(row).type" size="small">{{ keyStatus(row).text }}</el-tag>
        </template>
        <template #cell-expires_at="{ row }">{{ row.expires_at ? fmtTime(row.expires_at) : '永不过期' }}</template>
        <template #cell-last_used_at="{ row }">{{ fmtTime(row.last_used_at) }}</template>
        <template #cell-actions="{ row }">
          <el-button size="small" :icon="Pencil" @click="openEdit(row)">编辑</el-button>
          <el-button v-if="row.is_active" size="small" type="danger" :icon="Trash2" @click="doRevoke(row)">吊销</el-button>
        </template>
      </DataTable>
    </SectionCard>

    <NoticePanel
      title="第三方怎么用"
      summary="请求头带 X-API-Key 即可，权限层级：管理 > 读写 > 只读"
      :icon="Info"
      storage-key="api-keys-usage"
    >
      <p class="usage-text">
        请求头带 <code>X-API-Key: ak_...</code> 即可。
        第三方拿到 Key 后，用该 Key 作 X-API-Key 请求头调 <code>GET /api/admin/api-keys/verify</code> 验证连通性（走 Key 鉴权，不是管理员登录）。
        权限层级：管理 &gt; 读写 &gt; 只读。
      </p>
    </NoticePanel>

    <!-- 创建弹窗 -->
    <el-dialog v-model="showCreate" title="创建 API Key" width="480px">
      <el-form label-width="90px">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="比如：XX 系统对接" maxlength="100" />
        </el-form-item>
        <el-form-item label="权限范围">
          <el-checkbox-group v-model="form.scopes">
            <el-checkbox v-for="s in SCOPES" :key="s.value" :label="s.value">
              {{ s.label }}<span class="scope-desc">（{{ s.desc }}）</span>
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="有效期">
          <el-checkbox v-model="noExpire">永不过期</el-checkbox>
          <el-input-number v-if="!noExpire" v-model="form.expires_days" :min="1" :max="3650" style="margin-left: 12px" />
          <span v-if="!noExpire" class="scope-desc"> 天</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" @click="doCreate" :loading="creating">创建</el-button>
      </template>
    </el-dialog>

    <!-- 明文展示（只一次） -->
    <el-dialog v-model="showPlaintext" title="创建成功" width="520px" :close-on-click-modal="false" @close="closePlaintext">
      <el-alert type="error" :closable="false" show-icon>
        <b>只显示一次！</b>请立即复制保存，关闭后无法再查看。Key 明文不会存进数据库。
      </el-alert>
      <div class="plaintext-box">
        <div class="plaintext-name">{{ plaintextName }}</div>
        <code class="plaintext-key">{{ plaintextKey }}</code>
        <el-button :icon="Copy" @click="copyPlaintext" type="primary" plain>复制</el-button>
      </div>
    </el-dialog>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="showEdit" title="编辑 API Key" width="480px">
      <el-form label-width="90px">
        <el-form-item label="名称">
          <el-input v-model="editForm.name" maxlength="100" />
        </el-form-item>
        <el-form-item label="权限范围">
          <el-checkbox-group v-model="editForm.scopes">
            <el-checkbox v-for="s in SCOPES" :key="s.value" :label="s.value">{{ s.label }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="editForm.is_active" active-text="生效" inactive-text="停用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEdit = false">取消</el-button>
        <el-button type="primary" @click="doEdit" :loading="savingEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.tile-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
}
.is-ok { color: var(--au-success); }
.is-warn { color: var(--au-warning); }
.is-down { color: var(--au-danger); }
.key-name {
  font-weight: 600;
  font-size: 15px;
  color: var(--au-text);
}
.key-id {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 2px;
  font-family: monospace;
  font-size: 12px;
  color: var(--au-text-2);
}
.key-id .ic { width: 14px; height: 14px; }
.key-scopes {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.scope-pill {
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 999px;
  border: 1px solid currentColor;
}
.usage-text {
  margin: 0;
  font-size: 13px;
  color: var(--au-text-2);
  line-height: 1.8;
}
.usage-text code {
  background: var(--au-bg);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: monospace;
}
.scope-desc {
  color: var(--au-text-2);
  font-size: 12px;
}
.plaintext-box {
  margin-top: 16px;
  padding: 16px;
  background: var(--au-bg);
  border: 1px dashed var(--au-danger);
  border-radius: var(--au-r-lg);
  text-align: center;
}
.plaintext-name {
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--au-text);
}
.plaintext-key {
  display: block;
  font-family: monospace;
  font-size: 14px;
  word-break: break-all;
  padding: 12px;
  margin-bottom: 12px;
  background: var(--au-surface);
  border-radius: var(--radius-sm, 8px);
  color: var(--au-text);
  user-select: all;
}
</style>
