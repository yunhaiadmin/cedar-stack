<script setup lang="ts">
/**
 * LineSpeedtestView.vue —— 线路测速（用户中心 · 帮助组）
 *
 * 结构照 UHDNow 测速页 1:1（2026-11-13 用户发图亲点）：
 *   卡一 测试：线路下拉（选项来自站内真实线路记录）＋文件大小
 *        三档胶囊（32M/64M/100M）＋整宽彩虹「开始测速」；
 *   卡二 Download：大字当前速度（平均 Mbps）＋峰值/实时/下载速率/
 *        已下载四格＋真实采样速度曲线；
 *   卡三 网络信息：服务端回显的来源 IP（已打码）/地区/运营商；
 *   卡四 代理规则提示：按线路类型切换的提示条＋代理/直连两块
 *        按所选线路域名真实生成的可复制规则（模板在
 *        utils/speedtest.ts 单一真源）。
 *
 * 测速口径（如实标注，不冒充）：
 *   从当前浏览器向所选线路入口的 /speedtest/bin 真下载所选档位
 *   大小的字节（EA 提供的测速端点，档位与服务端一一对应），边下
 *   边按真实字节数计量：大字=平均速度、实时=最近采样窗瞬时速度、
 *   峰值=瞬时最大值、曲线=采样窗瞬时速度序列。线路入口是跨域
 *   地址，需要对方开启 CORS 才能读流计量；读不到（未开 CORS /
 *   入口没有测速端点 / 连接失败）一律如实报错，不补假数。
 *   单次测速最长 180 秒，超时中止并如实标注。
 *
 * 线路清单只来自现行配置下发，不写死任何线路名/地址：
 *   - 播放线路：账号卡 GET /api/user/emby/server 的 realms[].base_url；
 *   - 专属线路：GET /api/user/exclusive-lines（够格才下发）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  Check, ChevronDown, Copy, Gauge, Globe, HardDrive,
  Info, LoaderCircle, MapPin, Radio, Wifi,
} from 'lucide-vue-next'
import UhdAccountLayout from '@/components/uhd/UhdAccountLayout.vue'
import UhdButton from '@/components/uhd/UhdButton.vue'
import UhdEmptyState from '@/components/uhd/UhdEmptyState.vue'
import { embyApi } from '@/api'
import { getMyExclusiveLines, getMyNetworkInfo, type MyNetworkInfo } from '@/api/user'
import {
  SPEEDTEST_SIZES_MB, buildDirectRules, buildProxyRules, formatBytes,
  lineKindOf, proxyHintFor, type LineKind, type SpeedtestSizeMb,
} from '@/utils/speedtest'

interface LineOption {
  key: string
  name: string
  url: string
  host: string
  kind: LineKind
}

type Phase = 'idle' | 'testing' | 'done' | 'error' | 'stopped'

const TEST_TIMEOUT_MS = 180_000
const SAMPLE_MS = 250

const loading = ref(true)
const loadFailed = ref(false)
const lines = ref<LineOption[]>([])
const selectedKey = ref('')
const sizeMb = ref<SpeedtestSizeMb>(32)

const phase = ref<Phase>('idle')
const errorText = ref('')
const downloadedBytes = ref(0)
const elapsedMs = ref(0)
const instantMbps = ref(0)
const peakMbps = ref(0)
interface Sample { t: number; mbps: number }
const samples = ref<Sample[]>([])

const netInfo = ref<MyNetworkInfo | null>(null)

const copied = ref<'proxy' | 'direct' | null>(null)
let copiedTimer: ReturnType<typeof setTimeout> | null = null

let abortCtrl: AbortController | null = null
let sampleTimer: ReturnType<typeof setInterval> | null = null
let timeoutTimer: ReturnType<typeof setTimeout> | null = null

const selectedLine = computed(() => lines.value.find(l => l.key === selectedKey.value) ?? null)
const testing = computed(() => phase.value === 'testing')

const avgMbps = computed(() => {
  if (elapsedMs.value <= 0) return 0
  return (downloadedBytes.value * 8) / (elapsedMs.value / 1000) / 1e6
})
const avgMBs = computed(() => {
  if (elapsedMs.value <= 0) return 0
  return downloadedBytes.value / (elapsedMs.value / 1000) / 1e6
})
const bigMbps = computed(() => (phase.value === 'idle' ? 0 : avgMbps.value))

const proxyRules = computed(() => (selectedLine.value ? buildProxyRules(selectedLine.value.host) : ''))
const directRules = computed(() => (selectedLine.value ? buildDirectRules(selectedLine.value.host) : ''))
const hintText = computed(() => (selectedLine.value ? proxyHintFor(selectedLine.value.kind) : ''))

/* ---------- 曲线（真实采样点；空态不画线，只给提示） ---------- */
const CHART_W = 340
const CHART_H = 196
const PAD_L = 36
const PAD_R = 8
const PAD_T = 10
const PAD_B = 22

const yMax = computed(() => {
  const peak = Math.max(10, peakMbps.value)
  const steps = [10, 20, 50, 100, 200, 500, 1000]
  return steps.find(s => s >= peak) ?? Math.ceil(peak / 1000) * 1000
})
const yTicks = computed(() => {
  const m = yMax.value
  return [0, 0.25, 0.5, 0.75, 1].map(f => ({ v: m * f, y: PAD_T + (CHART_H - PAD_T - PAD_B) * (1 - f) }))
})
const xTicks = computed(() => {
  const dur = Math.max(1, samples.value.length ? samples.value[samples.value.length - 1].t : elapsedMs.value / 1000)
  return [0, 0.25, 0.5, 0.75, 1].map(f => ({
    v: dur * f,
    x: PAD_L + (CHART_W - PAD_L - PAD_R) * f,
  }))
})
function chartPoint(s: Sample): [number, number] {
  const dur = Math.max(1, samples.value.length ? samples.value[samples.value.length - 1].t : 1)
  const x = PAD_L + (CHART_W - PAD_L - PAD_R) * (s.t / dur)
  const y = PAD_T + (CHART_H - PAD_T - PAD_B) * (1 - Math.min(s.mbps, yMax.value) / yMax.value)
  return [x, y]
}
const curvePath = computed(() => {
  if (samples.value.length < 2) return ''
  return samples.value.map((s, i) => {
    const [x, y] = chartPoint(s)
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
})
const areaPath = computed(() => {
  if (samples.value.length < 2) return ''
  const base = CHART_H - PAD_B
  const [x0] = chartPoint(samples.value[0])
  const [x1] = chartPoint(samples.value[samples.value.length - 1])
  return `${curvePath.value} L${x1.toFixed(1)},${base} L${x0.toFixed(1)},${base} Z`
})

/* ---------- 线路清单 ---------- */
function hostOf(url: string): string {
  try {
    // 规则与分类只认主机名：DOMAIN 语法不带端口，带上端口规则无效。
    return new URL(url).hostname
  } catch {
    return url
  }
}

function makeOption(name: string, url: string): LineOption {
  const host = hostOf(url)
  return { key: url, name: name || host, url, host, kind: lineKindOf(name || '', host) }
}

async function loadLines() {
  loading.value = true
  loadFailed.value = false
  const rows: LineOption[] = []
  const seen = new Set<string>()
  let cardOk = false
  try {
    const card = await embyApi.getAccountCard()
    cardOk = true
    for (const r of card.realms ?? []) {
      const url = (r.base_url ?? '').trim()
      if (!url || seen.has(url)) continue
      seen.add(url)
      rows.push(makeOption(r.name, url))
    }
    const top = (card.base_url ?? '').trim()
    if (top && !seen.has(top)) {
      seen.add(top)
      rows.push(makeOption(card.realm_name || card.server_name || '主线路', top))
    }
  } catch { /* 账号卡失败：专属线路还能单独成页；两边都空才算加载失败 */ }
  const mine = await getMyExclusiveLines()
  for (const l of mine) {
    const url = (l.entry_url ?? '').trim()
    if (!url || seen.has(url)) continue
    seen.add(url)
    rows.push(makeOption(l.name, url))
  }
  lines.value = rows
  if (rows.length && !rows.some(r => r.key === selectedKey.value)) {
    selectedKey.value = rows[0].key
  }
  loadFailed.value = !cardOk && rows.length === 0
  loading.value = false
}

/* ---------- 测速 ---------- */
function resetResult() {
  downloadedBytes.value = 0
  elapsedMs.value = 0
  instantMbps.value = 0
  peakMbps.value = 0
  samples.value = []
  errorText.value = ''
}

function stopTimers() {
  if (sampleTimer) { clearInterval(sampleTimer); sampleTimer = null }
  if (timeoutTimer) { clearTimeout(timeoutTimer); timeoutTimer = null }
}

function onSelectionChange() {
  if (testing.value) return
  phase.value = 'idle'
  resetResult()
}

async function runTest() {
  const line = selectedLine.value
  if (!line || testing.value) return
  resetResult()
  phase.value = 'testing'

  const ctrl = new AbortController()
  abortCtrl = ctrl
  let timedOut = false
  timeoutTimer = setTimeout(() => { timedOut = true; ctrl.abort() }, TEST_TIMEOUT_MS)

  const base = line.url.replace(/\/+$/, '')
  const target = `${base}/speedtest/bin?mb=${sizeMb.value}&t=${Date.now()}`
  const t0 = performance.now()
  let lastBytes = 0
  let lastT = t0

  sampleTimer = setInterval(() => {
    const now = performance.now()
    const dt = (now - lastT) / 1000
    elapsedMs.value = now - t0
    if (dt > 0) {
      const inst = ((downloadedBytes.value - lastBytes) * 8) / dt / 1e6
      instantMbps.value = inst
      if (inst > peakMbps.value) peakMbps.value = inst
      samples.value.push({ t: (now - t0) / 1000, mbps: inst })
    }
    lastBytes = downloadedBytes.value
    lastT = now
  }, SAMPLE_MS)

  try {
    const resp = await fetch(target, {
      mode: 'cors',
      cache: 'no-store',
      credentials: 'omit',
      signal: ctrl.signal,
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    if (!resp.body) throw new Error('no-stream')
    const reader = resp.body.getReader()
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      if (value) downloadedBytes.value += value.byteLength
    }
    elapsedMs.value = performance.now() - t0
    phase.value = 'done'
  } catch (e) {
    elapsedMs.value = performance.now() - t0
    if ((e as DOMException)?.name === 'AbortError') {
      phase.value = timedOut ? 'error' : 'stopped'
      if (timedOut) errorText.value = '测速超时（180 秒未下完），已按已下载部分计量。'
    } else if ((e as Error)?.message === 'no-stream') {
      phase.value = 'error'
      errorText.value = '当前浏览器不支持流式计量，无法测速。'
    } else if ((e as Error)?.message?.startsWith('HTTP ')) {
      phase.value = 'error'
      errorText.value = `线路入口没返回测速文件（${(e as Error).message}），这条线路暂时测不了。`
    } else {
      phase.value = 'error'
      errorText.value = '连接失败：线路入口不可达，或未开放跨域读取，暂时测不了。'
    }
  } finally {
    stopTimers()
    abortCtrl = null
  }
}

function stopTest() {
  abortCtrl?.abort()
}

/* ---------- 复制 ---------- */
async function copyText(kind: 'proxy' | 'direct', text: string) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
  copied.value = kind
  if (copiedTimer) clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => { copied.value = null }, 1600)
}

onMounted(() => {
  loadLines()
  getMyNetworkInfo().then(v => { netInfo.value = v })
})
onBeforeUnmount(() => {
  stopTimers()
  abortCtrl?.abort()
  if (copiedTimer) clearTimeout(copiedTimer)
})
</script>

<template>
  <UhdAccountLayout title="线路测速" eyebrow="帮助">
    <div class="st-page">
      <p class="st-lede">测试当前网络到各条线路的下载速度，结果更接近实际观看体验。</p>

      <p v-if="loading" class="st-hint">正在读取线路…</p>

      <UhdEmptyState
        v-else-if="loadFailed"
        variant="icon"
        title="线路读取失败"
        desc="没能拿到你的线路清单，请检查网络后重试。"
      >
        <template #icon><Gauge :size="24" /></template>
        <template #action>
          <UhdButton variant="primary" @click="loadLines">重试</UhdButton>
        </template>
      </UhdEmptyState>

      <UhdEmptyState
        v-else-if="!lines.length"
        variant="icon"
        title="暂无可用线路"
        desc="你的账号下暂无服务器地址与专属线路，绑定或开通后这里会列出可测的线路。"
      >
        <template #icon><Gauge :size="24" /></template>
      </UhdEmptyState>

      <template v-else>
        <!-- 卡一 · 测试 -->
        <section class="st-card">
          <label class="st-field-label" for="st-line-select">
            <Radio :size="15" />测试线路
          </label>
          <div class="st-select-wrap">
            <select
              id="st-line-select"
              v-model="selectedKey"
              class="st-select"
              :disabled="testing"
              @change="onSelectionChange"
            >
              <option v-for="l in lines" :key="l.key" :value="l.key">
                {{ l.name }} · {{ l.host }}
              </option>
            </select>
            <ChevronDown :size="16" class="st-select__chev" />
          </div>

          <span class="st-field-label">
            <HardDrive :size="15" />文件大小
          </span>
          <div class="st-seg" role="radiogroup" aria-label="文件大小">
            <button
              v-for="s in SPEEDTEST_SIZES_MB"
              :key="s"
              type="button"
              role="radio"
              :aria-checked="sizeMb === s"
              class="st-seg__btn"
              :class="{ 'is-active': sizeMb === s }"
              :disabled="testing"
              @click="sizeMb = s; onSelectionChange()"
            >
              {{ s }}M
            </button>
          </div>

          <UhdButton
            v-if="!testing"
            variant="primary"
            block
            class="st-start"
            @click="runTest"
          >
            开始测速
          </UhdButton>
          <UhdButton
            v-else
            variant="primary"
            block
            class="st-start"
            @click="stopTest"
          >
            <LoaderCircle :size="16" class="st-spin" />测速中 · 点此停止
          </UhdButton>
        </section>

        <!-- 卡二 · Download -->
        <section class="st-card">
          <div class="st-dl-head">
            <span class="st-dl-title">Download</span>
            <Info :size="15" class="st-dl-info" />
          </div>
          <div class="st-big">
            <span class="st-big__num">{{ bigMbps.toFixed(1) }}</span>
            <span class="st-big__unit">Mbps</span>
          </div>
          <div class="st-stats">
            <div class="st-stat">
              <span class="st-stat__label">峰值</span>
              <span class="st-stat__value">{{ peakMbps.toFixed(1) }} Mbps</span>
            </div>
            <div class="st-stat">
              <span class="st-stat__label">实时</span>
              <span class="st-stat__value">{{ (testing ? instantMbps : 0).toFixed(1) }} Mbps</span>
            </div>
            <div class="st-stat">
              <span class="st-stat__label">下载速率</span>
              <span class="st-stat__value">{{ avgMBs.toFixed(1) }} MB/s</span>
            </div>
            <div class="st-stat">
              <span class="st-stat__label">已下载</span>
              <span class="st-stat__value">{{ formatBytes(downloadedBytes) }}</span>
            </div>
          </div>

          <div class="st-chart">
            <svg
              :viewBox="`0 0 ${CHART_W} ${CHART_H}`"
              class="st-chart__svg"
              role="img"
              aria-label="速度曲线"
            >
              <g v-for="t in yTicks" :key="`y${t.v}`">
                <line
                  :x1="PAD_L" :x2="CHART_W - PAD_R" :y1="t.y" :y2="t.y"
                  class="st-chart__grid"
                />
                <text :x="PAD_L - 6" :y="t.y + 3" class="st-chart__ylabel" text-anchor="end">
                  {{ Math.round(t.v) }}M
                </text>
              </g>
              <g v-for="t in xTicks" :key="`x${t.v}`">
                <text :x="t.x" :y="CHART_H - 6" class="st-chart__xlabel" text-anchor="middle">
                  {{ t.v.toFixed(t.v % 1 === 0 ? 0 : 1) }}s
                </text>
              </g>
              <path v-if="areaPath" :d="areaPath" class="st-chart__area" />
              <path v-if="curvePath" :d="curvePath" class="st-chart__line" />
            </svg>
            <span v-if="!samples.length" class="st-chart__empty">开始测速后生成速度曲线</span>
          </div>

          <p v-if="phase === 'error'" class="st-error">{{ errorText }}</p>
          <p v-else-if="phase === 'stopped'" class="st-note">已停止测速，以上是停止前已下载部分的计量。</p>
          <p v-else-if="phase === 'done'" class="st-note">
            测速完成：{{ sizeMb }}M 文件共下载 {{ formatBytes(downloadedBytes) }}，
            用时 {{ (elapsedMs / 1000).toFixed(1) }} 秒。
          </p>
        </section>
      </template>

      <!-- 卡三 · 网络信息 -->
      <section v-if="!loading" class="st-card">
        <h2 class="st-card-title"><Globe :size="17" />网络信息</h2>
        <div class="st-net-rows">
          <div class="st-net-row">
            <span class="st-net-row__icon"><Globe :size="16" /></span>
            <span class="st-net-row__main">
              <span class="st-net-row__label">IP 地址</span>
              <span class="st-net-row__value">{{ netInfo?.ip_masked || '--' }}</span>
            </span>
          </div>
          <div class="st-net-row">
            <span class="st-net-row__icon"><MapPin :size="16" /></span>
            <span class="st-net-row__main">
              <span class="st-net-row__label">所在地区</span>
              <span class="st-net-row__value">{{ netInfo?.region || '--' }}</span>
            </span>
          </div>
          <div class="st-net-row">
            <span class="st-net-row__icon"><Wifi :size="16" /></span>
            <span class="st-net-row__main">
              <span class="st-net-row__label">运营商</span>
              <span class="st-net-row__value">{{ netInfo?.operator || '--' }}</span>
            </span>
          </div>
        </div>
      </section>

      <!-- 卡四 · 代理规则提示 -->
      <section v-if="!loading && selectedLine" class="st-card">
        <h2 class="st-card-title"><Info :size="17" />代理规则提示</h2>
        <p class="st-hintbar">{{ hintText }}</p>

        <div class="st-rule">
          <div class="st-rule__head">
            <span class="st-rule__title">代理规则（Surge / Clash.Meta）</span>
            <button type="button" class="st-copy" @click="copyText('proxy', proxyRules)">
              <Check v-if="copied === 'proxy'" :size="13" />
              <Copy v-else :size="13" />
              {{ copied === 'proxy' ? '已复制' : '复制' }}
            </button>
          </div>
          <pre class="st-rule__body">{{ proxyRules }}</pre>
        </div>

        <div class="st-rule">
          <div class="st-rule__head">
            <span class="st-rule__title">直连规则（Surge / Clash.Meta）</span>
            <button type="button" class="st-copy" @click="copyText('direct', directRules)">
              <Check v-if="copied === 'direct'" :size="13" />
              <Copy v-else :size="13" />
              {{ copied === 'direct' ? '已复制' : '复制' }}
            </button>
          </div>
          <pre class="st-rule__body">{{ directRules }}</pre>
        </div>

        <p class="st-foot-note">
          规则里的 PROXY 是占位策略名，导入 Surge / Clash.Meta 后改成你自己的代理策略组名即可。
          测速是从这台设备向所选线路入口真下载所选大小的文件按字节计量的；
          入口未开放跨域读取时无法计量，会如实提示。
        </p>
      </section>
    </div>
  </UhdAccountLayout>
</template>

<style scoped>
/* 用户中心框架（UhdAccountLayout）内容面为浅色令牌面（框架统一，
   与同框架页面同口径）；本页卡片/数值排版照 UHDNow 测速页截图。 */
.st-page {
  font-family: var(--uhd-font-sans);
  color: var(--uhd-l-text);
  max-width: 720px;
}
.st-lede {
  margin: 0 0 20px;
  font-size: var(--uhd-fs-body);
  color: var(--uhd-l-text-2);
}
.st-hint {
  margin: 0;
  font-size: var(--uhd-fs-body);
  color: var(--uhd-l-text-2);
}
.st-card {
  background: var(--uhd-l-surface);
  border: 1px solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-card);
  padding: 20px;
  margin-bottom: 20px;
}
.st-card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 14px;
  font-size: var(--uhd-fs-title);
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-l-text);
}

/* 卡一 */
.st-field-label {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0 0 10px;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
}
.st-field-label:not(:first-child) {
  margin-top: 20px;
}
.st-select-wrap {
  position: relative;
}
.st-select {
  width: 100%;
  height: 48px;
  padding: 0 40px 0 14px;
  border: 1px solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-input);
  background: var(--uhd-l-input-bg);
  color: var(--uhd-l-text);
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-semibold);
  appearance: none;
  -webkit-appearance: none;
  cursor: pointer;
}
.st-select:disabled {
  opacity: 0.55;
  cursor: default;
}
.st-select__chev {
  position: absolute;
  right: 14px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--uhd-l-text-3);
  pointer-events: none;
}
.st-seg {
  display: inline-flex;
  gap: 4px;
  padding: 4px;
  border-radius: var(--uhd-radius-pill);
  background: var(--uhd-l-seg-bg);
}
.st-seg__btn {
  min-width: 76px;
  height: 38px;
  padding: 0 18px;
  border: none;
  border-radius: var(--uhd-radius-pill);
  background: transparent;
  color: var(--uhd-l-text-2);
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
}
.st-seg__btn.is-active {
  background: var(--uhd-l-surface);
  color: var(--uhd-l-text);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.12);
}
.st-seg__btn:disabled {
  opacity: 0.55;
  cursor: default;
}
.st-start {
  margin-top: 22px;
}
.st-spin {
  animation: st-rot 1s linear infinite;
  vertical-align: -3px;
  margin-right: 6px;
}
@keyframes st-rot {
  to { transform: rotate(360deg); }
}

/* 卡二 */
.st-dl-head {
  display: flex;
  align-items: center;
  gap: 7px;
}
.st-dl-title {
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-l-text);
}
.st-dl-info {
  color: var(--uhd-l-text-3);
}
.st-big {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin: 6px 0 14px;
}
.st-big__num {
  font-size: 64px;
  line-height: 1;
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-l-text);
  font-variant-numeric: tabular-nums;
  letter-spacing: -1px;
}
.st-big__unit {
  font-size: var(--uhd-fs-h3);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-l-text-2);
}
.st-stats {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px 20px;
  margin-bottom: 16px;
}
.st-stat {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.st-stat__label {
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
}
.st-stat__value {
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-l-text);
  font-variant-numeric: tabular-nums;
}
.st-chart {
  position: relative;
  border-top: 1px solid var(--uhd-l-divider);
  padding-top: 8px;
}
.st-chart__svg {
  display: block;
  width: 100%;
  height: auto;
}
.st-chart__grid {
  stroke: var(--uhd-l-divider);
  stroke-width: 1;
  stroke-dasharray: 3 4;
}
.st-chart__ylabel,
.st-chart__xlabel {
  font-size: 10px;
  fill: var(--uhd-l-text-3);
}
.st-chart__area {
  fill: rgba(107, 114, 128, 0.12);
}
.st-chart__line {
  fill: none;
  stroke: #9aa0a8;
  stroke-width: 2;
  stroke-linejoin: round;
  stroke-linecap: round;
}
.st-chart__empty {
  position: absolute;
  left: 50%;
  top: 46%;
  transform: translate(-50%, -50%);
  padding: 8px 16px;
  border: 1px solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-pill);
  background: var(--uhd-l-surface);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.08);
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
  white-space: nowrap;
}
.st-error {
  margin: 12px 0 0;
  font-size: var(--uhd-fs-caption);
  line-height: 1.7;
  color: #dc2626;
}
.st-note {
  margin: 12px 0 0;
  font-size: var(--uhd-fs-caption);
  line-height: 1.7;
  color: var(--uhd-l-text-2);
}

/* 卡三 */
.st-net-rows {
  display: flex;
  flex-direction: column;
}
.st-net-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
}
.st-net-row + .st-net-row {
  border-top: 1px solid var(--uhd-l-divider);
}
.st-net-row__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  flex-shrink: 0;
  border: 1px solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-navitem);
  background: var(--uhd-l-input-bg);
  color: var(--uhd-l-text-2);
}
.st-net-row__main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.st-net-row__label {
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
}
.st-net-row__value {
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-l-text);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 卡四 */
.st-hintbar {
  margin: 0 0 16px;
  padding: 12px 14px;
  border: 1px solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-base);
  background: var(--uhd-l-input-bg);
  font-size: var(--uhd-fs-caption);
  line-height: 1.7;
  color: var(--uhd-l-text);
}
.st-rule {
  border: 1px solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-base);
  background: var(--uhd-l-surface);
  padding: 12px 14px;
}
.st-rule + .st-rule {
  margin-top: 12px;
}
.st-rule__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.st-rule__title {
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
}
.st-copy {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-pill);
  background: var(--uhd-l-surface);
  color: var(--uhd-l-text-2);
  font-size: var(--uhd-fs-aux-sm);
  cursor: pointer;
  flex-shrink: 0;
}
.st-rule__body {
  margin: 0;
  padding: 10px 12px;
  border-radius: var(--uhd-radius-navitem);
  background: var(--uhd-l-input-bg);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: var(--uhd-fs-aux);
  line-height: 1.7;
  color: var(--uhd-l-text);
  white-space: pre-wrap;
  word-break: break-all;
}
.st-foot-note {
  margin: 14px 0 0;
  font-size: var(--uhd-fs-aux);
  line-height: 1.7;
  color: var(--uhd-l-text-3);
}
</style>
