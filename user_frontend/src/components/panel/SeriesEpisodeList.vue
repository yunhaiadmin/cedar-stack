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
      <ol class="ep-list">
        <li v-for="row in sec.rows" :key="row.key" class="ep-row" :class="{ missing: row.kind === 'missing' }">
          <span class="ep-num">{{ row.num !== null ? `第${row.num}集` : '集' }}</span>
          <template v-if="row.kind === 'have'">
            <span class="ep-name">{{ row.name }}</span>
            <span v-if="row.duration" class="ep-dur">{{ row.duration }}</span>
            <span v-if="row.played" class="ep-played">已看</span>
          </template>
          <template v-else>
            <span class="ep-missing-label"><span class="miss-badge">缺</span>这一集还没入库</span>
            <template v-if="missingInfo?.has_missing">
              <button
                v-if="fillState === 'open' || fillState === 'others'"
                type="button"
                class="fill-btn"
                :disabled="fillBusy"
                @click="doFill"
              >{{ fillBusy ? '求补中…' : '求补这集' }}</button>
              <span v-else-if="fillState === 'mine'" class="fill-done">已求补</span>
              <span v-else-if="fillState === 'voted'" class="fill-done">已附议</span>
            </template>
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
import {
  embyApi as embyProtocol,
  formatDuration,
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
.sel-wrap {
  margin-top: 12px;
}
.miss-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  padding: 10px 12px;
  border: 1px dashed var(--au-primary, #016a71);
  border-radius: 10px;
  background: var(--au-bg-soft, #f4f3f0);
  margin-bottom: 14px;
}
.miss-text {
  font-size: 13px;
  line-height: 1.5;
  color: var(--ink, #27251e);
}
.miss-done {
  font-size: 12.5px;
  color: var(--au-primary, #016a71);
  font-weight: 600;
}
.season-sec {
  margin-top: 18px;
}
.season-sec:first-of-type {
  margin-top: 4px;
}
.season-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  margin-bottom: 8px;
}
.season-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--ink, #27251e);
  margin: 0;
}
.season-count {
  font-size: 12px;
  color: var(--ink2, #6f6a5e);
}
.season-head .fill-btn,
.season-head .fill-done {
  margin-left: auto;
}
.ep-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ep-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border: 1px solid var(--paper2, #e3e3e2);
  border-radius: 10px;
  min-width: 0;
}
.ep-row.missing {
  border-style: dashed;
  background: var(--au-bg-soft, #f4f3f0);
  opacity: 0.85;
}
.ep-num {
  flex: 0 0 auto;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink2, #6f6a5e);
  min-width: 44px;
}
.ep-row.missing .ep-num {
  color: var(--au-text-3, #a8a29a);
}
.ep-name {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 13.5px;
  color: var(--ink, #27251e);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ep-dur {
  flex: 0 0 auto;
  font-size: 12px;
  color: var(--ink2, #6f6a5e);
}
.ep-played {
  flex: 0 0 auto;
  font-size: 11px;
  color: var(--au-primary, #016a71);
  border: 1px solid currentColor;
  border-radius: 999px;
  padding: 1px 7px;
}
.ep-missing-label {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 13px;
  color: var(--ink2, #6f6a5e);
  display: flex;
  align-items: center;
  gap: 8px;
}
.miss-badge {
  flex: 0 0 auto;
  font-size: 11px;
  font-weight: 700;
  color: #fff;
  background: var(--au-text-3, #a8a29a);
  border-radius: 6px;
  padding: 1px 6px;
}
.fill-btn {
  flex: 0 0 auto;
  font-size: 12.5px;
  font-weight: 600;
  color: #fff;
  background: var(--au-primary, #016a71);
  border: 1px solid var(--au-primary, #016a71);
  border-radius: 999px;
  padding: 5px 13px;
  cursor: pointer;
}
.fill-btn:disabled {
  opacity: 0.55;
  cursor: wait;
}
.fill-done {
  flex: 0 0 auto;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--au-primary, #016a71);
}
</style>
