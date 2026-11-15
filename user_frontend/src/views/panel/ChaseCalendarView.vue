<script setup lang="ts">
/**
 * 追新日历（/library/calendar —— 2026-11-15 前端接线，后端接口早已实现、
 * 此前全仓无入口）。数据走 GET /api/user/emby/calendar（chaseCalendarApi）：
 * 按入库日（Asia/Shanghai 自然日）分组的新增条目。
 * 月网格（周一起）：格子显示日期、当天条数与前几张封面，多出写「+N」；
 * 点某天在网格下方列出当天已回条目（每日至多 24 条详情，多出只有计数），
 * 点条目进详情页。类型筛选（全部/电影/剧集/单集）的角标数走后端 types
 * 字段（不受当前筛选影响，是区间内各类总量）。月份可往前翻、不能翻到未来。
 */
import { computed, onMounted, ref } from 'vue'
import { ChevronLeft, ChevronRight } from 'lucide-vue-next'
import StreamShell from '@/components/stream/StreamShell.vue'
import StreamPosterCard from '@/components/stream/StreamPosterCard.vue'
import { posterUrl } from '@/api/emby'
import {
  chaseCalendarApi,
  type ChaseCalendarDay,
  type ChaseCalendarItem,
} from '@/api/economy'

defineOptions({ name: 'ChaseCalendarView' })

type TypeKey = 'all' | 'movie' | 'series' | 'episode'
const TYPE_TABS: { key: TypeKey; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'movie', label: '电影' },
  { key: 'series', label: '剧集' },
  { key: 'episode', label: '单集' },
]
const typeFilter = ref<TypeKey>('all')
const typeCounts = ref<Record<string, number>>({})

const now = new Date()
const viewYear = ref(now.getFullYear())
const viewMonth = ref(now.getMonth())
const days = ref<ChaseCalendarDay[]>([])
const loading = ref(false)
const error = ref('')
const selectedDate = ref('')

function fmtDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

const monthLabel = computed(() => `${viewYear.value} 年 ${viewMonth.value + 1} 月`)
const canNext = computed(
  () =>
    viewYear.value < now.getFullYear() ||
    (viewYear.value === now.getFullYear() && viewMonth.value < now.getMonth()),
)
const totalInMonth = computed(() => days.value.reduce((s, d) => s + d.count, 0))

interface Cell {
  date: string
  dayNum: number
  inMonth: boolean
  isToday: boolean
  data?: ChaseCalendarDay
}
const cells = computed<Cell[]>(() => {
  const y = viewYear.value
  const m = viewMonth.value
  const startOffset = (new Date(y, m, 1).getDay() + 6) % 7 /* 周一起 */
  const daysInMonth = new Date(y, m + 1, 0).getDate()
  const daysInPrev = new Date(y, m, 0).getDate()
  const byDate = new Map(days.value.map((d) => [d.date, d]))
  const todayStr = fmtDate(new Date())
  const out: Cell[] = []
  for (let i = 0; i < 42; i++) {
    const n = i - startOffset + 1
    let date: Date
    let inMonth = true
    if (n < 1) {
      date = new Date(y, m - 1, daysInPrev + n)
      inMonth = false
    } else if (n > daysInMonth) {
      date = new Date(y, m + 1, n - daysInMonth)
      inMonth = false
    } else {
      date = new Date(y, m, n)
    }
    const key = fmtDate(date)
    out.push({ date: key, dayNum: date.getDate(), inMonth, isToday: key === todayStr, data: byDate.get(key) })
  }
  /* 末周整周都是下月时收掉，保持 5 周 */
  if (out.slice(35).every((c) => !c.inMonth)) return out.slice(0, 35)
  return out
})

const selectedDay = computed(() => days.value.find((d) => d.date === selectedDate.value) || null)

function typeCount(key: TypeKey): number | null {
  if (key === 'all') return null
  const v = typeCounts.value[key]
  return typeof v === 'number' ? v : null
}

async function load() {
  if (import.meta.env.VITE_FIXTURE === '1') {
    const y = viewYear.value
    const m = viewMonth.value
    const mk = (day: number, items: ChaseCalendarItem[]): ChaseCalendarDay => ({
      date: fmtDate(new Date(y, m, day)),
      count: items.length,
      items,
    })
    const it = (id: string, name: string, type: string): ChaseCalendarItem =>
      ({ Id: id, Name: name, Type: type, LibraryName: '电影库' }) as ChaseCalendarItem
    days.value = [
      mk(3, [it('f1', '夹具电影甲', 'Movie'), it('f2', '夹具剧集甲', 'Series')]),
      mk(9, [it('f3', '夹具剧集乙', 'Series'), it('f4', '夹具电影乙', 'Movie'), it('f5', '夹具单集甲', 'Episode')]),
      mk(14, [it('f6', '夹具电影丙', 'Movie')]),
    ]
    typeCounts.value = { movie: 3, series: 2, episode: 1 }
    selectedDate.value = days.value[days.value.length - 1].date
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  try {
    const res = await chaseCalendarApi.get({
      start: fmtDate(new Date(viewYear.value, viewMonth.value, 1)),
      end: fmtDate(new Date(viewYear.value, viewMonth.value + 1, 0)),
      ...(typeFilter.value !== 'all' ? { item_type: typeFilter.value } : {}),
    })
    days.value = res.days || []
    typeCounts.value = res.types || {}
    const todayStr = fmtDate(new Date())
    if (days.value.some((d) => d.date === todayStr)) selectedDate.value = todayStr
    else if (days.value.length) selectedDate.value = days.value[days.value.length - 1].date
    else selectedDate.value = ''
  } catch (e) {
    days.value = []
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function shiftMonth(delta: number) {
  const d = new Date(viewYear.value, viewMonth.value + delta, 1)
  viewYear.value = d.getFullYear()
  viewMonth.value = d.getMonth()
  void load()
}
function pickType(key: TypeKey) {
  if (typeFilter.value === key) return
  typeFilter.value = key
  void load()
}
function pickDay(cell: Cell) {
  if (cell.data) selectedDate.value = cell.date
}

function metaOf(item: ChaseCalendarItem): string {
  const parts: string[] = []
  const t =
    item.Type === 'Movie' ? '电影' : item.Type === 'Series' ? '剧集' : item.Type === 'Episode' ? '单集' : ''
  if (t) parts.push(t)
  if (item.ProductionYear) parts.push(String(item.ProductionYear))
  if (item.LibraryName) parts.push(item.LibraryName)
  return parts.join(' · ')
}

onMounted(() => {
  void load()
})
</script>

<template>
  <StreamShell>
    <div class="cc-wrap">
      <p class="cc-eyebrow">媒体库</p>
      <div class="cc-head">
        <h1 class="cc-h1">追新日历</h1>
        <div class="cc-nav">
          <button type="button" class="cc-navbtn" aria-label="上个月" @click="shiftMonth(-1)">
            <ChevronLeft :size="17" aria-hidden="true" />
          </button>
          <span class="cc-month">{{ monthLabel }}</span>
          <button
            type="button"
            class="cc-navbtn"
            :disabled="!canNext"
            aria-label="下个月"
            @click="shiftMonth(1)"
          >
            <ChevronRight :size="17" aria-hidden="true" />
          </button>
        </div>
      </div>

      <!-- 类型筛选：角标数是后端 types 的区间总量，不随当前筛选变 -->
      <div class="cc-tabs" role="group" aria-label="条目类型">
        <button
          v-for="t in TYPE_TABS"
          :key="t.key"
          type="button"
          class="cc-tab"
          :class="{ 'cc-tab--on': typeFilter === t.key }"
          :aria-pressed="typeFilter === t.key"
          @click="pickType(t.key)"
        >
          {{ t.label }}<span v-if="typeCount(t.key) !== null" class="cc-tab__count">{{ typeCount(t.key) }}</span>
        </button>
      </div>

      <p v-if="!loading || days.length" class="cc-count">本月新增 {{ totalInMonth }} 条</p>

      <div v-if="loading" class="cc-loading" role="status">正在加载…</div>
      <div v-else-if="error" class="cc-error">
        日历没加载出来：{{ error }}<br />
        <button type="button" class="cc-retry" @click="load">重试</button>
      </div>
      <template v-else>
        <div class="cc-week" aria-hidden="true">
          <span v-for="w in ['一', '二', '三', '四', '五', '六', '日']" :key="w">{{ w }}</span>
        </div>
        <div class="cc-grid">
          <button
            v-for="cell in cells"
            :key="cell.date"
            type="button"
            class="cc-cell"
            :class="{
              'cc-cell--out': !cell.inMonth,
              'cc-cell--today': cell.isToday,
              'cc-cell--sel': selectedDate === cell.date,
              'cc-cell--has': !!cell.data,
            }"
            :disabled="!cell.data"
            :aria-label="cell.data ? `${cell.date}，新增 ${cell.data.count} 条` : cell.date"
            @click="pickDay(cell)"
          >
            <span class="cc-cell__day">{{ cell.dayNum }}</span>
            <span v-if="cell.data" class="cc-cell__covers" aria-hidden="true">
              <template v-for="it in cell.data.items.slice(0, 3)" :key="it.Id">
                <img v-if="posterUrl(it, 96)" :src="posterUrl(it, 96)" alt="" loading="lazy" />
                <span v-else class="cc-cell__ph">{{ (it.Name || 'A').charAt(0) }}</span>
              </template>
            </span>
            <span v-if="cell.data" class="cc-cell__count">
              {{ cell.data.count }} 条<template v-if="cell.data.count > cell.data.items.length"> · +{{ cell.data.count - cell.data.items.length }}</template>
            </span>
          </button>
        </div>

        <!-- 选中日明细：后端当天回的条目详情（至多 24 条，多出只有计数） -->
        <template v-if="selectedDay">
          <h2 class="cc-daytitle">{{ selectedDay.date }} 新增 {{ selectedDay.count }} 条</h2>
          <div class="cc-daygrid">
            <StreamPosterCard
              v-for="item in selectedDay.items"
              :key="item.Id"
              size="grid"
              :title="item.Name"
              :meta="metaOf(item)"
              :rating="item.CommunityRating ?? null"
              :image="posterUrl(item, 456)"
              :to="`/media/${item.Id}`"
            />
          </div>
          <p v-if="selectedDay.count > selectedDay.items.length" class="cc-more">
            当天共 {{ selectedDay.count }} 条，这里只列前 {{ selectedDay.items.length }} 条
          </p>
        </template>
        <p v-else class="cc-empty">这个月还没有新入库的条目</p>
      </template>
    </div>
  </StreamShell>
</template>

<style scoped>
.cc-wrap {
  padding: 26px clamp(16px, 5vw, 96px) 56px;
}
@media (min-width: 1200px) {
  .cc-wrap { padding-left: 28px; padding-right: 28px; }
}
.cc-eyebrow {
  margin: 0;
  font-size: 14px;
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-s-text-2);
}
.cc-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.cc-h1 {
  margin: 8px 0 0;
  font-size: 58px;
  font-weight: var(--uhd-fw-black);
  line-height: 1.05;
  letter-spacing: -0.01em;
  color: var(--uhd-s-text);
}
.cc-nav {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}
.cc-month {
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-s-text);
  min-width: 118px;
  text-align: center;
}
.cc-navbtn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 20px;
  border: 1px solid var(--uhd-s-pill-border);
  background: var(--uhd-s-pill-bg);
  color: var(--uhd-s-text);
  cursor: pointer;
}
.cc-navbtn:disabled {
  opacity: 0.35;
  cursor: default;
}
.cc-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  margin-top: 22px;
}
.cc-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 40px;
  padding: 0 16px;
  border-radius: 20px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--uhd-s-text-2);
  font-size: 15px;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
}
.cc-tab__count {
  font-size: 13px;
  color: var(--uhd-s-text-2);
}
.cc-tab--on {
  background: var(--uhd-s-pill-active-bg);
  color: var(--uhd-s-pill-active-text);
  font-weight: var(--uhd-fw-semibold);
  box-shadow: var(--uhd-shadow-seg);
  border-color: var(--uhd-s-pill-border);
}
.cc-tab--on .cc-tab__count {
  color: inherit;
  font-weight: var(--uhd-fw-semibold);
}
.cc-count {
  margin: 18px 0 0;
  font-size: 14px;
  color: var(--uhd-s-text-2);
}
.cc-loading,
.cc-error,
.cc-empty {
  margin-top: 28px;
  font-size: 14.5px;
  color: var(--uhd-s-text-2);
}
.cc-retry {
  margin-top: 10px;
  height: 36px;
  padding: 0 16px;
  border-radius: 18px;
  border: 1px solid var(--uhd-s-pill-border);
  background: var(--uhd-s-pill-bg);
  color: var(--uhd-s-text);
  font-size: 14px;
  font-family: inherit;
  cursor: pointer;
}
.cc-week {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 8px;
  margin-top: 20px;
}
.cc-week span {
  text-align: center;
  font-size: 12.5px;
  color: var(--uhd-s-text-2);
  padding: 2px 0;
}
.cc-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 8px;
  margin-top: 6px;
}
.cc-cell {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  min-height: 86px;
  padding: 7px 8px;
  border-radius: 12px;
  border: 1px solid var(--uhd-s-border);
  background: transparent;
  color: var(--uhd-s-text);
  font-family: inherit;
  text-align: left;
  cursor: default;
}
.cc-cell--has {
  cursor: pointer;
  background: var(--uhd-s-pill-bg);
}
.cc-cell--out {
  opacity: 0.38;
}
.cc-cell--today {
  border-color: var(--uhd-s-text);
}
.cc-cell--sel {
  border-color: var(--uhd-s-text);
  box-shadow: var(--uhd-shadow-seg);
}
.cc-cell__day {
  font-size: 13.5px;
  font-weight: var(--uhd-fw-semibold);
}
.cc-cell__covers {
  display: flex;
  gap: 3px;
}
.cc-cell__covers img,
.cc-cell__ph {
  width: 22px;
  height: 32px;
  border-radius: 4px;
  background: var(--uhd-s-pill-bg);
}
.cc-cell__covers img {
  object-fit: cover;
}
.cc-cell__ph {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  color: var(--uhd-s-text-2);
}
.cc-cell__count {
  font-size: 11.5px;
  color: var(--uhd-s-text-2);
}
.cc-daytitle {
  margin: 30px 0 0;
  font-size: 19px;
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-s-text);
}
.cc-daygrid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 34px 21px;
  margin-top: 18px;
}
.cc-more {
  margin: 16px 0 0;
  font-size: 13px;
  color: var(--uhd-s-text-2);
}
@media (max-width: 1599px) { .cc-daygrid { grid-template-columns: repeat(6, 1fr); } }
@media (max-width: 1279px) { .cc-daygrid { grid-template-columns: repeat(5, 1fr); } }
@media (max-width: 1023px) { .cc-daygrid { grid-template-columns: repeat(4, 1fr); } }
@media (max-width: 767px) {
  .cc-h1 { font-size: 32px; margin-top: 0; }
  .cc-eyebrow { display: none; }
  .cc-daygrid { grid-template-columns: repeat(3, 1fr); gap: 22px 12px; }
  .cc-grid { gap: 5px; }
  .cc-week { gap: 5px; }
  .cc-cell { min-height: 64px; padding: 5px 6px; border-radius: 10px; }
  .cc-cell__covers img,
  .cc-cell__ph { width: 15px; height: 22px; }
  .cc-cell__count { font-size: 10.5px; }
  .cc-month { min-width: 96px; font-size: 14px; }
}
</style>
