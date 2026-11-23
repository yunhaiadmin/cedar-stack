<script setup lang="ts">
/**
 * 播放器登录黑名单
 *
 * 管的是「哪些播放器不许登录」：命中的播放器在 Emby 客户端**登录环节**
 * 被拒（403），只拦登录——已登录的令牌、网页端登录都不受影响。
 *
 * 规则形状（后端 backend/emby_server/player_blacklist.py）：每条规则
 * { label, match[], exclude[] }，对客户端名与 User-Agent 做大小写不敏感
 * 子串匹配；match 任一命中且 exclude 全部未命中才算拉黑（exclude 优先，
 * 靠它保住名字相近但要放行的客户端）。
 *
 * 名单来源有两种，页面顶部必须说清当前是哪种：
 * - 库里没存过 → 用代码级默认名单（using_default）；
 * - 管理员保存过 → 以库中值为准（哪怕存的是空数组 = 功能停用）。
 * 「恢复默认名单」= 传 rules=null 删行，回到第一种。
 *
 * 本页展示的规则一律来自 GET /player-blacklist/config，不写死任何规则内容；
 * 保存走 PUT 同一端点，保存后以后端返回的归一值回填（后端会把关键词
 * 转小写、去首尾空白）。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ListX, MonitorOff, Plus, RefreshCw, RotateCcw, Save, Trash2, Undo2 } from 'lucide-vue-next'
import { PageHeader, SectionCard, StatTile } from '@/components/ui'
import {
  fetchPlayerBlacklistConfig,
  savePlayerBlacklistConfig,
  type PlayerBlacklistConfig,
  type PlayerBlacklistRule,
} from '@/api/admin'

/** 编辑中的一条规则：match / exclude 在输入框里是逗号/换行分隔的文本 */
interface RuleDraft {
  /** 列表渲染用的稳定键（删除中间一条时输入框状态不错位），不回传后端 */
  uid: number
  label: string
  matchText: string
  excludeText: string
}

let nextUid = 1

const data = ref<PlayerBlacklistConfig | null>(null)
const loading = ref(false)
/** 首次加载失败：给出可重试的错误态，而不是一页空白 */
const loadError = ref(false)
const saving = ref(false)
const restoring = ref(false)

const drafts = ref<RuleDraft[]>([])

/** 关键词数组 → 输入框文本（回填时就是管理员当初填的样子） */
function toText(list: string[]): string {
  return (list || []).join(', ')
}

/** 输入框文本 → 关键词数组：逗号/中文逗号/分号/换行都能分隔，去空白与空项 */
function toPatterns(text: string): string[] {
  return text
    .split(/[,，;；\n\r]+/)
    .map((p) => p.trim())
    .filter(Boolean)
}

function toDrafts(rules: PlayerBlacklistRule[]): RuleDraft[] {
  return rules.map((r) => ({
    uid: nextUid++,
    label: r.label || '',
    matchText: toText(r.match),
    excludeText: toText(r.exclude),
  }))
}

function toRules(list: RuleDraft[]): PlayerBlacklistRule[] {
  return list.map((d) => ({
    label: d.label.trim(),
    match: toPatterns(d.matchText),
    exclude: toPatterns(d.excludeText),
  }))
}

/** 有没有没保存的改动：草稿归一后与当前生效值逐项比 */
const dirty = computed(() => {
  if (!data.value) return false
  return JSON.stringify(toRules(drafts.value)) !== JSON.stringify(data.value.rules)
})

/** 某条规则一个命中词都没填 = 永远不会命中，多半是漏填，提前说清（不拦保存） */
function ruleIncomplete(d: RuleDraft): boolean {
  return toPatterns(d.matchText).length === 0
}

async function load() {
  loading.value = true
  loadError.value = false
  try {
    const res = await fetchPlayerBlacklistConfig()
    data.value = res
    drafts.value = toDrafts(res.rules)
  } catch {
    /* 拦截器已提示；没有数据时显示错误态 */
    loadError.value = !data.value
  } finally {
    loading.value = false
  }
}

onMounted(load)

function addRule() {
  drafts.value.push({ uid: nextUid++, label: '', matchText: '', excludeText: '' })
}

function removeRule(idx: number) {
  drafts.value.splice(idx, 1)
}

function revert() {
  if (!data.value) return
  drafts.value = toDrafts(data.value.rules)
}

async function save() {
  saving.value = true
  try {
    const res = await savePlayerBlacklistConfig(toRules(drafts.value))
    data.value = res
    // 以后端返回的归一值回填，不自己猜
    drafts.value = toDrafts(res.rules)
    ElMessage.success(
      res.rules.length
        ? `黑名单已保存（${res.rules.length} 条规则），登录侧立即生效`
        : '黑名单已保存为空名单，功能已停用',
    )
  } catch {
    /* 拦截器已提示具体原因（如结构非法 400） */
  } finally {
    saving.value = false
  }
}

/** 恢复默认名单：删掉库里的配置行，回到代码级默认名单（与保存空数组不同） */
async function restoreDefault() {
  try {
    await ElMessageBox.confirm(
      '恢复后将删除当前保存的名单，改用系统内置的默认名单。确定恢复？',
      '恢复默认名单',
      { type: 'warning', confirmButtonText: '恢复默认', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  restoring.value = true
  try {
    const res = await savePlayerBlacklistConfig(null)
    data.value = res
    drafts.value = toDrafts(res.rules)
    ElMessage.success('已恢复默认名单，登录侧立即生效')
  } catch {
    /* 拦截器已提示 */
  } finally {
    restoring.value = false
  }
}
</script>

<template>
  <div class="admin-page player-blacklist">
    <PageHeader
      eyebrow="安全与准入"
      title="播放器黑名单"
      description="命中的播放器在客户端登录时被拒（403）。只拦登录，不影响已登录的会话与网页端。"
    >
      <template #actions>
        <el-button :loading="loading" @click="load">
          <RefreshCw :size="14" class="btn-ico" />刷新
        </el-button>
      </template>
    </PageHeader>

    <!-- 首屏骨架 -->
    <div v-if="loading && !data" class="pb-skeleton" aria-busy="true" aria-label="加载中">
      <div v-for="n in 4" :key="n" class="au-skeleton sk-tile" />
      <div class="au-skeleton sk-wide" />
    </div>

    <el-alert
      v-else-if="loadError"
      type="error"
      show-icon
      :closable="false"
      title="播放器黑名单加载失败"
      description="可能是网络或后端暂时不可用。点右上角「刷新」重试。"
    />

    <template v-if="data">
      <!-- 现在的状态：停用 / 用默认名单这两种最容易被误读，必须一眼看见 -->
      <section class="stat-grid" aria-label="当前状态">
        <StatTile
          label="当前状态"
          :icon="MonitorOff"
          :value="data.rules.length ? '拦截生效中' : '已停用'"
          :tone="data.rules.length ? 'danger' : 'plain'"
          :hint="data.rules.length ? '命中的播放器登录会收到 403' : '名单为空，任何播放器都能登录'"
          class="text-tile"
        />
        <StatTile
          label="名单来源"
          :icon="ListX"
          :value="data.using_default ? '系统默认名单' : '自定义名单'"
          :tone="data.using_default ? 'warn' : 'plain'"
          :hint="data.using_default ? '还没保存过，当前用的是内置默认名单' : '当前用的是管理员保存的名单'"
          class="text-tile"
        />
        <StatTile
          label="规则条数"
          :icon="ListX"
          :value="String(data.rules.length)"
          :hint="`命中词 ${data.rules.reduce((n, r) => n + r.match.length, 0)} 个 · 排除词 ${data.rules.reduce((n, r) => n + r.exclude.length, 0)} 个`"
          class="text-tile"
        />
        <StatTile
          label="命中提示"
          :icon="MonitorOff"
          :value="data.block_message"
          hint="被拦的客户端会看到这句话（后端固定文案）"
          class="text-tile"
        />
      </section>

      <el-alert
        v-if="!data.rules.length && !drafts.length"
        type="warning"
        show-icon
        :closable="false"
        title="当前名单为空，黑名单功能处于停用状态"
        description="这是管理员保存空名单的结果，与「恢复默认名单」不同：默认名单不会自动回来。需要拦截时在下方添加规则并保存，或点「恢复默认名单」。"
      />
    </template>

    <SectionCard
      v-if="data"
      title="规则"
      :icon="ListX"
      description="每条规则：命中词任一命中、且排除词全部未命中，才拦登录（排除优先）。改完点「保存」才写入，保存后立即生效。"
      :tone="dirty ? 'accent' : 'default'"
    >
      <template v-if="dirty" #actions>
        <span class="au-badge au-badge-amber">有未保存的改动</span>
      </template>

      <p class="pb-hint">
        匹配对象是播放器的<strong>客户端名</strong>与 <code>User-Agent</code>，做<strong>子串</strong>匹配、
        大小写不敏感。命中词与排除词用逗号或换行分隔；保存时后端会统一转成小写并去掉首尾空白。
        排除词是用来保住名字相近但要放行的客户端的——命中词写宽了先想想排除词。
      </p>

      <div v-if="!drafts.length" class="pb-empty">
        还没有规则。点下方「添加规则」新建，或点页脚「恢复默认名单」回到内置默认名单。
      </div>

      <div v-for="(d, idx) in drafts" :key="d.uid" class="pb-rule">
        <div class="pb-rule-head">
          <span class="pb-rule-title">规则 {{ idx + 1 }}</span>
          <el-button text type="danger" @click="removeRule(idx)">
            <Trash2 :size="14" class="btn-ico" />删除
          </el-button>
        </div>
        <el-form label-position="top">
          <el-form-item label="名称（备注用，如播放器名）">
            <el-input v-model="d.label" placeholder="给这条规则起个名字" />
          </el-form-item>
          <div class="pb-fields">
            <el-form-item label="命中词（任一命中即算中，如播放器的客户端名片段）">
              <el-input
                v-model="d.matchText"
                type="textarea"
                :rows="2"
                placeholder="多个词用逗号或换行分隔"
              />
            </el-form-item>
            <el-form-item label="排除词（任一命中就放行，优先于命中词）">
              <el-input
                v-model="d.excludeText"
                type="textarea"
                :rows="2"
                placeholder="可留空；多个词用逗号或换行分隔"
              />
            </el-form-item>
          </div>
        </el-form>
        <p v-if="ruleIncomplete(d)" class="pb-note is-warn">
          这条规则还没有命中词，永远不会拦住任何人——请先填命中词。
        </p>
      </div>

      <el-button @click="addRule">
        <Plus :size="14" class="btn-ico" />添加规则
      </el-button>

      <template #footer>
        <div class="pb-actions">
          <span class="pb-hint pb-hint-inline">
            保存空名单 = 功能停用；「恢复默认名单」是删掉已保存的名单、回到内置默认，两者不一样。
          </span>
          <div class="pb-actions-btns">
            <el-button :disabled="data.using_default || restoring" :loading="restoring" @click="restoreDefault">
              <RotateCcw :size="14" class="btn-ico" />恢复默认名单
            </el-button>
            <el-button :disabled="!dirty" @click="revert">
              <Undo2 :size="14" class="btn-ico" />还原
            </el-button>
            <el-button type="primary" :loading="saving" :disabled="!dirty" @click="save">
              <Save :size="14" class="btn-ico" />保存
            </el-button>
          </div>
        </div>
      </template>
    </SectionCard>
  </div>
</template>

<style scoped>
.player-blacklist { gap: 16px; }
.btn-ico { margin-right: 4px; }

/* ===== 骨架 / 网格 ===== */
.pb-skeleton,
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 12px;
}
.sk-tile { height: 96px; border-radius: var(--au-r-lg); }
.sk-wide { grid-column: 1 / -1; height: 280px; border-radius: var(--au-r-lg); }
/* 状态瓦片的值是短文字，不是数字：字号收一档 */
.text-tile :deep(.au-stat__value) { font-size: var(--ax-fs-title); }

.pb-hint {
  display: block;
  margin: 0 0 14px;
  font-size: var(--ax-fs-aux-sm);
  color: var(--au-text-2);
  line-height: 1.8;
}
.pb-hint code {
  font-family: var(--font-mono);
  padding: 0 4px;
  border-radius: var(--au-r-sm);
  background: var(--au-surface-2);
}
.pb-hint-inline { margin: 0; }

/* ===== 规则分块 ===== */
.pb-empty {
  padding: 14px;
  margin-bottom: 14px;
  border: 1px dashed var(--au-border);
  border-radius: var(--au-r-md);
  font-size: var(--ax-fs-aux-sm);
  color: var(--au-text-2);
  line-height: 1.7;
}
.pb-rule {
  padding-bottom: 16px;
  margin-bottom: 16px;
  border-bottom: 1px solid var(--au-border);
}
.pb-rule-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 4px;
}
.pb-rule-title {
  font-family: var(--au-font-serif);
  font-size: var(--ax-fs-body);
  font-weight: 600;
  color: var(--au-text);
}
.pb-fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 0 16px;
}
.pb-note {
  margin: 2px 0 0;
  font-size: var(--ax-fs-aux-sm);
  line-height: 1.7;
}
.pb-note.is-warn { color: var(--au-warning); }

.pb-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.pb-actions .pb-hint { flex: 1 1 280px; }
.pb-actions-btns { display: flex; gap: 8px; flex-wrap: wrap; }

/* 手机：操作按钮铺满 */
@media (max-width: 768px) {
  .pb-actions-btns { flex: 1 1 100%; }
  .pb-actions-btns .el-button { flex: 1 1 0; }
  .pb-rule-head { flex-wrap: wrap; }
}
</style>
