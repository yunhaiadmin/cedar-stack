<script setup lang="ts">
/**
 * LineSpeedtestView.vue —— 线路测速（用户中心 · 帮助组）
 *
 * 线路清单只来自现行配置下发，不写死任何线路名/地址：
 *   - 播放线路：账号卡 GET /api/user/emby/server 的 realms[].base_url
 *     （服配置；没查看权限的服 base_url 置空，自动滤掉）；
 *   - 专属线路：GET /api/user/exclusive-lines（管理端专属线路记录，
 *     只有够格用户才下发，无资格回空列表、整组不渲染）。
 *
 * 测速口径（如实标注，不冒充）：
 *   从当前浏览器向每条线路入口发 3 次小请求计时，取最快一次显示为
 *   近似往返延迟（毫秒）。线路入口是跨域地址，浏览器读不到响应内容
 *   （no-cors 不透明响应），计时包含 DNS/TCP/TLS 建连与等待响应——
 *   是近似往返值，不是 ICMP ping，也不测下载带宽。
 *   单次请求 5 秒超时；超时/连接失败如实显示，不补假数。
 * 测速全程只在用户浏览器与线路入口之间发生，不向后端上报任何数据。
 */
import { computed, onMounted, ref } from 'vue'
import { Gauge, RotateCw } from 'lucide-vue-next'
import UhdAccountLayout from '@/components/uhd/UhdAccountLayout.vue'
import UhdButton from '@/components/uhd/UhdButton.vue'
import UhdEmptyState from '@/components/uhd/UhdEmptyState.vue'
import { embyApi } from '@/api'
import { getMyExclusiveLines } from '@/api/user'

type LineStatus = 'idle' | 'testing' | 'ok' | 'timeout' | 'failed'

interface LineRow {
  key: string
  group: 'realm' | 'exclusive'
  name: string
  url: string
  host: string
  status: LineStatus
  ms: number | null
}

const ATTEMPTS = 3
const TIMEOUT_MS = 5000

const loading = ref(true)
const loadFailed = ref(false)
const lines = ref<LineRow[]>([])
const testingAll = ref(false)

const realmLines = computed(() => lines.value.filter(l => l.group === 'realm'))
const exclusiveLines = computed(() => lines.value.filter(l => l.group === 'exclusive'))
const anyTesting = computed(() => testingAll.value || lines.value.some(l => l.status === 'testing'))

function hostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

function makeRow(group: LineRow['group'], name: string, url: string): LineRow {
  return { key: `${group}:${url}`, group, name: name || hostOf(url), url, host: hostOf(url), status: 'idle', ms: null }
}

async function loadLines() {
  loading.value = true
  loadFailed.value = false
  const rows: LineRow[] = []
  const seen = new Set<string>()
  let cardOk = false
  try {
    const card = await embyApi.getAccountCard()
    cardOk = true
    for (const r of card.realms ?? []) {
      const url = (r.base_url ?? '').trim()
      if (!url || seen.has(url)) continue
      seen.add(url)
      rows.push(makeRow('realm', r.name, url))
    }
    // 顶层 base_url 是当前服的地址；realms 没带时补上（名称用服名，不发明）
    const top = (card.base_url ?? '').trim()
    if (top && !seen.has(top)) {
      seen.add(top)
      rows.push(makeRow('realm', card.realm_name || card.server_name || '主线路', top))
    }
  } catch { /* 账号卡失败：专属线路还能单独成页；两边都空才算加载失败 */ }
  const mine = await getMyExclusiveLines()
  for (const l of mine) {
    const url = (l.entry_url ?? '').trim()
    if (!url || seen.has(url)) continue
    seen.add(url)
    rows.push(makeRow('exclusive', l.name, url))
  }
  lines.value = rows
  loadFailed.value = !cardOk && rows.length === 0
  loading.value = false
}

type ProbeResult = { ms: number } | { err: 'timeout' | 'failed' }

async function probeOnce(url: string): Promise<ProbeResult> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS)
  const sep = url.includes('?') ? '&' : '?'
  const target = `${url}${sep}__speedtest=${Date.now()}`
  const t0 = performance.now()
  try {
    await fetch(target, { mode: 'no-cors', cache: 'no-store', credentials: 'omit', signal: ctrl.signal })
    return { ms: Math.max(1, Math.round(performance.now() - t0)) }
  } catch (e) {
    return { err: (e as DOMException)?.name === 'AbortError' ? 'timeout' : 'failed' }
  } finally {
    clearTimeout(timer)
  }
}

async function testLine(row: LineRow) {
  if (row.status === 'testing') return
  row.status = 'testing'
  row.ms = null
  const samples: number[] = []
  let lastErr: 'timeout' | 'failed' = 'failed'
  for (let i = 0; i < ATTEMPTS; i++) {
    const r = await probeOnce(row.url)
    if ('ms' in r) samples.push(r.ms)
    else lastErr = r.err
  }
  if (samples.length) {
    row.ms = Math.min(...samples)
    row.status = 'ok'
  } else {
    row.status = lastErr
  }
}

async function testAll() {
  if (anyTesting.value) return
  testingAll.value = true
  try {
    for (const row of lines.value) await testLine(row)
  } finally {
    testingAll.value = false
  }
}

function statusText(row: LineRow): string {
  switch (row.status) {
    case 'testing': return '测速中…'
    case 'ok': return `${row.ms} ms`
    case 'timeout': return '超时'
    case 'failed': return '连接失败'
    default: return '未测速'
  }
}

function dotClass(row: LineRow): string {
  if (row.status === 'ok' && row.ms != null) {
    if (row.ms < 150) return 'is-good'
    if (row.ms < 400) return 'is-mid'
    return 'is-slow'
  }
  if (row.status === 'timeout' || row.status === 'failed') return 'is-bad'
  return ''
}

onMounted(loadLines)
</script>

<template>
  <UhdAccountLayout title="线路测速" eyebrow="帮助">
    <div class="st-page">
      <p class="st-lede">测一测你现在连每条线路的速度，挑延迟低的填进播放器。</p>

      <!-- 加载中 -->
      <p v-if="loading" class="st-hint">正在读取线路…</p>

      <!-- 加载失败 -->
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

      <!-- 无可用线路 -->
      <UhdEmptyState
        v-else-if="!lines.length"
        variant="icon"
        title="暂无可用线路"
        desc="你的账号下暂无服务器地址与专属线路，绑定或开通后这里会列出可测的线路。"
      >
        <template #icon><Gauge :size="24" /></template>
      </UhdEmptyState>

      <template v-else>
        <div class="st-actions">
          <UhdButton variant="primary" :disabled="anyTesting" @click="testAll">
            {{ anyTesting ? '测速中…' : '开始测速' }}
          </UhdButton>
          <span class="st-actions__note">逐条测试，也可以单条重测</span>
        </div>

        <section v-if="realmLines.length" class="st-group">
          <h2 class="st-gtitle">播放线路</h2>
          <div class="st-rows">
            <div v-for="row in realmLines" :key="row.key" class="st-row">
              <span class="st-row__main">
                <span class="st-row__name">{{ row.name }}</span>
                <span class="st-row__host">{{ row.host }}</span>
              </span>
              <span class="st-row__result">
                <span v-if="dotClass(row)" class="st-dot" :class="dotClass(row)" aria-hidden="true"></span>
                <span class="st-row__ms" :class="{ 'is-dim': row.status === 'idle' }">{{ statusText(row) }}</span>
                <button
                  type="button"
                  class="st-retest"
                  :disabled="row.status === 'testing' || testingAll"
                  @click="testLine(row)"
                >
                  <RotateCw :size="13" />重测
                </button>
              </span>
            </div>
          </div>
        </section>

        <section v-if="exclusiveLines.length" class="st-group">
          <h2 class="st-gtitle">专属线路</h2>
          <div class="st-rows">
            <div v-for="row in exclusiveLines" :key="row.key" class="st-row">
              <span class="st-row__main">
                <span class="st-row__name">{{ row.name }}</span>
                <span class="st-row__host">{{ row.host }}</span>
              </span>
              <span class="st-row__result">
                <span v-if="dotClass(row)" class="st-dot" :class="dotClass(row)" aria-hidden="true"></span>
                <span class="st-row__ms" :class="{ 'is-dim': row.status === 'idle' }">{{ statusText(row) }}</span>
                <button
                  type="button"
                  class="st-retest"
                  :disabled="row.status === 'testing' || testingAll"
                  @click="testLine(row)"
                >
                  <RotateCw :size="13" />重测
                </button>
              </span>
            </div>
          </div>
        </section>

        <section class="st-group">
          <h2 class="st-gtitle">说明</h2>
          <div class="st-notes">
            <p>测的是你当前设备到每条线路入口的往返延迟：页面会向每条线路入口发 3 次小请求，取最快一次。</p>
            <p>线路入口是跨域地址，浏览器读不到响应内容，计时包含建连与等响应，属于近似值——不是 ping 值，这里也不测下载速度。</p>
            <p>单次请求超过 5 秒算超时；连不上会如实显示连接失败。</p>
          </div>
        </section>
      </template>
    </div>
  </UhdAccountLayout>
</template>

<style scoped>
/* 用户中心框架（UhdAccountLayout）是浅色面（--uhd-l-bg 写死、无深色变体），
   本页与同框架的账号设置/许愿池同口径：浅色令牌打底。 */
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
.st-actions {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 28px;
  flex-wrap: wrap;
}
.st-actions__note {
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-3);
}
.st-group {
  margin-bottom: 30px;
}
.st-gtitle {
  margin: 0 0 6px;
  font-size: var(--uhd-fs-h3, 17px);
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-l-text);
}
.st-rows {
  border-top: 1px solid var(--uhd-l-divider);
}
.st-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 62px;
  padding: 10px 2px;
  border-bottom: 1px solid var(--uhd-l-divider);
}
.st-row__main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.st-row__name {
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-l-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.st-row__host {
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.st-row__result {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.st-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #22c55e;
}
.st-dot.is-mid { background: #f59e0b; }
.st-dot.is-slow { background: #f97316; }
.st-dot.is-bad { background: #ef4444; }
.st-row__ms {
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-l-text);
  font-variant-numeric: tabular-nums;
  min-width: 64px;
  text-align: right;
}
.st-row__ms.is-dim {
  color: var(--uhd-l-text-3);
  font-weight: var(--uhd-fw-regular, 400);
}
.st-retest {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 32px;
  min-height: 32px;
  padding: 0 10px;
  border-radius: var(--uhd-radius-pill);
  border: 1px solid var(--uhd-l-border);
  background: var(--uhd-l-surface);
  color: var(--uhd-l-text-2);
  font-size: var(--uhd-fs-caption);
  cursor: pointer;
}
.st-retest:disabled {
  opacity: 0.45;
  cursor: default;
}
.st-notes {
  border-top: 1px solid var(--uhd-l-divider);
  padding-top: 12px;
}
.st-notes p {
  margin: 0 0 8px;
  font-size: var(--uhd-fs-caption);
  line-height: 1.7;
  color: var(--uhd-l-text-2);
}
</style>
