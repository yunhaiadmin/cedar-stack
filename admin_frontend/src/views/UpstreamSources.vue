<script setup lang="ts">
/**
 * 上游源页 —— 上游聚合下线后只剩线路配置面。
 *
 * 源管理（新建/启用/凭据）与目录聚合已随后上游聚合整族删除；
 * 本页保留两个仍在服役的配置面：详情页上游线路开关（U-02）与
 * 白名单专属线路（记录 CRUD、批 6 能力）。源列表只读数据仅供
 * 专属线路的选源候选与按 id 显示源名（fetchUpstreamSources）。
 * 存储绑定 / 115 账号池 / sidecar 三个观测页签与本页同框保留。
 */
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { toAdminError } from '@/utils/errorMessage'
import {
  createExclusiveLine,
  deleteExclusiveLine,
  fetchExclusiveLines,
  fetchUpstreamDetailLineConfig,
  fetchUpstreamSources,
  resetExclusiveLineSecret,
  updateExclusiveLine,
  updateUpstreamDetailLineConfig,
} from '@/api/admin'
import type {
  ExclusiveLine,
  ExclusiveLineUpdate,
  UpstreamSource,
} from '@/api/admin'
import { PageHeader, SectionCard } from '@/components/ui'

/**
 * 重新布局批 3：存储绑定 / 115 账号池 / sidecar 三个只读观测页
 * 降为本页标签（沿用 Codes.vue 的 ?tab= 同步 + KeepAlive 模式）。
 * 线路配置面板用 v-show：来回切标签不卸载状态；观测面板按需加载、
 * 进过一次后 KeepAlive 缓存，不重拉数据。
 */
const StorageBindings = defineAsyncComponent(() => import('./StorageBindings.vue'))
const Yun139Pools = defineAsyncComponent(() => import('./Yun139Pools.vue'))
const ProviderSidecar = defineAsyncComponent(() => import('./ProviderSidecar.vue'))

type UpstreamTab = 'sources' | 'bindings' | 'pools' | 'sidecar'

const route = useRoute()
const router = useRouter()

const activeTab = computed<UpstreamTab>({
  get: () => {
    const t = route.query.tab
    return t === 'bindings' || t === 'pools' || t === 'sidecar' ? t : 'sources'
  },
  set: (tab) => {
    if (tab === activeTab.value) return
    router.replace({ query: { ...route.query, tab } })
  },
})

const sources = ref<UpstreamSource[]>([])

async function loadSources() {
  try {
    const res = await fetchUpstreamSources()
    sources.value = res.items
  } catch {
    // 选源候选拉不到就留空：专属线路的范围显示回落 #id，不阻断配置
    sources.value = []
  }
}

// 详情页上游线路开关（U-02）：默认开；关后用户端详情/PlaybackInfo
// 来源列表不再出现「上游 · <源名>」线路，取流口 404。
const detailLineEnabled = ref(true)
const detailLineSaving = ref(false)

async function loadDetailLineConfig() {
  try {
    const res = await fetchUpstreamDetailLineConfig()
    detailLineEnabled.value = res.enabled
  } catch (e) {
    ElMessage.error(toAdminError(e))
  }
}

async function onDetailLineToggle(value: boolean) {
  detailLineSaving.value = true
  try {
    const res = await updateUpstreamDetailLineConfig(value)
    detailLineEnabled.value = res.enabled
    ElMessage.success(res.enabled ? '详情页上游线路已开启' : '详情页上游线路已关闭')
  } catch (e) {
    ElMessage.error(toAdminError(e))
    detailLineEnabled.value = !value
  } finally {
    detailLineSaving.value = false
  }
}

// 专属线路通用能力（2026-11-05）：线路＝一条配置记录、可多条，
// 在此统一增删改。入口地址对用户展示（够格用户在门户「我的」里
// 能看到并复制）；取流基址（含路径口令）与入口密钥是内部配置，
// 后端只回已配状态、原文永不回显，编辑时留空 = 不动。
// 现行 pilot 线即第一条（迁移线路，id=pilot），行为与单线时一致。
const exclusiveLines = ref<ExclusiveLine[]>([])
const exclusiveLinesSaving = ref(false)
const lineDialogVisible = ref(false)
const lineEditingId = ref<string | null>(null)
const lineForm = ref({
  name: '',
  entry_url: '',
  stream_base_url: '',
  entry_secret: '',
  audience: 'whitelist',
  scope_mode: 'all' as 'all' | 'sources',
  scope_source_ids: [] as number[],
  legacy_source_ids: [] as number[],
  enabled: true,
  stream_configured: false,
  secret_configured: false,
})

/** 范围/名单选源候选：苹果 CMS 型（专属线路只服务 CMS 播放主机）。 */
const lineSourceCandidates = computed(() =>
  sources.value.filter((s) => s.source_type === 'applecms'),
)

function lineScopeText(line: ExclusiveLine) {
  if (line.scope_mode === 'all') return '全部无界资源'
  const names = line.scope_source_ids.map((id) => {
    const src = sources.value.find((s) => s.id === id)
    return src ? src.name : `#${id}`
  })
  return names.length ? `指定源：${names.join('、')}` : '指定源：（空）'
}

async function loadExclusiveLines() {
  try {
    const res = await fetchExclusiveLines()
    exclusiveLines.value = res.lines
  } catch (e) {
    ElMessage.error(toAdminError(e))
  }
}

function openLineCreate() {
  lineEditingId.value = null
  lineForm.value = {
    name: '', entry_url: '', stream_base_url: '', entry_secret: '',
    audience: 'whitelist', scope_mode: 'all', scope_source_ids: [],
    legacy_source_ids: [], enabled: true,
    stream_configured: false, secret_configured: false,
  }
  lineDialogVisible.value = true
}

function openLineEdit(line: ExclusiveLine) {
  lineEditingId.value = line.id
  lineForm.value = {
    name: line.name, entry_url: line.entry_url,
    // 基址/密钥原文不回显：输入框留空 = 不动，已配状态单独展示
    stream_base_url: '', entry_secret: '',
    audience: line.audience, scope_mode: line.scope_mode,
    scope_source_ids: [...line.scope_source_ids],
    legacy_source_ids: [...line.legacy_source_ids],
    enabled: line.enabled,
    stream_configured: line.stream_configured,
    secret_configured: line.secret_configured,
  }
  lineDialogVisible.value = true
}

/** 生成随机入口密钥填入表单（管理员抄去改反代侧注入值用）。 */
function generateLineSecret() {
  const bytes = new Uint8Array(32)
  crypto.getRandomValues(bytes)
  lineForm.value.entry_secret = Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, '0')).join('')
}

async function onLineSave() {
  const f = lineForm.value
  if (!f.name.trim()) {
    ElMessage.warning('线路名称不能为空')
    return
  }
  exclusiveLinesSaving.value = true
  try {
    if (lineEditingId.value === null) {
      await createExclusiveLine({
        name: f.name.trim(),
        entry_url: f.entry_url.trim(),
        stream_base_url: f.stream_base_url.trim() || undefined,
        entry_secret: f.entry_secret.trim() || undefined,
        audience: f.audience,
        scope_mode: f.scope_mode,
        scope_source_ids: f.scope_source_ids,
        legacy_source_ids: f.legacy_source_ids,
        enabled: f.enabled,
      })
      ElMessage.success('线路已创建，入口认定即刻生效')
    } else {
      const payload: ExclusiveLineUpdate = {
        name: f.name.trim(),
        entry_url: f.entry_url.trim(),
        audience: f.audience,
        scope_mode: f.scope_mode,
        scope_source_ids: f.scope_source_ids,
        legacy_source_ids: f.legacy_source_ids,
        enabled: f.enabled,
      }
      // 基址/密钥留空 = 不动（原文不回显，无从回填）
      if (f.stream_base_url.trim()) payload.stream_base_url = f.stream_base_url.trim()
      if (f.entry_secret.trim()) payload.entry_secret = f.entry_secret.trim()
      await updateExclusiveLine(lineEditingId.value, payload)
      ElMessage.success('线路已保存，入口认定即刻生效')
    }
    lineDialogVisible.value = false
    void loadExclusiveLines()
  } catch (e) {
    ElMessage.error(toAdminError(e))
  } finally {
    exclusiveLinesSaving.value = false
  }
}

async function onLineToggle(line: ExclusiveLine, enabled: boolean) {
  exclusiveLinesSaving.value = true
  try {
    await updateExclusiveLine(line.id, { enabled })
    line.enabled = enabled
    ElMessage.success(enabled ? '线路已启用' : '线路已停用，入口不再被认定')
  } catch (e) {
    ElMessage.error(toAdminError(e))
    void loadExclusiveLines()
  } finally {
    exclusiveLinesSaving.value = false
  }
}

async function onLineResetSecret(line: ExclusiveLine) {
  try {
    const { value } = await ElMessageBox.prompt(
      '输入新的入口密钥（须同步把反代侧注入的标记头值改成同值；旧密钥立即失效）',
      `重置「${line.name}」的入口密钥`,
      { inputType: 'password', confirmButtonText: '重置', cancelButtonText: '取消' },
    )
    const secret = (value || '').trim()
    if (!secret) {
      ElMessage.warning('密钥为空，未改动')
      return
    }
    await resetExclusiveLineSecret(line.id, secret)
    ElMessage.success('入口密钥已重置（反代侧注入值须同步修改）')
    void loadExclusiveLines()
  } catch {
    // 用户取消 prompt：不提示
  }
}

async function onLineDelete(line: ExclusiveLine) {
  try {
    await ElMessageBox.confirm(
      `删除线路「${line.name}」？删后该入口不再被认定，用户端不再展示。`,
      '删除专属线路',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  exclusiveLinesSaving.value = true
  try {
    await deleteExclusiveLine(line.id)
    ElMessage.success('线路已删除')
    void loadExclusiveLines()
  } catch (e) {
    ElMessage.error(toAdminError(e))
  } finally {
    exclusiveLinesSaving.value = false
  }
}

onMounted(() => {
  void loadSources()
  void loadDetailLineConfig()
  void loadExclusiveLines()
})
</script>

<template>
  <div class="upstream-sources">
    <PageHeader
      title="上游源"
      subtitle="上游聚合已下线：本页只剩详情页线路开关与专属线路配置；源数据只读，供专属线路选源与显示源名。"
    />
    <div class="tab-bar" role="tablist" aria-label="上游源">
      <button
        id="upstream-tab-sources"
        type="button"
        role="tab"
        class="tab-btn"
        :class="{ active: activeTab === 'sources' }"
        :aria-selected="activeTab === 'sources'"
        aria-controls="upstream-panel"
        @click="activeTab = 'sources'"
      >
        线路配置
        <span class="tab-hint">详情线路 / 专属线路</span>
      </button>
      <button
        id="upstream-tab-bindings"
        type="button"
        role="tab"
        class="tab-btn"
        :class="{ active: activeTab === 'bindings' }"
        :aria-selected="activeTab === 'bindings'"
        aria-controls="upstream-panel"
        @click="activeTab = 'bindings'"
      >
        观测·绑定
        <span class="tab-hint">存储绑定</span>
      </button>
      <button
        id="upstream-tab-pools"
        type="button"
        role="tab"
        class="tab-btn"
        :class="{ active: activeTab === 'pools' }"
        :aria-selected="activeTab === 'pools'"
        aria-controls="upstream-panel"
        @click="activeTab = 'pools'"
      >
        观测·账号池
        <span class="tab-hint">115 账号池</span>
      </button>
      <button
        id="upstream-tab-sidecar"
        type="button"
        role="tab"
        class="tab-btn"
        :class="{ active: activeTab === 'sidecar' }"
        :aria-selected="activeTab === 'sidecar'"
        aria-controls="upstream-panel"
        @click="activeTab = 'sidecar'"
      >
        观测·sidecar
        <span class="tab-hint">供应商 sidecar</span>
      </button>
    </div>

    <!-- 线路配置面板：v-show 常驻，切标签回来状态都在 -->
    <div v-show="activeTab === 'sources'">
    <SectionCard title="用户端展示">
      <div class="detail-line-row">
        <div>
          <div>详情页上游线路</div>
          <div class="detail-line-hint">
            开启时，有上游映射的条目会在用户端详情页来源列表里多一条
            「上游 · 源名」线路，点它走上游起播；关闭后线路不出现。
          </div>
        </div>
        <el-switch
          :model-value="detailLineEnabled"
          :loading="detailLineSaving"
          active-text="开启"
          inactive-text="关闭"
          @change="onDetailLineToggle"
        />
      </div>
    </SectionCard>
    <SectionCard title="专属线路">
      <template #actions>
        <el-button type="primary" @click="openLineCreate">新增线路</el-button>
        <el-button @click="loadExclusiveLines">刷新</el-button>
      </template>
      <div class="detail-line-hint" style="margin-bottom: 12px">
        线路是一条独立入口：够格用户把播放器服务器地址填成线路的入口地址，
        请求经该线反代进入时带专属标记（标记头值与线路的入口密钥一致才认定），
        入口内适用范围里的苹果 CMS 线路走该线的取流基址回源；不够格用户在登录
        与播放信息两处被拒。够格用户能在门户「我的」里看到自己线路的地址。
        取流基址（含路径口令）与入口密钥只显示已配状态、原文不回显；所有改动
        保存即刻生效。现行 pilot 线是迁移来的第一条，行为与此前一致；「存量旧
        地址名单」只管带 :wl 的存量旧地址在取流口的放行查。
      </div>
      <div v-if="exclusiveLines.length" class="exclusive-list">
        <div v-for="line in exclusiveLines" :key="line.id" class="exclusive-row">
          <span>
            {{ line.name }}
            <span class="exclusive-id">{{ line.entry_url || '（未填入口地址）' }}</span>
            <span class="exclusive-id">
              · {{ line.audience === 'whitelist' ? '白名单' : line.audience }}
              · {{ lineScopeText(line) }}
              · 基址{{ line.stream_configured ? '已配' : '未配' }}
              · 密钥{{ line.secret_configured ? '已配' : '未配' }}
            </span>
          </span>
          <span class="exclusive-actions">
            <el-switch
              :model-value="line.enabled"
              :loading="exclusiveLinesSaving"
              active-text="启用"
              inactive-text="停用"
              @change="(v: boolean) => onLineToggle(line, v)"
            />
            <el-button size="small" :disabled="exclusiveLinesSaving" @click="openLineEdit(line)">
              编辑
            </el-button>
            <el-button size="small" :disabled="exclusiveLinesSaving" @click="onLineResetSecret(line)">
              重置密钥
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :disabled="exclusiveLinesSaving"
              @click="onLineDelete(line)"
            >
              删除
            </el-button>
          </span>
        </div>
      </div>
      <div v-else class="detail-line-hint">暂无线路。新增一条并配好取流基址与入口密钥后，够格用户即可使用。</div>
    </SectionCard>
    <el-dialog
      v-model="lineDialogVisible"
      :title="lineEditingId === null ? '新增专属线路' : '编辑专属线路'"
      width="560px"
    >
      <el-form label-width="110px">
        <el-form-item label="名称">
          <el-input v-model="lineForm.name" placeholder="如：白名单专属线路" />
        </el-form-item>
        <el-form-item label="入口地址">
          <el-input v-model="lineForm.entry_url" placeholder="https://pilot.example.com" />
          <div class="detail-line-hint">对用户展示的公网地址，用户在播放器里填这个。</div>
        </el-form-item>
        <el-form-item label="取流基址">
          <el-input
            v-model="lineForm.stream_base_url"
            type="password"
            show-password
            :placeholder="lineEditingId !== null && lineForm.stream_configured ? '已配置（留空 = 不动）' : 'https://…/p/…'"
          />
          <div class="detail-line-hint">内部回源地址，含路径口令，原文不回显；编辑时留空 = 不动。</div>
        </el-form-item>
        <el-form-item label="入口密钥">
          <div class="exclusive-pilot">
            <el-input
              v-model="lineForm.entry_secret"
              type="password"
              show-password
              :placeholder="lineEditingId !== null && lineForm.secret_configured ? '已配置（留空 = 不动）' : '输入或生成'"
              style="width: 300px"
            />
            <el-button @click="generateLineSecret">生成</el-button>
          </div>
          <div class="detail-line-hint">反代注入的标记头值必须与它一致；编辑时留空 = 不动。</div>
        </el-form-item>
        <el-form-item label="准入人群">
          <el-select v-model="lineForm.audience" style="width: 200px">
            <el-option label="白名单用户" value="whitelist" />
          </el-select>
        </el-form-item>
        <el-form-item label="适用范围">
          <el-radio-group v-model="lineForm.scope_mode">
            <el-radio value="all">全部无界资源</el-radio>
            <el-radio value="sources">指定源</el-radio>
          </el-radio-group>
          <el-select
            v-if="lineForm.scope_mode === 'sources'"
            v-model="lineForm.scope_source_ids"
            multiple
            placeholder="选择源（苹果 CMS 型）"
            style="width: 100%; margin-top: 8px"
          >
            <el-option
              v-for="s in lineSourceCandidates"
              :key="s.id"
              :label="`${s.name}（#${s.id}）`"
              :value="s.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="存量旧地址名单">
          <el-select
            v-model="lineForm.legacy_source_ids"
            multiple
            placeholder="带 :wl 的存量旧地址可放行的源"
            style="width: 100%"
          >
            <el-option
              v-for="s in lineSourceCandidates"
              :key="s.id"
              :label="`${s.name}（#${s.id}）`"
              :value="s.id"
            />
          </el-select>
          <div class="detail-line-hint">迁移线路从旧专属名单带入；新线路一般留空。</div>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="lineForm.enabled" active-text="启用" inactive-text="停用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="lineDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="exclusiveLinesSaving" @click="onLineSave">保存</el-button>
      </template>
    </el-dialog>
    </div>

    <!-- 观测面板：四个子页各自带 PageHeader，这里只放标签条，不再叠第二个页头 -->
    <div
      id="upstream-panel"
      class="tab-panel"
      role="tabpanel"
      :aria-labelledby="`upstream-tab-${activeTab}`"
    >
      <KeepAlive>
        <StorageBindings v-if="activeTab === 'bindings'" />
        <Yun139Pools v-else-if="activeTab === 'pools'" />
        <ProviderSidecar v-else-if="activeTab === 'sidecar'" />
      </KeepAlive>
    </div>
  </div>
</template>

<style scoped>
.upstream-sources {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
/* 标签条（与 Codes.vue / Welfare.vue 同款） */
.tab-bar {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--au-border);
  overflow-x: auto;
  scrollbar-width: none;
}
.tab-btn {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  flex-shrink: 0;
  padding: 10px 18px;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--au-text-2);
  font: inherit;
  font-size: 15px;
  font-weight: 500;
  cursor: pointer;
  margin-bottom: -1px;
  transition: color var(--au-fast) var(--au-ease), border-color var(--au-fast) var(--au-ease);
}
.tab-btn:hover { color: var(--au-text); }
.tab-btn:focus-visible { outline: 2px solid var(--au-border-focus); outline-offset: -2px; }
.tab-btn.active {
  color: var(--au-primary);
  border-bottom-color: var(--au-primary);
}
.tab-hint {
  font-size: 12px;
  color: var(--au-text-3);
  font-weight: 400;
  white-space: nowrap;
}
.tab-btn.active .tab-hint { color: var(--au-primary); }
.tab-panel { min-width: 0; }
.detail-line-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.detail-line-hint {
  margin-top: 4px;
  font-size: 12px;
  color: var(--au-text-3);
}
.exclusive-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 12px;
}
.exclusive-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.exclusive-id { color: var(--au-text-3); font-size: 12px; }
.exclusive-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.exclusive-pilot {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

@media (max-width: 640px) {
  .tab-btn { padding: 8px 12px; font-size: 14px; }
  .tab-hint { font-size: 11px; }
}
</style>
