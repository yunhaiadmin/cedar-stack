<script setup lang="ts">
/**
 * 官网内容 —— 官网落地页（未登录首页）整页文案编辑。
 *
 * 落点是独立页（内容组）：落地页文本原先写死在用户端 LandingView
 * 里，改字要发版；现存 SystemConfig 单键 landing_content（家族
 * landing），本页整份 GET/PUT，保存即生效（用户端公开端点直查）。
 * 没保存过自定义时表单里就是现行默认文案，改哪句存哪句；「恢复
 * 默认」删掉自定义行，官网回落默认文案。
 *
 * 版式口径（不进本页）：卖点卡恒 3 张只改文案；规格 chip 与要点
 * 的图标按位序固定；合作方是纯文字字标（无 logo 图），名单可增
 * 删排序，新平台走基础字标样式；Hero 背景图、页脚免责声明不在
 * 本页范围。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowDown,
  ArrowUp,
  MonitorSmartphone,
  Plus,
  RotateCcw,
  Save,
  Trash2,
} from 'lucide-vue-next'
import { PageHeader, SectionCard } from '@/components/ui'
import {
  fetchLandingContent,
  resetLandingContent,
  updateLandingContent,
  type LandingContent,
  type LandingPoint,
  type LandingSpec,
} from '@/api/landing'
import { toAdminError } from '@/utils/errorMessage'

const loading = ref(false)
const saving = ref(false)
const hasCustom = ref(false)

function blankContent(): LandingContent {
  return {
    hero: { title_lines: [], subs: [], scroll_hint: '', specs: [] },
    partners: { eyebrow: '', names: [] },
    devices: { eyebrow: '', title_lines: [], sub: '', marquee_clients: [], marquee_specs: [] },
    showcase: {
      cinema: { eyebrow: '', title: '', sub: '' },
      audio: { eyebrow: '', title: '', sub: '' },
    },
    essentials: { eyebrow: '', title: '', sub: '', cards: [] },
  }
}

const form = reactive<LandingContent>(blankContent())

function applyConfig(config: LandingContent, custom: boolean) {
  const copy = JSON.parse(JSON.stringify(config)) as LandingContent
  Object.assign(form, copy)
  hasCustom.value = custom
}

async function load() {
  loading.value = true
  try {
    const res = await fetchLandingContent()
    applyConfig(res.config, res.has_custom)
  } catch (e) {
    ElMessage.error(toAdminError(e, '官网文案加载失败'))
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const res = await updateLandingContent(JSON.parse(JSON.stringify(form)))
    applyConfig(res.config, res.has_custom)
    ElMessage.success('已保存，官网立即生效')
  } catch (e) {
    ElMessage.error(toAdminError(e, '保存失败'))
  } finally {
    saving.value = false
  }
}

async function resetAll() {
  try {
    await ElMessageBox.confirm(
      '将删除自定义文案，官网立即回落到默认文案。确定恢复默认？',
      '恢复默认文案',
      { type: 'warning', confirmButtonText: '恢复默认', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  saving.value = true
  try {
    const res = await resetLandingContent()
    applyConfig(res.config, res.has_custom)
    ElMessage.success('已恢复默认文案')
  } catch (e) {
    ElMessage.error(toAdminError(e, '恢复默认失败'))
  } finally {
    saving.value = false
  }
}

/* ---------- 列表编辑小工具（名单/词表/多行文本共用） ---------- */

function addLine(list: string[], max: number, label: string) {
  if (list.length >= max) {
    ElMessage.warning(`${label}最多 ${max} 条`)
    return
  }
  list.push('')
}

function removeAt<T>(list: T[], idx: number) {
  list.splice(idx, 1)
}

function moveAt<T>(list: T[], idx: number, dir: -1 | 1) {
  const target = idx + dir
  if (target < 0 || target >= list.length) return
  const [item] = list.splice(idx, 1)
  list.splice(target, 0, item as T)
}

function addSpec(list: LandingSpec[]) {
  if (list.length >= 4) {
    ElMessage.warning('规格 chip 最多 4 条')
    return
  }
  list.push({ value: '', sub: '' })
}

function addPoint(list: LandingPoint[]) {
  if (list.length >= 5) {
    ElMessage.warning('要点最多 5 条')
    return
  }
  list.push({ title: '', text: '' })
}

const statusText = computed(() =>
  hasCustom.value ? '当前使用自定义文案' : '当前使用默认文案（尚未保存过自定义）',
)

onMounted(load)
</script>

<template>
  <div class="landing-page" v-loading="loading">
    <PageHeader
      eyebrow="内容"
      title="官网内容"
      description="官网落地页（未登录首页）的全部文案：首屏、合作方、设备区、展示区与卖点三卡。保存后立即生效；合作方为纯文字字标名单，可增删排序（无 logo 图片）。"
    >
      <template #actions>
        <el-button :disabled="saving" @click="resetAll">
          <RotateCcw :size="15" /> 恢复默认
        </el-button>
        <el-button type="primary" :loading="saving" @click="save">
          <Save :size="15" /> 保存文案
        </el-button>
      </template>
    </PageHeader>

    <p class="landing-status">{{ statusText }}</p>

    <SectionCard title="首屏" :icon="MonitorSmartphone" description="主标题逐行显示；副文案多条时每 4 秒轮换一条；规格 chip 图标按顺序固定，只改文字。">
      <div class="fld">
        <span class="fld-label">主标题（每行一条，最多 3 行）</span>
        <div v-for="(_line, i) in form.hero.title_lines" :key="i" class="row-line">
          <el-input v-model="form.hero.title_lines[i]" maxlength="40" show-word-limit placeholder="如：你的私人影院" />
          <el-button text :icon="ArrowUp" :disabled="i === 0" @click="moveAt(form.hero.title_lines, i, -1)" />
          <el-button text :icon="ArrowDown" :disabled="i === form.hero.title_lines.length - 1" @click="moveAt(form.hero.title_lines, i, 1)" />
          <el-button text type="danger" :icon="Trash2" @click="removeAt(form.hero.title_lines, i)" />
        </div>
        <el-button text type="primary" :icon="Plus" @click="addLine(form.hero.title_lines, 3, '主标题')">加一行</el-button>
      </div>

      <div class="fld">
        <span class="fld-label">副文案轮换（至少 1 条，最多 8 条）</span>
        <div v-for="(_line, i) in form.hero.subs" :key="i" class="row-line">
          <el-input v-model="form.hero.subs[i]" maxlength="80" show-word-limit placeholder="如：无广告干扰，沉浸观影从按下播放开始" />
          <el-button text :icon="ArrowUp" :disabled="i === 0" @click="moveAt(form.hero.subs, i, -1)" />
          <el-button text :icon="ArrowDown" :disabled="i === form.hero.subs.length - 1" @click="moveAt(form.hero.subs, i, 1)" />
          <el-button text type="danger" :icon="Trash2" @click="removeAt(form.hero.subs, i)" />
        </div>
        <el-button text type="primary" :icon="Plus" @click="addLine(form.hero.subs, 8, '副文案')">加一条</el-button>
      </div>

      <div class="fld">
        <span class="fld-label">滚动提示</span>
        <el-input v-model="form.hero.scroll_hint" maxlength="40" show-word-limit class="fld-narrow" />
      </div>

      <div class="fld">
        <span class="fld-label">规格 chip（最多 4 条：大字＋小字）</span>
        <div v-for="(spec, i) in form.hero.specs" :key="i" class="row-line">
          <el-input v-model="spec.value" maxlength="24" placeholder="大字，如 4K" class="fld-pair" />
          <el-input v-model="spec.sub" maxlength="24" placeholder="小字，如 超高清" class="fld-pair" />
          <el-button text :icon="ArrowUp" :disabled="i === 0" @click="moveAt(form.hero.specs, i, -1)" />
          <el-button text :icon="ArrowDown" :disabled="i === form.hero.specs.length - 1" @click="moveAt(form.hero.specs, i, 1)" />
          <el-button text type="danger" :icon="Trash2" @click="removeAt(form.hero.specs, i)" />
        </div>
        <el-button text type="primary" :icon="Plus" @click="addSpec(form.hero.specs)">加一条</el-button>
      </div>
    </SectionCard>

    <SectionCard title="合作方" description="STREAMING PARTNERS 平台名单，顺序即显示顺序；名单清空则整行不在官网显示。字标为纯文字排版，新平台自动用基础样式。">
      <div class="fld">
        <span class="fld-label">节眉题</span>
        <el-input v-model="form.partners.eyebrow" maxlength="40" show-word-limit class="fld-narrow" />
      </div>
      <div class="fld">
        <span class="fld-label">平台名单（最多 10 个）</span>
        <div v-for="(_name, i) in form.partners.names" :key="i" class="row-line">
          <el-input v-model="form.partners.names[i]" maxlength="32" show-word-limit placeholder="如：Netflix" />
          <el-button text :icon="ArrowUp" :disabled="i === 0" @click="moveAt(form.partners.names, i, -1)" />
          <el-button text :icon="ArrowDown" :disabled="i === form.partners.names.length - 1" @click="moveAt(form.partners.names, i, 1)" />
          <el-button text type="danger" :icon="Trash2" @click="removeAt(form.partners.names, i)" />
        </div>
        <el-button text type="primary" :icon="Plus" @click="addLine(form.partners.names, 10, '合作方')">加一个平台</el-button>
      </div>
    </SectionCard>

    <SectionCard title="设备区" description="「在你顺手的设备上」一节：标题逐字入场动画按行拆分；两条跑马灯词表横向滚动展示。">
      <div class="fld">
        <span class="fld-label">节眉题</span>
        <el-input v-model="form.devices.eyebrow" maxlength="40" show-word-limit class="fld-narrow" />
      </div>
      <div class="fld">
        <span class="fld-label">标题（每行一条，最多 3 行）</span>
        <div v-for="(_line, i) in form.devices.title_lines" :key="i" class="row-line">
          <el-input v-model="form.devices.title_lines[i]" maxlength="40" show-word-limit placeholder="如：在你顺手的设备上，" />
          <el-button text :icon="ArrowUp" :disabled="i === 0" @click="moveAt(form.devices.title_lines, i, -1)" />
          <el-button text :icon="ArrowDown" :disabled="i === form.devices.title_lines.length - 1" @click="moveAt(form.devices.title_lines, i, 1)" />
          <el-button text type="danger" :icon="Trash2" @click="removeAt(form.devices.title_lines, i)" />
        </div>
        <el-button text type="primary" :icon="Plus" @click="addLine(form.devices.title_lines, 3, '设备区标题')">加一行</el-button>
      </div>
      <div class="fld">
        <span class="fld-label">副文案</span>
        <el-input v-model="form.devices.sub" type="textarea" :rows="2" maxlength="160" show-word-limit />
      </div>
      <div class="fld">
        <span class="fld-label">跑马灯 · 客户端词（最多 24 个）</span>
        <div v-for="(_word, i) in form.devices.marquee_clients" :key="i" class="row-line">
          <el-input v-model="form.devices.marquee_clients[i]" maxlength="32" show-word-limit placeholder="如：Emby" />
          <el-button text :icon="ArrowUp" :disabled="i === 0" @click="moveAt(form.devices.marquee_clients, i, -1)" />
          <el-button text :icon="ArrowDown" :disabled="i === form.devices.marquee_clients.length - 1" @click="moveAt(form.devices.marquee_clients, i, 1)" />
          <el-button text type="danger" :icon="Trash2" @click="removeAt(form.devices.marquee_clients, i)" />
        </div>
        <el-button text type="primary" :icon="Plus" @click="addLine(form.devices.marquee_clients, 24, '跑马灯词')">加一个词</el-button>
      </div>
      <div class="fld">
        <span class="fld-label">跑马灯 · 规格词（最多 24 个）</span>
        <div v-for="(_word, i) in form.devices.marquee_specs" :key="i" class="row-line">
          <el-input v-model="form.devices.marquee_specs[i]" maxlength="32" show-word-limit placeholder="如：4K Ultra HD" />
          <el-button text :icon="ArrowUp" :disabled="i === 0" @click="moveAt(form.devices.marquee_specs, i, -1)" />
          <el-button text :icon="ArrowDown" :disabled="i === form.devices.marquee_specs.length - 1" @click="moveAt(form.devices.marquee_specs, i, 1)" />
          <el-button text type="danger" :icon="Trash2" @click="removeAt(form.devices.marquee_specs, i)" />
        </div>
        <el-button text type="primary" :icon="Plus" @click="addLine(form.devices.marquee_specs, 24, '跑马灯词')">加一个词</el-button>
      </div>
    </SectionCard>

    <SectionCard title="展示区" description="钉住滚动展示的两块文案：影院（画面）与音频（声音）。">
      <div class="fld">
        <span class="fld-label">影院块 · 眉题</span>
        <el-input v-model="form.showcase.cinema.eyebrow" maxlength="40" show-word-limit class="fld-narrow" />
      </div>
      <div class="fld">
        <span class="fld-label">影院块 · 标题</span>
        <el-input v-model="form.showcase.cinema.title" maxlength="30" show-word-limit class="fld-narrow" />
      </div>
      <div class="fld">
        <span class="fld-label">影院块 · 副文案</span>
        <el-input v-model="form.showcase.cinema.sub" maxlength="80" show-word-limit />
      </div>
      <div class="fld">
        <span class="fld-label">音频块 · 眉题</span>
        <el-input v-model="form.showcase.audio.eyebrow" maxlength="40" show-word-limit class="fld-narrow" />
      </div>
      <div class="fld">
        <span class="fld-label">音频块 · 标题</span>
        <el-input v-model="form.showcase.audio.title" maxlength="30" show-word-limit class="fld-narrow" />
      </div>
      <div class="fld">
        <span class="fld-label">音频块 · 副文案</span>
        <el-input v-model="form.showcase.audio.sub" maxlength="80" show-word-limit />
      </div>
    </SectionCard>

    <SectionCard title="卖点区" description="「一份订阅，全部搞定」三张卡：版式固定为 3 张，只改文案；卡内设备示意、数据行（实时统计）不在编辑范围。">
      <div class="fld">
        <span class="fld-label">节眉题</span>
        <el-input v-model="form.essentials.eyebrow" maxlength="40" show-word-limit class="fld-narrow" />
      </div>
      <div class="fld">
        <span class="fld-label">节标题</span>
        <el-input v-model="form.essentials.title" maxlength="40" show-word-limit class="fld-narrow" />
      </div>
      <div class="fld">
        <span class="fld-label">节副文案</span>
        <el-input v-model="form.essentials.sub" type="textarea" :rows="2" maxlength="160" show-word-limit />
      </div>

      <div v-for="(card, ci) in form.essentials.cards" :key="ci" class="card-block">
        <h4 class="card-block-title">卖点卡 {{ ci + 1 }}</h4>
        <div class="fld">
          <span class="fld-label">眉题</span>
          <el-input v-model="card.eyebrow" maxlength="40" show-word-limit class="fld-narrow" />
        </div>
        <div class="fld">
          <span class="fld-label">标题（每行一条，最多 3 行）</span>
          <div v-for="(_line, i) in card.title_lines" :key="i" class="row-line">
            <el-input v-model="card.title_lines[i]" maxlength="40" show-word-limit />
            <el-button text :icon="ArrowUp" :disabled="i === 0" @click="moveAt(card.title_lines, i, -1)" />
            <el-button text :icon="ArrowDown" :disabled="i === card.title_lines.length - 1" @click="moveAt(card.title_lines, i, 1)" />
            <el-button text type="danger" :icon="Trash2" @click="removeAt(card.title_lines, i)" />
          </div>
          <el-button text type="primary" :icon="Plus" @click="addLine(card.title_lines, 3, '卡标题')">加一行</el-button>
        </div>
        <div class="fld">
          <span class="fld-label">描述</span>
          <el-input v-model="card.desc" type="textarea" :rows="2" maxlength="200" show-word-limit />
        </div>
        <div class="fld">
          <span class="fld-label">小标签 chips（最多 8 个，可留空）</span>
          <div v-for="(_chip, i) in card.chips" :key="i" class="row-line">
            <el-input v-model="card.chips[i]" maxlength="24" show-word-limit placeholder="如：TV" />
            <el-button text :icon="ArrowUp" :disabled="i === 0" @click="moveAt(card.chips, i, -1)" />
            <el-button text :icon="ArrowDown" :disabled="i === card.chips.length - 1" @click="moveAt(card.chips, i, 1)" />
            <el-button text type="danger" :icon="Trash2" @click="removeAt(card.chips, i)" />
          </div>
          <el-button text type="primary" :icon="Plus" @click="addLine(card.chips, 8, '小标签')">加一个标签</el-button>
        </div>
        <div class="fld">
          <span class="fld-label">要点（小标题＋说明，最多 5 条，可留空）</span>
          <div v-for="(pt, i) in card.points" :key="i" class="row-line">
            <el-input v-model="pt.title" maxlength="80" placeholder="小标题，如 自动入库" class="fld-pair" />
            <el-input v-model="pt.text" maxlength="80" placeholder="说明，如 新片到盘，自动扫描进片库" class="fld-pair" />
            <el-button text :icon="ArrowUp" :disabled="i === 0" @click="moveAt(card.points, i, -1)" />
            <el-button text :icon="ArrowDown" :disabled="i === card.points.length - 1" @click="moveAt(card.points, i, 1)" />
            <el-button text type="danger" :icon="Trash2" @click="removeAt(card.points, i)" />
          </div>
          <el-button text type="primary" :icon="Plus" @click="addPoint(card.points)">加一条要点</el-button>
        </div>
      </div>
    </SectionCard>

    <div class="landing-foot">
      <el-button :disabled="saving" @click="resetAll">
        <RotateCcw :size="15" /> 恢复默认
      </el-button>
      <el-button type="primary" :loading="saving" @click="save">
        <Save :size="15" /> 保存文案
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.landing-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.landing-status {
  margin: 0;
  font-size: var(--uhd-fs-caption);
  color: var(--el-text-color-secondary);
}

.fld {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 18px;
}

.fld:last-child {
  margin-bottom: 0;
}

.fld-label {
  font-size: var(--uhd-fs-caption);
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.fld-narrow {
  max-width: 420px;
}

.fld-pair {
  flex: 1;
  min-width: 0;
}

.row-line {
  display: flex;
  align-items: center;
  gap: 6px;
  max-width: 640px;
}

.row-line .el-input {
  flex: 1;
  min-width: 0;
}

.card-block {
  border-top: 1px solid var(--el-border-color-lighter);
  padding-top: 16px;
  margin-top: 8px;
}

.card-block-title {
  margin: 0 0 14px;
  font-size: var(--uhd-fs-body);
  font-weight: 600;
}

.landing-foot {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
</style>
