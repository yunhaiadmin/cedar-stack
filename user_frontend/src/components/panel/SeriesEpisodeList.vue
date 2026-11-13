<template>
  <div class="sel-wrap">
    <!-- 缺集小结：口径与许愿池缺集条一致（同一只读口 + 同一落单口），
         这里只是把入口搬到剧跟前，不新开第二套求补记录 -->
    <div v-if="missingInfo?.has_missing" class="miss-bar">
      <span class="miss-text">🧩 这部剧还缺 {{ missingInfo.missing_count }} 集：{{ missingInfo.missing_text }}</span>
      <span v-if="fillState === 'mine'" class="miss-done">已求补，补齐后会通知你</span>
      <span v-else-if="fillState === 'voted'" class="miss-done">已附议求补，补齐后会通知你</span>
      <span v-else-if="fillState === 'others'" class="miss-done">已有 {{ (missingInfo.request?.vote_count || 0) + 1 }} 人求补</span>
    </div>

    <div v-if="loading" class="v4-skeleton" aria-hidden="true" style="height: 180px"></div>
    <div v-else-if="error" class="v4-err" role="alert">
      季集没加载出来：{{ error }}<br />
      <button type="button" class="v4-retry" :disabled="loading" @click="loadAll">重试</button>
    </div>
    <div v-else-if="sections.length === 0" class="v4-empty">这部剧还没有入库的集</div>

    <section v-for="sec in sections" :key="sec.key" class="season-sec">
      <div class="season-head">
        <h3 class="season-title">{{ sec.title }}</h3>
        <span class="season-count">
          已有 {{ sec.existingCount }} 集<template v-if="sec.missingNums.length"> · 缺 {{ sec.missingNums.length }} 集</template>
        </span>
        <!-- 整季都缺：季标题处给汇总入口（落单仍是整剧一单，与逐集按钮同口） -->
        <template v-if="sec.wholeMissing && missingInfo?.has_missing">
          <button
            v-if="fillState === 'open' || fillState === 'others'"
            type="button"
            class="fill-btn"
            :disabled="fillBusy"
            @click="doFill"
          >{{ fillBusy ? '求补中…' : '求补本季缺集' }}</button>
          <span v-else-if="fillState === 'mine'" class="fill-done">已求补</span>
          <span v-else-if="fillState === 'voted'" class="fill-done">已附议</span>
        </template>
      </div>

      <!-- 集数条（录屏形态）：高 50/圆角 25 浅灰底、数字圆 36（白圆）、
           当前集黑底白字（深色反之）；条右端内嵌「正序/倒序」分段切换；
           滚动时吸顶（sticky，顶栏之下） -->
      <div v-if="sec.rows.length" class="ep-strip" role="group" :aria-label="`${sec.title} 集数`">
        <div class="ep-strip__nums">
          <button
            v-for="row in sec.rows"
            :key="`strip-${row.key}`"
            type="button"
            class="ep-strip__num"
            :class="{ 'ep-strip__num--on': activeNum(sec) === row.num }"
            @click="jumpToEpisode(sec, row.num)"
          >{{ row.num !== null ? String(row.num).padStart(2, '0') : '·' }}</button>
        </div>
        <div class="ep-seg" role="group" aria-label="集数排序">
          <button type="button" :class="{ on: !orderDesc }" @click="orderDesc = false">正序</button>
          <button type="button" :class="{ on: orderDesc }" @click="orderDesc = true">倒序</button>
        </div>
      </div>

      <ol class="ep-list">
        <li
          v-for="row in orderedRows(sec)"
          :id="`ep-${sec.key}-${row.num}`"
          :key="row.key"
          class="ep-row"
          :class="{ missing: row.kind === 'missing' }"
        >
          <template v-if="row.kind === 'have'">
            <!-- 手机列表形态（录屏）：大号两位数序号圆（桌面隐藏，桌面走缩略图卡） -->
            <span class="ep-bignum" aria-hidden="true">{{ row.num !== null ? String(row.num).padStart(2, '0') : '·' }}</span>
            <span class="ep-thumb">
              <img v-if="rowThumb(row)" :src="rowThumb(row)" alt="" loading="lazy" />
              <Clapperboard v-else :size="22" :stroke-width="1.5" class="ep-thumb__fallback" aria-hidden="true" />
              <!-- 全片库唯一角标：集数缩略图右上「N版本」黑半透明胶囊 52×22/圆角 11 -->
              <span v-if="rowVersions(row) > 1" class="ep-ver-badge">{{ rowVersions(row) }}版本</span>
            </span>
            <span class="ep-body">
              <span class="ep-line1">
                <span class="ep-num">{{ row.num !== null ? `${row.num}.` : '' }}</span>
                <span class="ep-name">{{ row.name }}</span>
                <span v-if="row.duration" class="ep-dur">{{ row.duration }}</span>
                <span v-if="row.played" class="ep-played">已看</span>
              </span>
              <span v-if="rowDate(row)" class="ep-date">{{ rowDate(row) }}</span>
              <span v-if="row.item?.Overview" class="ep-overview">{{ row.item.Overview }}</span>
            </span>
            <!-- 圆形眼睛钮（录屏形态）：已看标记开关，走现行 setPlayed 口径 -->
            <button
              type="button"
              class="ep-eye"
              :class="{ 'ep-eye--on': row.played }"
              :aria-label="row.played ? '标为未看' : '标为已看'"
              :title="row.played ? '标为未看' : '标为已看'"
              @click="togglePlayed(row)"
            >
              <Eye v-if="row.played" :size="17" />
              <EyeOff v-else :size="17" />
            </button>
          </template>
          <template v-else>
            <span class="ep-thumb ep-thumb--missing">
              <span class="miss-badge">缺</span>
            </span>
            <span class="ep-body">
              <span class="ep-line1">
                <span class="ep-num">{{ row.num !== null ? `${row.num}.` : '' }}</span>
                <span class="ep-missing-label">这一集还没入库</span>
              </span>
              <span v-if="missingInfo?.has_missing" class="ep-missing-action">
                <button
                  v-if="fillState === 'open' || fillState === 'others'"
                  type="button"
                  class="fill-btn"
                  :disabled="fillBusy"
                  @click="doFill"
                >{{ fillBusy ? '求补中…' : '求补这集' }}</button>
                <span v-else-if="fillState === 'mine'" class="fill-done">已求补</span>
                <span v-else-if="fillState === 'voted'" class="fill-done">已附议</span>
              </span>
            </span>
          </template>
        </li>
      </ol>
    </section>
  </div>
</template>

<script setup lang="ts">
/**
 * 剧集季/集列表（详情页专用）：在真实集列表里把缺集标出来并就地求补。
 *
 * 数据来源（全部复用现成口径，不新造）：
 * - 季/集：Emby 协议口 embyApi.getSeasons / getEpisodes（api/emby.ts）；
 * - 缺集：门户只读口 GET /api/user/media-seek/missing（与许愿池候选卡
 *   同一个 _missing_info_payload：台账 emby_missing_episode_records 的
 *   open 记录，missing_detail = {季号: [缺的集号]}）；
 * - 求补：POST /api/user/media-seek/missing-request —— 与许愿池「求补
 *   这些集」同一落单口：一剧一单、快照整剧缺集、他人已求时转附议。
 *   所以本页每个「求补这集 / 求补本季缺集」按钮提交的都是那一单，
 *   提交后全页按钮一起变「已求补 / 已附议」，状态一律回读后端为准。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { Clapperboard, Eye, EyeOff } from 'lucide-vue-next'
import {
  embyApi as embyProtocol,
  formatDuration,
  imageUrl,
  ticksToSeconds,
  type EmbyItem,
} from '@/api/emby'
import { mediaSeekApi, type MissingInfo } from '@/api'
import { humanizeError } from '@/composables/usePanelApi'
import { useToast } from '@/composables/useToast'

const props = defineProps<{
  /** 剧的 Emby guid（与门户收藏口的 id 同一空间） */
  seriesId: string
  /** 剧的 TMDB id（条目 ProviderIds.Tmdb）；没有就不查缺集、只列已有集 */
  tmdbId: string | null
}>()

const toast = useToast()

interface EpRow {
  key: string
  kind: 'have' | 'missing'
  num: number | null
  name?: string
  duration?: string
  played?: boolean
  /** 原始集条目（批 3 片库面卡片用：缩略图/日期/简介/版本数） */
  item?: EmbyItem
}

interface SeasonSection {
  key: string
  title: string
  seasonNumber: number | null
  existingCount: number
  missingNums: number[]
  wholeMissing: boolean
  rows: EpRow[]
}

const loading = ref(false)
const error = ref('')
const seasons = ref<EmbyItem[]>([])
const episodes = ref<EmbyItem[]>([])
const missingInfo = ref<MissingInfo | null>(null)
const fillBusy = ref(false)
let loadSeq = 0
let missingSeq = 0

function errText(err: unknown): string {
  const msg = (err as { userMessage?: string })?.userMessage
  if (msg) return msg
  if (err instanceof Error) return err.message
  return '加载失败'
}

/** 后端错误 detail 原文（求补 400/409/429 的大白话都在 detail 里） */
function errDetail(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail : ''
}

/** 求补状态机：与许愿池缺集条同一套（无单 / 本人单 / 已附议 / 他人单） */
const fillState = computed<'none' | 'open' | 'mine' | 'voted' | 'others'>(() => {
  const mi = missingInfo.value
  if (!mi?.has_missing) return 'none'
  if (!mi.request) return 'open'
  if (mi.request.mine) return 'mine'
  if (mi.request.voted) return 'voted'
  return 'others'
})

const sections = computed<SeasonSection[]>(() =>
  buildSections(seasons.value, episodes.value, missingInfo.value),
)

/* ---------- 批 3 片库面：正序/倒序 + 集数条（纯展示，不动数据与求补口径） ---------- */
const orderDesc = ref(false)
const activeNums = ref<Record<string, number | null>>({})

function orderedRows(sec: SeasonSection): EpRow[] {
  return orderDesc.value ? [...sec.rows].reverse() : sec.rows
}
/** 当前集：用户点选优先；默认第一集未看完的已有集，再退到第一行 */
function activeNum(sec: SeasonSection): number | null {
  const picked = activeNums.value[sec.key]
  if (picked !== undefined) return picked
  const firstUnplayed = sec.rows.find((r) => r.kind === 'have' && !r.played)
  return (firstUnplayed ?? sec.rows[0])?.num ?? null
}
function jumpToEpisode(sec: SeasonSection, num: number | null) {
  activeNums.value = { ...activeNums.value, [sec.key]: num }
  if (num === null) return
  document
    .getElementById(`ep-${sec.key}-${num}`)
    ?.scrollIntoView({ block: 'center', behavior: 'smooth' })
}
function rowThumb(row: EpRow): string {
  const it = row.item
  if (!it) return ''
  if (it.ImageTags?.Primary) return imageUrl(it.Id, 'Primary', 416)
  if (it.BackdropImageTags?.length) return imageUrl(it.Id, 'Thumb', 416)
  return ''
}
function rowVersions(row: EpRow): number {
  return row.item?.MediaSources?.length || 0
}
function rowDate(row: EpRow): string {
  return row.item?.PremiereDate ? row.item.PremiereDate.slice(0, 10) : ''
}
/** 已看标记开关（现行 setPlayed 口；失败静默回滚状态由服务端下次拉取校正） */
const playedBusy = ref(false)
async function togglePlayed(row: EpRow) {
  if (!row.item || playedBusy.value) return
  playedBusy.value = true
  const next = !row.played
  try {
    await embyProtocol.setPlayed(row.item.Id, next)
    row.played = next
    if (row.item.UserData) row.item.UserData = { ...row.item.UserData, Played: next }
  } catch {
    /* 标记失败不打断浏览；状态保持原样 */
  } finally {
    playedBusy.value = false
  }
}

function seasonTitle(sn: number | null, fallbackName?: string): string {
  if (fallbackName && fallbackName.trim()) return fallbackName
  if (sn === 0) return '特别篇'
  if (sn !== null) return `第 ${sn} 季`
  return '季'
}

function buildSections(
  seasonItems: EmbyItem[],
  episodeItems: EmbyItem[],
  missing: MissingInfo | null,
): SeasonSection[] {
  // 缺集表：{季号: [集号]} → Map<季号, 集号[]>（去重升序）
  const missingMap = new Map<number, number[]>()
  if (missing?.has_missing && missing.missing_detail) {
    for (const [k, v] of Object.entries(missing.missing_detail)) {
      const sn = Number(k)
      if (!Number.isFinite(sn) || !Array.isArray(v)) continue
      missingMap.set(sn, [...new Set(v)].sort((a, b) => a - b))
    }
  }

  // 已有集先按 SeasonId 归季，归不上的按 ParentIndexNumber 兜底
  const bySeasonId = new Map<string, EmbyItem[]>()
  const orphans: EmbyItem[] = []
  for (const ep of episodeItems) {
    if (ep.SeasonId) {
      const arr = bySeasonId.get(ep.SeasonId) || []
      arr.push(ep)
      bySeasonId.set(ep.SeasonId, arr)
    } else {
      orphans.push(ep)
    }
  }

  const coveredNums = new Set<number>()
  const out: SeasonSection[] = []

  const makeSection = (
    key: string,
    title: string,
    sn: number | null,
    eps: EmbyItem[],
  ): SeasonSection => {
    const sorted = [...eps].sort((a, b) => {
      const an = a.IndexNumber ?? Number.MAX_SAFE_INTEGER
      const bn = b.IndexNumber ?? Number.MAX_SAFE_INTEGER
      return an - bn
    })
    const haveNums = new Set(
      sorted.map((e) => e.IndexNumber).filter((n): n is number => n !== null && n !== undefined),
    )
    const missingNums =
      sn !== null ? (missingMap.get(sn) || []).filter((n) => !haveNums.has(n)) : []
    const rows: EpRow[] = []
    for (const ep of sorted) {
      rows.push({
        key: `have-${ep.Id}`,
        kind: 'have',
        num: ep.IndexNumber ?? null,
        name: ep.Name || '',
        duration: ep.RunTimeTicks ? formatDuration(ticksToSeconds(ep.RunTimeTicks)) : '',
        played: !!ep.UserData?.Played,
        item: ep,
      })
    }
    for (const n of missingNums) {
      rows.push({ key: `miss-${sn}-${n}`, kind: 'missing', num: n })
    }
    rows.sort((a, b) => {
      const an = a.num ?? Number.MAX_SAFE_INTEGER
      const bn = b.num ?? Number.MAX_SAFE_INTEGER
      return an - bn
    })
    return {
      key,
      title,
      seasonNumber: sn,
      existingCount: sorted.length,
      missingNums,
      wholeMissing: sorted.length === 0 && missingNums.length > 0,
      rows,
    }
  }

  for (const s of seasonItems) {
    const sn = s.IndexNumber ?? null
    if (sn !== null) coveredNums.add(sn)
    const eps = [...(bySeasonId.get(s.Id) || [])]
    // 无 SeasonId 的集按季号补进对应季
    for (let i = orphans.length - 1; i >= 0; i--) {
      if (sn !== null && orphans[i].ParentIndexNumber === sn) {
        eps.push(orphans[i])
        orphans.splice(i, 1)
      }
    }
    out.push(makeSection(`season-${s.Id}`, seasonTitle(sn, s.Name), sn, eps))
  }

  // 没有季条目承接的已有集（季行缺失时按季号自成一节，别把集吞了）
  const orphanByNum = new Map<number | null, EmbyItem[]>()
  for (const ep of orphans) {
    const sn = ep.ParentIndexNumber ?? null
    const arr = orphanByNum.get(sn) || []
    arr.push(ep)
    orphanByNum.set(sn, arr)
  }
  for (const [sn, eps] of orphanByNum) {
    if (sn !== null) coveredNums.add(sn)
    out.push(makeSection(`orphan-${sn}`, seasonTitle(sn), sn, eps))
  }

  // 整季都缺：台账里有这一季、库里连季行都没有 → 合成一节，集全是缺集行
  for (const [sn] of missingMap) {
    if (coveredNums.has(sn)) continue
    out.push(makeSection(`miss-season-${sn}`, seasonTitle(sn), sn, []))
  }

  // 排序：正季升序在前，特别篇（0）与未知季号殿后
  const rank = (sn: number | null): number =>
    sn === null ? 2 : sn === 0 ? 1 : 0
  out.sort((a, b) => {
    const r = rank(a.seasonNumber) - rank(b.seasonNumber)
    if (r !== 0) return r
    return (a.seasonNumber ?? 0) - (b.seasonNumber ?? 0)
  })
  return out
}

async function loadMissing(): Promise<void> {
  const tmdbId = props.tmdbId
  if (!tmdbId) {
    missingInfo.value = null
    return
  }
  const seq = ++missingSeq
  try {
    const r = await mediaSeekApi.missing(tmdbId)
    if (seq !== missingSeq) return
    missingInfo.value = r
  } catch {
    // 缺集是附加信息：查不到就不标缺集，绝不挡季集主列表
    if (seq !== missingSeq) return
    missingInfo.value = null
  }
}

async function loadAll(): Promise<void> {
  const seq = ++loadSeq
  loading.value = true
  error.value = ''
  try {
    const [ss, eps] = await Promise.all([
      embyProtocol.getSeasons(props.seriesId),
      embyProtocol.getEpisodes(props.seriesId),
    ])
    if (seq !== loadSeq) return
    seasons.value = ss
    episodes.value = eps
  } catch (err) {
    if (seq !== loadSeq) return
    error.value = errText(err)
    seasons.value = []
    episodes.value = []
  } finally {
    if (seq === loadSeq) loading.value = false
  }
  void loadMissing()
}

/** 逐集 / 整季按钮同口：落整剧一单（或转附议），回读最新状态为准 */
async function doFill(): Promise<void> {
  const tmdbId = props.tmdbId
  if (!tmdbId || fillBusy.value) return
  fillBusy.value = true
  try {
    const r = await mediaSeekApi.requestMissing(tmdbId)
    toast.success(r.message || '求补已提交')
    await loadMissing()
  } catch (e) {
    toast.error(errDetail(e) || humanizeError(e))
  } finally {
    fillBusy.value = false
  }
}

onMounted(() => {
  void loadAll()
})

watch(
  () => props.seriesId,
  () => {
    missingInfo.value = null
    void loadAll()
  },
)
watch(
  () => props.tmdbId,
  () => {
    void loadMissing()
  },
)
</script>

<style scoped>
/* 批 3 片库面皮：颜色/底走 --uhd-s-* 令牌（详情页在 StreamShell 内渲染）；
   在非片库面语境（如旧引用）下令牌有字面兜底，不破版。 */
.sel-wrap {
  margin-top: 12px;
}
.miss-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  padding: 10px 12px;
  border: 1px dashed var(--uhd-s-border, #e5e5e5);
  border-radius: 12px;
  background: var(--uhd-s-card-soft, #f5f5f5);
  margin-bottom: 14px;
}
.miss-text {
  font-size: 13px;
  line-height: 1.5;
  color: var(--uhd-s-text, #27251e);
}
.miss-done {
  font-size: 12.5px;
  color: var(--uhd-s-text, #016a71);
  font-weight: 600;
}
.season-sec {
  margin-top: 26px;
}
.season-sec:first-of-type {
  margin-top: 4px;
}
.season-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  margin-bottom: 10px;
}
.season-title {
  font-size: 17px;
  font-weight: 700;
  color: var(--uhd-s-text, #27251e);
  margin: 0;
}
.season-count {
  font-size: 13px;
  color: var(--uhd-s-text-2, #6f6a5e);
}
.season-head .fill-btn,
.season-head .fill-done {
  margin-left: auto;
}
.season-head .fill-btn {
  margin-left: 0;
}
/* 集数条：高 50/圆角 25/浅灰底；数字圆 36；滚动吸顶（顶栏之下） */
.ep-strip {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 50px;
  padding: 0 8px 0 10px;
  border-radius: 25px;
  background: var(--uhd-s-strip-bg, #f0f0f0);
  margin-bottom: 16px;
  position: sticky;
  top: 76px;
  z-index: 6;
}
.ep-strip__nums {
  display: flex;
  align-items: center;
  gap: 5px;
  flex: 1 1 auto;
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
}
.ep-strip__nums::-webkit-scrollbar {
  display: none;
}
/* 条内「正序/倒序」分段切换（录屏形态）：激活 = 白胶囊 + 阴影 */
.ep-seg {
  flex: none;
  display: flex;
  gap: 2px;
  margin-left: 4px;
}
.ep-seg button {
  height: 36px;
  padding: 0 15px;
  border: none;
  border-radius: 18px;
  background: transparent;
  color: var(--uhd-s-text-2, #6f6a5e);
  font-size: 13.5px;
  font-family: inherit;
  cursor: pointer;
}
.ep-seg button.on {
  background: var(--uhd-s-pill-active-bg, #ffffff);
  color: var(--uhd-s-text, #111111);
  font-weight: 600;
  box-shadow: var(--uhd-shadow-seg, 0 1px 4px rgba(0, 0, 0, 0.12));
}
.ep-strip__num {
  flex: 0 0 36px;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: none;
  background: var(--uhd-s-strip-num-bg, transparent);
  color: var(--uhd-s-strip-num-text, #111111);
  font-size: 14px;
  font-family: inherit;
  cursor: pointer;
}
.ep-strip__num--on {
  background: var(--uhd-s-strip-current-bg, #111111);
  color: var(--uhd-s-strip-current-text, #ffffff);
  font-weight: 700;
}
/* 集数卡 2 列：行高约 145、缩略图 208×117/圆角 12 */
.ep-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px 28px;
}
.ep-row {
  display: flex;
  gap: 16px;
  padding: 14px 4px;
  min-width: 0;
  border-bottom: 1px solid var(--uhd-s-divider, #eeeeee);
  scroll-margin-top: 140px;
  animation: ep-in 0.4s ease both;
}
@keyframes ep-in {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: none; }
}
@media (prefers-reduced-motion: reduce) {
  .ep-row { animation: none; }
}
/* 大号序号圆与眼睛钮：录屏的手机列表形态专用，桌面默认隐藏 */
.ep-bignum {
  display: none;
}
.ep-eye {
  display: none;
  flex: none;
  width: 38px;
  height: 38px;
  border-radius: 50%;
  border: 1px solid var(--uhd-s-border, #e5e5e5);
  background: transparent;
  color: var(--uhd-s-text-2, #6f6a5e);
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-family: inherit;
  align-self: center;
}
.ep-eye--on {
  color: var(--uhd-s-text, #111111);
  border-color: var(--uhd-s-text, #111111);
}
.ep-row.missing {
  opacity: 0.85;
}
.ep-thumb {
  position: relative;
  flex: 0 0 208px;
  width: 208px;
  aspect-ratio: 16 / 9;
  border-radius: 12px;
  overflow: hidden;
  background: var(--uhd-s-card-soft, #f5f5f5);
  display: flex;
  align-items: center;
  justify-content: center;
}
.ep-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.ep-thumb__fallback {
  color: var(--uhd-s-text-2, #a8a29a);
}
.ep-thumb--missing {
  border: 1px dashed var(--uhd-s-border, #e5e5e5);
  background: transparent;
}
/* 「N版本」角标：52×22/圆角 11/黑半透明/白字 12px（两模式无差异） */
.ep-ver-badge {
  position: absolute;
  top: 8px;
  right: 8px;
  min-width: 52px;
  height: 22px;
  padding: 0 8px;
  border-radius: 11px;
  background: var(--uhd-s-badge-bg, rgba(0, 0, 0, 0.55));
  color: #ffffff;
  font-size: 12px;
  line-height: 22px;
  text-align: center;
}
.ep-body {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-top: 2px;
}
.ep-line1 {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.ep-num {
  flex: 0 0 auto;
  font-size: 15px;
  font-weight: 600;
  color: var(--uhd-s-text-2, #6f6a5e);
}
.ep-name {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--uhd-s-text, #27251e);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ep-dur {
  flex: 0 0 auto;
  font-size: 14px;
  color: var(--uhd-s-text-2, #6f6a5e);
}
.ep-played {
  flex: 0 0 auto;
  font-size: 11px;
  color: var(--uhd-s-text-2, #6f6a5e);
  border: 1px solid currentColor;
  border-radius: 999px;
  padding: 1px 7px;
}
.ep-date {
  font-size: 14px;
  color: var(--uhd-s-text-2, #6f6a5e);
}
.ep-overview {
  font-size: 14px;
  line-height: 1.55;
  color: var(--uhd-s-text-2, #6f6a5e);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.ep-missing-label {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 15px;
  color: var(--uhd-s-text-2, #6f6a5e);
}
.ep-missing-action {
  margin-top: 6px;
}
.miss-badge {
  flex: 0 0 auto;
  font-size: 12px;
  font-weight: 700;
  color: #fff;
  background: var(--uhd-s-text-2, #a8a29a);
  border-radius: 6px;
  padding: 2px 8px;
}
.fill-btn {
  flex: 0 0 auto;
  font-size: 13px;
  font-weight: 600;
  color: var(--uhd-s-text, #111111);
  background: var(--uhd-s-pill-active-bg, #ffffff);
  border: 1px solid var(--uhd-s-border, #e5e5e5);
  border-radius: 999px;
  padding: 6px 14px;
  cursor: pointer;
  font-family: inherit;
}
.fill-btn:disabled {
  opacity: 0.55;
  cursor: wait;
}
.fill-done {
  flex: 0 0 auto;
  font-size: 13px;
  font-weight: 600;
  color: var(--uhd-s-text, #016a71);
}
@media (max-width: 900px) {
  .ep-list {
    grid-template-columns: 1fr;
  }
}
/* 手机列表形态（录屏）：无缩略图、大序号圆 + 标题/时长 + 日期/简介一行 + 眼睛钮 */
@media (max-width: 767px) {
  /* 吸顶时叠在详情页播放行（top 8 + 高约 68）之下 */
  .ep-strip {
    top: 80px;
  }
  .ep-thumb {
    display: none;
  }
  .ep-bignum {
    display: inline-flex;
    flex: none;
    width: 46px;
    height: 46px;
    border-radius: 50%;
    background: var(--uhd-s-card-soft, #f5f5f5);
    color: var(--uhd-s-text-2, #6f6a5e);
    font-size: 17px;
    font-weight: 700;
    align-items: center;
    justify-content: center;
    align-self: flex-start;
  }
  .ep-eye {
    display: inline-flex;
  }
  .ep-row .ep-num {
    display: none;
  }
  .ep-overview {
    -webkit-line-clamp: 1;
  }
  .ep-row {
    gap: 12px;
    padding: 13px 2px;
  }
}
@media (max-width: 560px) {
  .ep-thumb {
    flex-basis: 148px;
    width: 148px;
  }
  .ep-name {
    font-size: 15px;
  }
}
</style>
