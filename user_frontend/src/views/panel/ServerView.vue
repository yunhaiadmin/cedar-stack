<template>
  <div class="sv-page">
    <div class="sv-inner">
      <header class="sv-head">
        <h1 class="sv-title">服务器</h1>
        <p class="sv-sub">连接、任务、媒体库和入库趋势</p>
      </header>

      <!-- 播放账号直达：找播放账号的人常从服务器页进来，入口常驻、不随状态加载成败 -->
      <section class="sv-sec sv-sec--entry">
        <RouterLink class="sv-entry" to="/game/operator">
          <MonitorPlay :size="25" class="sv-entry__icon" aria-hidden="true" />
          <span class="sv-entry__main">
            <span class="sv-entry__title">播放账号</span>
            <span class="sv-entry__sub">服务器地址、用户名、播放密码和一键导入都在这里</span>
          </span>
          <ChevronRight :size="18" class="sv-entry__chev" aria-hidden="true" />
        </RouterLink>
      </section>

      <!-- 顶层错误卡：失败时各区收起，只留这一张 -->
      <div v-if="server.error" class="sv-sec">
        <div class="sv-err" role="alert">
          <p>{{ server.error }}</p>
          <button class="sv-retry" :disabled="server.loading" @click="retryServer">{{ server.loading ? '正在重连…' : '重试' }}</button>
        </div>
      </div>

      <template v-if="!server.error">
        <!-- 服务器状态：会员卡式服务器卡（光带＋大字状态）＋状态行组 -->
        <section class="sv-sec">
          <h2 class="sv-sec-title">服务器状态</h2>
          <p class="sv-sec-sub">是否连上，以及当前负载</p>
          <div v-if="server.loading" class="sv-loading" role="status">
            <span class="sv-spinner" aria-hidden="true"></span>正在加载…
          </div>
          <template v-else-if="data?.vitals">
            <div class="sv-card">
              <div class="sv-card__shine" aria-hidden="true"></div>
              <div class="sv-card__top">
                <span class="sv-card__brand">
                  Aetrix
                  <span class="sv-card__chip"><span class="sv-dot" :class="`sv-dot--${statusTone}`" aria-hidden="true"></span>{{ statusChip }}</span>
                </span>
                <span class="sv-card__logo" aria-hidden="true"></span>
              </div>
              <div class="sv-card__bottom">
                <div class="sv-card__big">{{ cardBig }}</div>
                <div class="sv-card__line">{{ cardLine1 }}<br>{{ cardLine2 }}</div>
              </div>
            </div>
            <div class="sv-rows">
              <div class="sv-row">
                <Activity :size="22" class="sv-row__icon" aria-hidden="true" />
                <span class="sv-row__main"><span class="sv-row__title">状态</span></span>
                <span class="sv-row__status">
                  <span class="sv-dot" :class="`sv-dot--${connTone}`" aria-hidden="true"></span>
                  <span :class="`sv-row__status-text--${connTone}`">{{ vitalCells[0].value }}</span>
                </span>
              </div>
              <div class="sv-row">
                <ListTodo :size="22" class="sv-row__icon" aria-hidden="true" />
                <span class="sv-row__main"><span class="sv-row__title">待处理</span></span>
                <span class="sv-row__status"><span class="sv-row__status-text--plain">{{ vitalCells[1].value }} 条</span></span>
              </div>
              <div class="sv-row">
                <HeartPulse :size="22" class="sv-row__icon" aria-hidden="true" />
                <span class="sv-row__main"><span class="sv-row__title">健康等级</span></span>
                <span class="sv-row__status">
                  <span class="sv-dot" :class="`sv-dot--${levelTone}`" aria-hidden="true"></span>
                  <span :class="`sv-row__status-text--${levelTone}`">{{ vitalCells[2].value }}</span>
                </span>
              </div>
              <div class="sv-row">
                <Tag :size="22" class="sv-row__icon" aria-hidden="true" />
                <span class="sv-row__main"><span class="sv-row__title">版本</span></span>
                <span class="sv-row__status"><span class="sv-row__status-text--plain sv-mono">{{ vitalCells[3].value }}</span></span>
              </div>
            </div>
          </template>
          <div v-else class="sv-empty">
            <p class="sv-empty__title">暂未连上服务器</p>
            <p class="sv-empty__hint">点「重新检查」再试一次</p>
            <button class="sv-retry" @click="retryServer">重新检查</button>
          </div>
        </section>

        <!-- 任务：状态行组（状态点＋进行中/待命） -->
        <section class="sv-sec">
          <h2 class="sv-sec-title">任务</h2>
          <p class="sv-sec-sub">当前正在运行的任务</p>
          <div v-if="server.loading" class="sv-loading" role="status">
            <span class="sv-spinner" aria-hidden="true"></span>正在加载…
          </div>
          <template v-else-if="data?.radar_tasks?.length">
            <p class="sv-lead">{{ tasksLead }}</p>
            <div class="sv-rows">
              <div v-for="t in data.radar_tasks" :key="t.key" class="sv-row">
                <span class="sv-row__main"><span class="sv-row__title">{{ t.name }}</span></span>
                <span class="sv-row__status">
                  <span class="sv-dot" :class="t.active ? 'sv-dot--ok' : 'sv-dot--idle'" aria-hidden="true"></span>
                  <span :class="t.active ? 'sv-row__status-text--ok' : 'sv-row__status-text--idle'">{{ t.active ? '进行中' : '待命' }}</span>
                </span>
              </div>
            </div>
          </template>
          <div v-else class="sv-empty">
            <p class="sv-empty__title">当前没有运行中的任务</p>
            <p class="sv-empty__hint">有任务在跑时会显示在这里</p>
            <button class="sv-retry" @click="retryServer">重新检查</button>
          </div>
        </section>

        <!-- 媒体库：状态行组（库名＋条目数） -->
        <section class="sv-sec">
          <h2 class="sv-sec-title">媒体库</h2>
          <p class="sv-sec-sub">已配置的媒体库</p>
          <div v-if="server.loading" class="sv-loading" role="status">
            <span class="sv-spinner" aria-hidden="true"></span>正在加载…
          </div>
          <template v-else-if="data?.eco?.length">
            <div class="sv-rows">
              <div v-for="lib in data.eco" :key="lib.library_id" class="sv-row">
                <LibraryBig :size="22" class="sv-row__icon" aria-hidden="true" />
                <span class="sv-row__main"><span class="sv-row__title">{{ lib.name }}</span></span>
                <span class="sv-row__status"><span class="sv-row__status-text--plain">{{ fmtCount(lib.count) }}</span></span>
              </div>
            </div>
            <p class="sv-foot">片库每天都在新增</p>
          </template>
          <div v-else class="sv-empty">
            <p class="sv-empty__title">尚未配置媒体库</p>
            <p class="sv-empty__hint">配置完成后会显示在这里</p>
            <button class="sv-retry" @click="retryServer">重新检查</button>
          </div>
        </section>

        <!-- 入库趋势：状态行组（星期/日期＋当日数量） -->
        <section class="sv-sec">
          <h2 class="sv-sec-title">入库趋势</h2>
          <p class="sv-sec-sub">过去 7 天新入库的数量</p>
          <div v-if="server.loading" class="sv-loading" role="status">
            <span class="sv-spinner" aria-hidden="true"></span>正在加载…
          </div>
          <template v-else-if="data?.growth?.length">
            <div class="sv-rows">
              <div v-for="d in growthDays" :key="d.date" class="sv-row">
                <span class="sv-row__main">
                  <span class="sv-row__title">{{ d.weekday }}</span>
                  <span class="sv-row__desc">{{ d.date }}</span>
                </span>
                <span class="sv-row__status"><span class="sv-row__status-text--plain">{{ d.count }} 部</span></span>
              </div>
            </div>
            <p class="sv-foot">{{ growthFoot }}</p>
          </template>
          <div v-else class="sv-empty">
            <p class="sv-empty__title">近 7 天没有入库记录</p>
            <p class="sv-empty__hint">有新片入库后会显示在这里</p>
            <button class="sv-retry" @click="retryServer">重新检查</button>
          </div>
        </section>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { Activity, ChevronRight, HeartPulse, LibraryBig, ListTodo, MonitorPlay, Tag } from 'lucide-vue-next'
import { usePanelStore } from '@/stores/panel'
import type { ServerData } from '@/composables/usePanelApi'
import './server-view.css'

const panel = usePanelStore()
const { server } = storeToRefs(panel)

const data = computed(() => (server.value.data || null) as ServerData | null)

onMounted(() => {
  void panel.loadServer()
})

function retryServer() {
  void panel.loadServer(true)
}

/* ===== 以下文案/数值推导与旧 LifeOrb / RadarCanvas / EcoGarden / GrowthChart 一比一对齐：
   只换呈现（光球/雷达/花园/柱状图 → 会员卡式服务器卡＋状态行组），数据与口径不动 ===== */

const vitals = computed(() => data.value?.vitals ?? null)
const backlog = computed(() => vitals.value?.backlog ?? 0)
const level = computed(() => String(vitals.value?.level ?? '').toLowerCase())
const status = computed(() => String(vitals.value?.status ?? '').toLowerCase())
const isDown = computed(() => level.value === 'down' || status.value === 'down')
const isDegraded = computed(() => level.value === 'degraded' || status.value === 'degraded')

/** 卡面大字（旧 LifeOrb 状态行的人话口径） */
const cardBig = computed(() => {
  if (isDown.value) return '服务异常'
  if (backlog.value > 0) return '处理中'
  return '运行正常'
})
/** 卡面状态小胶囊（旧 statusLine 去掉「状态：」前缀） */
const statusChip = computed(() => {
  if (isDown.value) return '异常'
  if (backlog.value > 0) return '处理中'
  return '正常'
})
const statusTone = computed(() => (isDown.value ? 'bad' : isDegraded.value ? 'warn' : backlog.value > 0 ? 'warn' : 'ok'))
/** 状态行「状态」一项的色调只看连接本身：在线=绿、部分异常=橙、异常=红（积压的橙只归卡面与待处理） */
const connTone = computed(() => {
  const st = status.value || level.value
  if (st === 'healthy' || st === 'up') return 'ok'
  if (st === 'degraded') return 'warn'
  if (st === 'down') return 'bad'
  return 'idle'
})
/** 卡面两行说明（旧 cond 区）：第一行待处理数、第二行运行状态。
    旧组件在 down 且无积压时第二行误写「运行正常」，这里按 statusLine 同口径诚实呈现 */
const cardLine1 = computed(() => (backlog.value > 0 ? `有 ${backlog.value} 条待处理` : '暂无待处理任务'))
const cardLine2 = computed(() => {
  if (isDown.value) return '服务异常 · 正在恢复'
  if (backlog.value > 0) return '运行正常 · 正在处理'
  return '运行正常'
})

/** 健康等级人话：后端英文 → 中文，未知兜底原文 */
const levelText = computed(() => {
  if (level.value === 'healthy') return '正常'
  if (level.value === 'degraded') return '降级'
  if (level.value === 'down') return '故障'
  return '—'
})
const levelTone = computed(() => (level.value === 'down' ? 'bad' : level.value === 'degraded' ? 'warn' : level.value === 'healthy' ? 'ok' : 'idle'))

/** 旧 vitalCells 四格 → 状态行四行（状态/待处理/健康等级/版本），值与口径不变 */
const vitalCells = computed(() => {
  const st = status.value || level.value || '—'
  const stText =
    st === 'healthy' || st === 'up' ? '在线' :
    st === 'degraded' ? '部分异常' :
    st === 'down' ? '异常' : '—'
  return [
    { value: stText, label: '状态' },
    { value: backlog.value > 0 ? `${backlog.value}` : '0', label: '待处理' },
    { value: levelText.value, label: '健康等级' },
    { value: String(vitals.value?.version || '—'), label: '版本' },
  ]
})

/** 任务区首行（旧 RadarCanvas 标题口径） */
const tasks = computed(() => data.value?.radar_tasks || [])
const activeCount = computed(() => tasks.value.filter((t) => t.active).length)
const tasksLead = computed(() =>
  activeCount.value > 0 ? `服务器在忙 · ${activeCount.value} 个任务在线` : '服务器待命 · 暂无在线任务',
)

/** 数量格式化：万（旧 EcoGarden fmtCount，脏数据兜底 '0'） */
function fmtCount(n: number): string {
  if (!Number.isFinite(n)) return '0'
  if (n >= 10000) {
    const w = n / 10000
    return w >= 100 ? `${Math.round(w)}万` : `${w.toFixed(1)}万`
  }
  return `${n}`
}

/** 入库趋势：后 7 天（旧 GrowthChart 口径，total 与行数据同批求和） */
const growthDays = computed(() => (data.value?.growth || []).slice(-7))
const growthTotal = computed(() => growthDays.value.reduce((s, d) => s + (d.count || 0), 0))
const growthFoot = computed(() => {
  const t = growthTotal.value
  if (t === 0) return '这 7 天静悄悄，一部新片也没长出来'
  return `过去 7 天，一共长了 ${t} 部新片`
})
</script>

<style scoped>
/* 服务器页（UHD 办事面）：平面分组＋发丝线＋状态行组＋会员卡式服务器卡。
   本地主题令牌（--sv-*）定义在同目录 server-view.css（由本文件引入）：
   契约检查只认被引入的样式表，scoped 样式里的定义不作数。 */
.sv-page {
  min-height: 100dvh;
  background: linear-gradient(180deg, #111213 0%, #09090b 100%);
  color: var(--sv-text);
  font-family: var(--uhd-font-sans);
  -webkit-font-smoothing: antialiased;
}
.sv-inner {
  max-width: 760px;
  margin: 0 auto;
  padding: calc(14px + env(safe-area-inset-top)) 20px 150px;
  box-sizing: border-box;
}

/* 页头 */
.sv-head { padding-bottom: 6px; }
.sv-title {
  margin: 0;
  font-family: var(--uhd-font-sans);
  font-size: 32px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
  line-height: var(--uhd-lh-tight);
}
.sv-sub { margin: 8px 0 0; font-size: 14px; color: var(--sv-text-2); }

/* 分区：H2 24px/800 领起（说明书 2.6 分组口径） */
.sv-sec { margin-top: 34px; }
.sv-sec--entry { margin-top: 22px; }
.sv-sec-title {
  margin: 0;
  font-family: var(--uhd-font-sans);
  font-size: 24px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
}
.sv-sec-sub { margin: 6px 0 0; font-size: 14px; color: var(--sv-text-2); }
.sv-lead { margin: 14px 0 0; font-size: 15px; font-weight: var(--uhd-fw-medium); }
.sv-foot { margin: 12px 2px 0; font-size: 13px; color: var(--sv-text-2); }

/* 播放账号直达行（状态行形态：图标＋两行＋chevron） */
.sv-entry {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 68px;
  padding: 10px 2px;
  border-top: 1px solid var(--sv-divider);
  border-bottom: 1px solid var(--sv-divider);
  color: var(--sv-text);
  text-decoration: none;
}
.sv-entry__icon { flex: none; }
.sv-entry__main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.sv-entry__title { font-size: 16px; font-weight: var(--uhd-fw-semibold); }
.sv-entry__sub {
  font-size: 14px;
  color: var(--sv-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sv-entry__chev { flex: none; color: var(--sv-text-2); }
.sv-entry:focus-visible { outline: 2px solid var(--uhd-l-link); outline-offset: 2px; }

/* 服务器卡：会员卡模具（说明书 2.5/§3 行 23）——圆角 28、斜向光带＋细斜纹、
   左上品牌＋状态小胶囊、右上彩虹三角、底部大字状态＋说明行。
   渐变停点＝实测清单 §1（浅）/§1b（深）像素采样串，与概览页会员卡同串 */
.sv-card {
  position: relative;
  overflow: hidden;
  margin-top: 16px;
  min-height: 218px;
  border-radius: 28px;
  padding: 24px 26px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  background:
    linear-gradient(115deg, #2b2d2f 0%, #252629 6%, #2e2e34 12%, #35333d 18%, #3e3e45 24%, #433e4d 29%, #484255 34%, #4f4a5e 39%, #605a76 43%, #655f7d 46%, #716b8e 49%, #79769c 52%, #74759a 57%, #6d7599 62%, #697498 67%, #677598 72%, #5f7898 77%, #4e6781 82%, #445a70 86%, #3f5367 90%, #354556 93%, #313e4c 96%, #1e2328 98%, #161522 100%);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.32);
  color: #ffffff;
}
.sv-card__shine {
  position: absolute;
  inset: 0;
  background: repeating-linear-gradient(115deg, rgba(255, 255, 255, 0.1) 0 1px, transparent 1px 4.2px);
  -webkit-mask-image: linear-gradient(115deg, transparent 30%, #000 58%);
  mask-image: linear-gradient(115deg, transparent 30%, #000 58%);
  pointer-events: none;
}
.sv-card__top {
  position: relative;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.sv-card__brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-size: 16px;
  font-weight: var(--uhd-fw-bold);
}
.sv-card__chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 999px;
  background: var(--sv-chip-bg);
  font-size: 12px;
  font-weight: var(--uhd-fw-semibold);
}
.sv-card__logo {
  flex: none;
  width: 25px;
  height: 25px;
  background: var(--uhd-rainbow-conic);
  clip-path: polygon(50% 8%, 92% 88%, 8% 88%);
}
.sv-card__bottom { position: relative; margin-top: auto; padding-top: 48px; }
.sv-card__big {
  font-size: 32px;
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
  line-height: 1.15;
}
.sv-card__line { margin-top: 10px; font-size: 14px; line-height: 1.6; color: rgba(255, 255, 255, 0.62); }

/* 状态行组（说明书 2.6）：行高 64–72、左图标 22、标题 16 粗、副文 14 灰、
   右状态点＋文字，行间 1px 发丝线 */
.sv-rows { margin-top: 18px; border-top: 1px solid var(--sv-divider); }
.sv-row {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 64px;
  padding: 10px 2px;
  border-bottom: 1px solid var(--sv-divider);
  box-sizing: border-box;
}
.sv-row__icon { flex: none; }
.sv-row__main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.sv-row__title {
  font-size: 16px;
  font-weight: var(--uhd-fw-semibold);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sv-row__desc { font-size: 14px; color: var(--sv-text-2); }
.sv-row__status { display: inline-flex; align-items: center; gap: 7px; flex: none; font-size: 14px; }
.sv-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.sv-dot--ok { background: var(--uhd-dot-ok); }
.sv-dot--idle { background: var(--uhd-dot-idle); }
.sv-dot--warn { background: var(--uhd-dot-doing); }
.sv-dot--bad { background: var(--uhd-dot-bad); }
.sv-row__status-text--ok { color: var(--sv-ok); }
.sv-row__status-text--warn { color: var(--uhd-warn-text-2); }
.sv-row__status-text--bad { color: var(--uhd-danger); }
.sv-row__status-text--idle,
.sv-row__status-text--plain { color: var(--sv-text-2); }
.sv-mono { font-family: var(--uhd-font-mono); font-size: 13px; }

/* 加载态（说明书 1.6：旋转圈＋「正在加载…」灰字） */
.sv-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 16px;
  font-size: 14px;
  color: var(--sv-text-2);
}
.sv-spinner {
  width: 18px;
  height: 18px;
  flex: none;
  border-radius: 50%;
  border: 2px solid var(--sv-divider);
  border-top-color: var(--sv-text-2);
  animation: uhd-spin 0.8s linear infinite;
}

/* 空态虚线卡（说明书 2.7 形态：虚线边框、圆角 16、居中灰字＋动作钮） */
.sv-empty {
  margin-top: 16px;
  padding: 32px 24px;
  border: 1.5px dashed var(--sv-divider);
  border-radius: 16px;
  text-align: center;
}
.sv-empty__title { margin: 0; font-size: 15px; color: var(--sv-text-2); }
.sv-empty__hint { margin: 6px 0 0; font-size: 13px; color: var(--sv-text-2); }
.sv-empty .sv-retry { margin-top: 14px; }

/* 错误卡 */
.sv-err {
  padding: 20px;
  border: 1px solid var(--sv-divider);
  border-radius: 16px;
  background: var(--sv-surface);
  font-size: 14px;
  line-height: 1.7;
}
.sv-err p { margin: 0 0 12px; }

/* 次按钮小胶囊（说明书 2.2：白底＋1px 灰边；深色面透明底＋边框＋白字） */
.sv-retry {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 38px;
  padding: 0 18px;
  border-radius: 999px;
  border: 1px solid var(--sv-divider);
  background: transparent;
  color: var(--sv-text);
  font-family: var(--uhd-font-sans);
  font-size: 14px;
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
}
.sv-retry:disabled { opacity: 0.55; cursor: not-allowed; }
.sv-retry:focus-visible { outline: 2px solid var(--uhd-l-link); outline-offset: 2px; }

/* 浅色覆盖：跟随用户外观（html[data-theme='light']，实测清单 §1） */
html[data-theme='light'] .sv-page {
  background: linear-gradient(180deg, #f8f8f9 0%, #f1f2f3 100%);
}
html[data-theme='light'] .sv-card {
  background:
    linear-gradient(115deg, #fcfcfc 0%, #f4f0fa 14%, #ede7f7 25%, #e4dbf5 34%, #d7cdf2 42%, #c9bfef 48%, #c2b9ee 54%, #bdb6ee 59%, #b7b5ef 63%, #b0b6f0 67%, #a8b9f0 70%, #a0bbf1 73%, #d0dff9 76%, #a6c4f4 79%, #d7e6fa 82%, #b7d1f6 85%, #bed7f7 88%, #e1edfb 91%, #ccdff9 94%, #dae8fa 96%, #f2f7fd 98%, #fbfcfd 100%);
  box-shadow: 0 8px 22px rgba(90, 94, 150, 0.08);
  color: #111111;
}
html[data-theme='light'] .sv-card__line { color: #55575c; }
html[data-theme='light'] .sv-retry { background: #ffffff; }

@media (max-width: 480px) {
  .sv-card__big { font-size: 28px; }
  .sv-entry__sub { white-space: normal; }
}
</style>
