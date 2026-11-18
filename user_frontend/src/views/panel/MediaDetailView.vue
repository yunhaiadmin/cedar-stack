<script setup lang="ts">
/**
 * 条目详情页（/media/:id）—— 批 3 片库面：
 * 「信息 + 季集齐全度 + 播放主入口」——2026-11-13 用户新口径下
 * 页面组织按 UHDNow 打破重排：播放主按钮走现行 PlayerSchemeSheet
 * （门禁/绑盘引导/起播口径都在弹层内，不在本页另起逻辑）；
 * 剧集的季/集列表与缺集求补在 SeriesEpisodeList 里。
 * 版式按 /stream 详情页实机补拆（存档第 5/6 节）：居中容器约 1135、
 * 剧照带 Hero 高约 340 渐隐、海报 220×330/圆角 16、类型 chip 60×28、
 * 按钮行高 52、简介 16px/行高 28/3 行截断、右侧信息栏竖分隔线 +
 * TMDB/IMDb chip 85×28、电影「可播放版本」卡 580×70/圆角 18。
 * Aetrix 无片名 Logo 字标图，剧集标题用文字 H1（数据驱动）。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ArrowLeft, ArrowUpRight, BellPlus, Captions, ChevronRight, Clapperboard, Heart, MessagesSquare, Play, Trash2 } from 'lucide-vue-next'
import Modal from '@/components/ui/Modal.vue'
import {
  embyApi as embyProtocol,
  backdropUrl,
  formatDuration,
  imageUrl,
  posterUrl,
  ticksToSeconds,
  type EmbyItem,
} from '@/api/emby'
import { embyApi, mediaSeekApi, type PortalResumeItem } from '@/api'
import { seriesFollowApi } from '@/api/economy'
import { fetchDanmakuWindow } from '@/api/danmaku'
import { embeddedSubLabel, subLangLabel } from '@/utils/subtitleLabels'
import SeriesEpisodeList from '@/components/panel/SeriesEpisodeList.vue'
import PlayerSchemeSheet from '@/components/panel/PlayerSchemeSheet.vue'
import DetailTopPill from '@/components/panel/DetailTopPill.vue'
import StreamShell from '@/components/stream/StreamShell.vue'
import StreamRow from '@/components/stream/StreamRow.vue'
import StreamPosterCard from '@/components/stream/StreamPosterCard.vue'
import { useToast } from '@/composables/useToast'

const route = useRoute()
const router = useRouter()
const toast = useToast()

const loading = ref(false)
const error = ref('')
const item = ref<EmbyItem | null>(null)
const posterBroken = ref(false)
const favBusy = ref(false)
const removeOpen = ref(false)
const removeBusy = ref(false)
const overviewExpanded = ref(false)
const sheetOpen = ref(false)
const recommendations = ref<EmbyItem[]>([])

const itemId = computed(() => String(route.params.id || ''))

const tmdbId = computed<string | null>(() => {
  const ids = item.value?.ProviderIds || {}
  return ids.Tmdb || ids.TMDB || null
})
const imdbId = computed<string | null>(() => {
  const ids = item.value?.ProviderIds || {}
  return ids.Imdb || ids.IMDB || null
})

const poster = computed(() => (item.value ? posterUrl(item.value, 440) : ''))
/** 海报缺失/加载失败时的占位：片名首字（数据驱动，不写死） */
const posterInitial = computed(() => (item.value?.Name || '').trim().charAt(0) || 'A')
const backdrop = computed(() => (item.value ? backdropUrl(item.value, 1600) : ''))

/* ---------- 顶图二段兜底（FE-15，与 StreamHero imgStage 同口径） ----------
 * 顶图先 backdrop（无 backdrop 时用 poster 兜底），失败换 poster，
 * 再失败落空态 div，不留破图区；切条目在 load() 里复位。 */
const heroStage = ref(0)
const heroSrc = computed(() => {
  if (heroStage.value === 0) return backdrop.value || poster.value
  if (heroStage.value === 1) {
    // 首张是 backdrop 时才有 poster 可换；首张已是 poster 则无下一张
    return backdrop.value && poster.value && poster.value !== backdrop.value
      ? poster.value
      : ''
  }
  return ''
})
/** 有图但顶图全失败：海报卡不再按「与顶图重复」连带隐藏，
    改由海报卡自己的 @error/占位兜底（FE-15） */
const heroBroken = computed(() => !!(backdrop.value || poster.value) && !heroSrc.value)
function onHeroError() {
  heroStage.value += 1
}
const isSeries = computed(() => item.value?.Type === 'Series')
const typeLabel = computed(() => (isSeries.value ? '剧集' : item.value?.Type === 'Movie' ? '电影' : item.value?.Type || ''))

/** 详情页专用时长文案（友站口径「2 小时 19 分」，数字与单位带空格）。
    不动共享 formatDuration：选集弹层与集行还在用旧文案（StreamHomeView
    注释先例——只改本面格式，不动共享函数）。 */
function formatDurationSpaced(seconds: number): string {
  if (!seconds || seconds <= 0) return '--'
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  return h > 0 ? `${h} 小时 ${m} 分` : `${m} 分钟`
}

/** 元信息行顺序（友站口径）：年份 →「N 季」→ 电影时长 → ★评分；
    剧集时长（若有）仍压尾，不抢评分前的位置 */
const metaParts = computed(() => {
  const it = item.value
  if (!it) return [] as string[]
  const parts: string[] = []
  if (it.ProductionYear) parts.push(String(it.ProductionYear))
  if (isSeries.value && it.ChildCount) parts.push(`${it.ChildCount} 季`)
  if (!isSeries.value && it.RunTimeTicks) parts.push(formatDurationSpaced(ticksToSeconds(it.RunTimeTicks)))
  if (it.CommunityRating) parts.push(`★ ${it.CommunityRating.toFixed(1)}`)
  if (isSeries.value && it.RunTimeTicks) parts.push(formatDurationSpaced(ticksToSeconds(it.RunTimeTicks)))
  return parts
})

/** 门户续看条目（按条目 id 匹配）：给播放钮与续看胶囊提供季/集/集名 */
const resumeEntry = ref<PortalResumeItem | null>(null)
async function loadResume(my: number) {
  resumeEntry.value = null
  try {
    const res = await embyApi.getResume(50)
    if (my !== loadSeq) return
    resumeEntry.value = (res?.items || []).find((x) => String(x.id) === itemId.value) || null
  } catch {
    if (my !== loadSeq) return
    resumeEntry.value = null
  }
}

// === G-99 playButtonKindOf 开始 ===
/* 主钮三态纯判定（2026-11-18 UHDNow 差异定案 G-99；tests/playButtonState
   .test.mjs 抽取本段真执行，勿改段首/段尾标记、勿在本段引用段外变量）。
   判定边界：
   ① hasResumePoint → 'resume'「继续播放」：有未看完的续播点；优先于②——
      看到一半的那集就是续播集，不是「下一集」；
   ② 无续播点、已看完 ≥1 集且仍有未看集 → 'next'「播放下一集」：
      定位＝集列表顺序第一个未看集；
   ③ 其余 → 'immediate'「立即播放」：无任何观看记录，或全部看完
      （定位第一集）；集计数拿不到（watchedCount/totalCount 为 0）也落③，
      保守不误报「下一集」。
   电影无下一集概念：isSeries=false 恒 'immediate'，三态不改电影形态。
   起播定位不在本页另传目标：点击仍只开 PlayerSchemeSheet，弹层现行选集
   口径（断点集优先 → 未看完集 → 第一个未看集 → 第一集）与三态一一对应。 */
type PlayButtonKind = 'resume' | 'next' | 'immediate'
interface PlayButtonInput {
  isSeries: boolean
  hasResumePoint: boolean
  watchedCount: number
  totalCount: number
}
function playButtonKindOf(input: PlayButtonInput): PlayButtonKind {
  if (!input.isSeries) return 'immediate'
  if (input.hasResumePoint) return 'resume'
  if (input.watchedCount > 0 && input.totalCount > input.watchedCount) return 'next'
  return 'immediate'
}
const PLAY_BUTTON_LABELS: Record<PlayButtonKind, string> = {
  resume: '继续播放',
  next: '播放下一集',
  immediate: '立即播放',
}
// === G-99 playButtonKindOf 结束 ===

/** 已看集数：总集数 RecursiveItemCount − 未看集数 UnplayedItemCount
    （与 canRemoveRecord 同一口径）；计数拿不到时 0（判定保守落③）；
    剧行 Played 为真（全看完）时即总集数 */
const seriesWatchedCount = computed(() => {
  const total = item.value?.RecursiveItemCount || 0
  const unplayed = item.value?.UserData?.UnplayedItemCount
  if (total > 0 && typeof unplayed === 'number') return Math.max(0, total - unplayed)
  return item.value?.UserData?.Played && total > 0 ? total : 0
})

/** 播放主按钮三态：续播点＝续看记录命中本剧，或条目自身 UserData
    留有 PlaybackPositionTicks 断点 */
const playButtonKind = computed(() =>
  playButtonKindOf({
    isSeries: isSeries.value,
    hasResumePoint:
      !!resumeEntry.value || (item.value?.UserData?.PlaybackPositionTicks || 0) > 0,
    watchedCount: seriesWatchedCount.value,
    totalCount: item.value?.RecursiveItemCount || 0,
  }),
)
const playLabel = computed(() => PLAY_BUTTON_LABELS[playButtonKind.value])

const resumeText = computed(() => {
  const e = resumeEntry.value
  if (e && e.type === 'series' && e.season_number != null && e.episode_number != null) {
    /* 封顶 99 与下方 ticks 分支、首页续看同口径（FE-20）：未看完不显示 100% */
    const pct = typeof e.progress === 'number' ? ` · 已看 ${Math.min(99, Math.round(e.progress))}%` : ''
    return `续看 S${e.season_number}：E${e.episode_number}${e.episode_name ? `《${e.episode_name}》` : ''}${pct}`
  }
  const pos = item.value?.UserData?.PlaybackPositionTicks || 0
  if (!pos || !item.value?.RunTimeTicks) return ''
  const pct = Math.min(99, Math.round((pos / item.value.RunTimeTicks) * 100))
  return `上次看到 ${pct}% · ${formatDuration(ticksToSeconds(pos))} 处`
})

/**
 * 「移除观看记录」走真接口 Emby 的 DELETE /Users/{uid}/PlayedItems/{id}
 * （后端 mark_unplayed 清已看标记、播放次数与断点，条目随之离开继续观看）。
 * 电影上它就是整部口径；剧集后端已级联：打到剧行时同事务清掉名下
 * 全部季/集的本人记录，所以剧集同钮开放、语义同为整部移除。
 * 门户口的 hide/unhide（藏起来、记录还在）语义不同，不许拿来冒充。
 */
const canRemoveRecord = computed(() => {
  const ud = item.value?.UserData
  if (ud?.Played || (ud?.PlaybackPositionTicks || 0) > 0 || resumeEntry.value) return true
  if (isSeries.value) {
    // 剧行自身没断点时靠集级信号：总集数 > 未看集数 = 至少一集留有记录
    const total = item.value?.RecursiveItemCount || 0
    const unplayed = ud?.UnplayedItemCount
    return total > 0 && typeof unplayed === 'number' && unplayed < total
  }
  return false
})

async function removeWatchRecord() {
  const it = item.value
  if (!it || removeBusy.value) return
  removeBusy.value = true
  try {
    const ud = await embyProtocol.setPlayed(it.Id, false)
    const mergedUd = { ...it.UserData, ...ud, Played: false, PlaybackPositionTicks: 0 }
    if (isSeries.value && it.RecursiveItemCount) {
      // 各集已全清：未看集数回到总集数，角标与钮态不用等重新拉取
      mergedUd.UnplayedItemCount = it.RecursiveItemCount
    }
    item.value = { ...it, UserData: mergedUd }
    resumeEntry.value = null
    removeOpen.value = false
    toast.success('观看记录已移除')
    void loadResume(loadSeq)
  } catch (err) {
    toast.error(`移除失败：${errText(err)}`)
  } finally {
    removeBusy.value = false
  }
}

/** 动态范围标签：VideoRange 原值优先，判不出再从 DisplayTitle 里认，
    两处都没有证据就不出（不许默认 SDR 冒充探测结果） */
function rangeChipOf(video?: { VideoRange?: string | null; DisplayTitle?: string } | null): string {
  const raw = (video?.VideoRange || '').trim()
  if (raw) {
    if (/dolby/i.test(raw)) return 'Dolby Vision'
    if (/hdr10\+/i.test(raw)) return 'HDR10+'
    if (/hdr10/i.test(raw)) return 'HDR10'
    if (/hlg/i.test(raw)) return 'HLG'
    if (/hdr/i.test(raw)) return 'HDR'
    if (/sdr/i.test(raw)) return 'SDR'
    return ''
  }
  const t = video?.DisplayTitle || ''
  for (const tok of ['Dolby Vision', 'HDR10+', 'HDR10', 'HLG', 'HDR']) {
    if (t.includes(tok)) return tok
  }
  return ''
}

/** 声道标签：Atmos 看音轨标题里的真实标记，其余按声道布局/声道数；
    判不出返回空（不猜布局） */
function audioChipOf(streams: NonNullable<EmbyItem['MediaSources']>[number]['MediaStreams']): string {
  const audios = (streams || []).filter((s) => s.Type === 'Audio')
  if (!audios.length) return ''
  if (audios.some((s) => /atmos/i.test(`${s.DisplayTitle || ''} ${s.Title || ''}`))) return '杜比全景声'
  const a = audios.find((s) => s.IsDefault) || audios[0]
  const layout = (a.ChannelLayout || '').toLowerCase()
  if (layout.includes('7.1')) return '7.1 声道'
  if (layout.includes('5.1')) return '5.1 声道'
  if (layout === 'stereo' || layout === '2.0') return '2.0 声道'
  const byChannels: Record<number, string> = { 8: '7.1 声道', 6: '5.1 声道', 2: '2.0 声道', 1: '1.0 声道' }
  return a.Channels ? byChannels[a.Channels] || '' : ''
}

/** 中文字幕：字幕轨语言为中文或文件名/标题带中文标记才算，不猜 */
function hasChineseSub(streams: NonNullable<EmbyItem['MediaSources']>[number]['MediaStreams']): boolean {
  return (streams || []).some((s) => {
    if (s.Type !== 'Subtitle') return false
    const lang = (s.Language || '').trim().toLowerCase()
    if (['chi', 'zho', 'zh', 'zh-cn', 'zh-tw', 'zh-hans', 'zh-hant'].includes(lang)) return true
    return /中文|简中|繁中|chs|cht/i.test(s.DisplayTitle || '')
  })
}

/**
 * 规格胶囊（友站详情形态）：分辨率 / 动态范围 / 声道 / 中文字幕，
 * 全部由主源的真实探测字段驱动，没有就不渲染——与播放弹层版本卡
 * 同一套口径（弹层展开网格是同一批字段）。旧口径（分级 + 容器格式）
 * 不是规格，已撤：分级在信息栏、容器在弹层展开网格里。
 */
const specChips = computed(() => {
  const it = item.value
  if (!it) return [] as string[]
  const src = (it.MediaSources || [])[0]
  const streams = src?.MediaStreams || []
  const video = streams.find((s) => s.Type === 'Video')
  const chips: string[] = []
  const w = video?.Width ?? it.Width
  const h = video?.Height ?? it.Height
  if (w && h) {
    chips.push(h >= 2000 || w >= 3800 ? '4K' : h >= 1000 || w >= 1900 ? '1080p' : h >= 700 || w >= 1200 ? '720p' : `${h}p`)
  }
  const range = rangeChipOf(video)
  if (range) chips.push(range)
  const audio = audioChipOf(streams)
  if (audio) chips.push(audio)
  if (hasChineseSub(streams)) chips.push('中文字幕')
  return chips
})

/** 类型胶囊（与规格胶囊同流同款描边，见模板 md-chips 区） */
const genreChips = computed(() => (item.value?.Genres || []).slice(0, 4))

/** 信息表「链接」行外链（友站形态：描边胶囊钮 TMDB ↗ / IMDb ↗） */
const tmdbUrl = computed(() => {
  if (!tmdbId.value) return ''
  return isSeries.value
    ? `https://www.themoviedb.org/tv/${tmdbId.value}`
    : `https://www.themoviedb.org/movie/${tmdbId.value}`
})
const imdbUrl = computed(() =>
  imdbId.value ? `https://www.imdb.com/title/${imdbId.value}/` : '',
)

/** 推荐卡元信息（友站口径：年份 · 类型，评分由卡内粗体段拼） */
function recMeta(rec: EmbyItem): string {
  return [rec.ProductionYear ? String(rec.ProductionYear) : '', (rec.Genres || [])[0] || '']
    .filter(Boolean)
    .join(' · ')
}

interface VersionCard {
  key: string
  label: string
  sub: string
  size: string
}
/** 人员键（与后端 portal.normalize_person_name 同口径）：姓名归一化后拼
 * name: 前缀；人员详情接口会把姓名键升级解析为 tmdb 规范键。 */
function personKey(name: string): string {
  return `name:${(name || '').trim().replace(/\s+/g, ' ').toLowerCase()}`
}

function fmtSize(bytes?: number | null): string {
  if (!bytes) return ''
  const gb = bytes / 1_000_000_000
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1_000_000))} MB`
}
const versionCards = computed<VersionCard[]>(() => {
  const it = item.value
  if (!it || isSeries.value) return []
  if (it.Versions?.length) {
    return it.Versions.map((v) => ({
      key: v.Id,
      label: v.VersionLabel,
      sub: [v.Width && v.Height ? `${v.Width}×${v.Height}` : '', v.Container || ''].filter(Boolean).join(' · '),
      size: fmtSize(v.SizeBytes),
    }))
  }
  return (it.MediaSources || []).map((s, i) => {
    const video = (s.MediaStreams || []).find((m) => m.Type === 'Video')
    return {
      key: s.Id || String(i),
      label: video?.DisplayTitle || s.Name || `版本 ${i + 1}`,
      sub: s.Container ? s.Container.toUpperCase() : '',
      size: fmtSize(s.Size),
    }
  })
})

interface SubtitleTrack {
  key: string
  external: boolean
  label: string
  meta: string
}

/**
 * 字幕轨清单（FE-14，只读展示）：与播放弹层同源——条目详情 DTO 的
 * MediaSources[].MediaStreams 里 Type=Subtitle 的轨；外挂轨看
 * DisplayTitle（文件名）/Language/Codec，内嵌轨走共用标签函数，
 * 语言口径与弹层完全一致（@/utils/subtitleLabels）。多源之间按
 * （内外+语言+格式+标题）去重，同轨不摆两遍。选轨动作不在本页，
 * 仍走播放弹层（点「播放时选择」打开同一弹层）。
 * 剧集的字幕挂在各集上、剧行 DTO 没有 MediaSources，这里自然为空，
 * 区块只留入口文案，不拿别集的字幕凑数。
 */
const subtitleTracks = computed<SubtitleTrack[]>(() => {
  const it = item.value
  if (!it) return []
  const seen = new Set<string>()
  const out: SubtitleTrack[] = []
  for (const src of it.MediaSources || []) {
    for (const s of src.MediaStreams || []) {
      if (s.Type !== 'Subtitle') continue
      const dedupe = [
        s.IsExternal ? 'ext' : 'emb',
        s.Language || '',
        s.Codec || '',
        s.DisplayTitle || '',
        s.Title || '',
      ].join('|')
      if (seen.has(dedupe)) continue
      seen.add(dedupe)
      if (s.IsExternal) {
        const file = s.DisplayTitle || ''
        const codec = (s.Codec || file.split('.').pop() || '').toLowerCase()
        out.push({
          key: dedupe,
          external: true,
          label: `${subLangLabel(s.Language, file)}${codec ? ` · ${codec}` : ''}`,
          meta: file,
        })
      } else {
        out.push({
          key: dedupe,
          external: false,
          label: embeddedSubLabel(s),
          meta: s.IsTextSubtitleStream ? '文本字幕' : '图片字幕 · 网页暂不支持',
        })
      }
    }
  }
  return out
})

/** 字幕卡副行摘要：前三条标签，多的收成「等 N 条」（清单在卡下全列） */
const subtitleSummary = computed(() => {
  const labels = subtitleTracks.value.map((t) => t.label)
  if (labels.length <= 3) return labels.join('、')
  return `${labels.slice(0, 3).join('、')} 等 ${labels.length} 条`
})

/**
 * 弹幕入口（FE-14，站内自发弹幕）：显隐口径与播放器完全一致——
 * 拉一次弹幕窗，接口 404（站点开关 danmaku_enabled 关）就整块不
 * 渲染；开着才显示入口与存量。发送仍在播放器里（要对时间点），
 * 本页入口走现行播放弹层起播，不另起逻辑。计数是拉取上限内的
 * 条数（后端单窗上限 500），到上限显示 500+，不冒充精确总数。
 */
const danmakuOn = ref(false)
const danmakuCount = ref(0)
async function loadDanmaku() {
  danmakuOn.value = false
  danmakuCount.value = 0
  const it = item.value
  if (!it) return
  const end = it.RunTimeTicks ? ticksToSeconds(it.RunTimeTicks) : 7 * 24 * 3600
  try {
    const win = await fetchDanmakuWindow(it.Id, 0, Math.max(end, 1))
    if (item.value?.Id !== it.Id) return
    danmakuOn.value = true
    danmakuCount.value = (win.items || []).length
  } catch {
    danmakuOn.value = false
  }
}

const danmakuNote = computed(() => {
  if (isSeries.value) return '弹幕按集发送，播放时可以看、也可以发'
  if (danmakuCount.value > 0) {
    return `已有 ${danmakuCount.value}${danmakuCount.value >= 500 ? '+' : ''} 条弹幕，播放时会滚动显示`
  }
  return '还没有人发弹幕，播放时来发第一条'
})

function errText(err: unknown): string {
  const msg = (err as { userMessage?: string })?.userMessage
  if (msg) return msg
  if (err instanceof Error) return err.message
  return '加载失败'
}

async function loadRecommendations(it: EmbyItem, my: number) {
  recommendations.value = []
  const genre = it.Genres?.[0]
  if (!genre) return
  try {
    const res = await embyProtocol.getItems({
      genres: [genre],
      includeTypes: [it.Type === 'Series' ? 'Series' : 'Movie'],
      limit: 12,
    })
    if (my !== loadSeq) return
    recommendations.value = (res.Items || []).filter((x) => x.Id !== it.Id).slice(0, 10)
  } catch {
    if (my !== loadSeq) return
    recommendations.value = []
  }
}

/** 详情 load 竞态守卫（与 FE-10 同型 seq）：切条目时旧慢响应不许覆盖新条目；
 * 子加载（推荐/续看/追剧）与 load() 共用同一号，旧号落共享状态前一律丢弃。 */
let loadSeq = 0

async function load() {
  if (!itemId.value) {
    error.value = '条目地址不对'
    return
  }
  const my = ++loadSeq
  loading.value = true
  error.value = ''
  posterBroken.value = false
  heroStage.value = 0
  overviewExpanded.value = false
  try {
    const it = await embyProtocol.getItem(itemId.value)
    if (my !== loadSeq) return
    item.value = it
    void loadRecommendations(item.value, my)
    void loadResume(my)
    void loadFollowState(my)
    void loadDanmaku()
  } catch (err) {
    if (my !== loadSeq) return
    item.value = null
    error.value = `详情没加载出来：${errText(err)}`
  } finally {
    if (my === loadSeq) loading.value = false
  }
}

async function toggleFavorite() {
  const it = item.value
  if (!it || favBusy.value) return
  favBusy.value = true
  const next = !it.UserData?.IsFavorite
  try {
    const ud = await embyProtocol.setFavorite(it.Id, next)
    item.value = { ...it, UserData: { ...it.UserData, ...ud, IsFavorite: next } }
    toast.success(next ? `已收藏《${it.Name}》` : `已取消收藏《${it.Name}》`)
  } catch (err) {
    toast.error(`收藏操作失败：${errText(err)}`)
  } finally {
    favBusy.value = false
  }
}

/** 追剧订阅（同上归位；管理面板在账号设置页）：仅剧集。订阅口只认数字
 * 主键，走站内查库口按条目 GUID 对位取 item_id（与订阅面板同口径）；
 * 接口 404（未启用）或对不上主键时整钮不显示。 */
const followVisible = ref(false)
const followBusy = ref(false)
const followSubscribed = ref(false)
const followSeriesId = ref<number | null>(null)
async function loadFollowState(my: number) {
  followVisible.value = false
  followSubscribed.value = false
  followSeriesId.value = null
  const it = item.value
  if (!it || it.Type !== 'Series') return
  let follows
  try {
    const res = await seriesFollowApi.list()
    if (my !== loadSeq) return
    follows = res.follows || []
  } catch { return /* 未启用/失败：整钮不显示 */ }
  try {
    const res = await mediaSeekApi.lookup(it.Name)
    if (my !== loadSeq) return
    const cands = (res.items || []).filter(
      i => i.type === 'series' && typeof i.item_id === 'number',
    )
    const hit = cands.find(i => i.id === it.Id) || (cands.length === 1 ? cands[0] : undefined)
    if (!hit || typeof hit.item_id !== 'number') return
    followSeriesId.value = hit.item_id
  } catch { return }
  followSubscribed.value = follows.some(
    f => f.series_item_id === followSeriesId.value && f.status === 'active',
  )
  followVisible.value = true
}
async function toggleFollow() {
  const sid = followSeriesId.value
  if (sid == null || followBusy.value) return
  followBusy.value = true
  try {
    if (followSubscribed.value) {
      await seriesFollowApi.unsubscribe(sid)
      followSubscribed.value = false
      toast.success('已取消追剧订阅')
    } else {
      await seriesFollowApi.subscribe(sid)
      followSubscribed.value = true
      toast.success('已订阅追剧，新集到货会自动转存到你的盘')
    }
  } catch (err) {
    toast.error(`追剧订阅操作失败：${errText(err)}`)
  } finally {
    followBusy.value = false
  }
}

function goBack() {
  if (window.history.length > 1) router.back()
  else void router.push('/library')
}

onMounted(() => {
  if (import.meta.env.VITE_FIXTURE === '1') {
    loading.value = false
    item.value = {
      Id: 'fixture-1',
      Name: '夹具影片',
      Type: 'Movie',
      ProductionYear: 2026,
      Overview: '夹具简介，不是片库条目。',
      Genres: ['夹具'],
      RunTimeTicks: 3600 * 10_000_000,
    } as typeof item.value
    return
  }
  void load()
})
watch(itemId, () => {
  item.value = null
  recommendations.value = []
  void load()
})
</script>

<template>
  <StreamShell>
    <!-- 顶层错误 -->
    <div v-if="error" class="md-wrap md-state" role="alert">
      {{ error }}<br />
      <button type="button" class="md-retry" :disabled="loading" @click="load">
        {{ loading ? '正在重试…' : '重试' }}
      </button>
    </div>
    <!-- 骨架屏（录屏形态）：剧照带/海报/文本行灰块 + 居中站标，尺寸贴真实布局 -->
    <div v-else-if="loading && !item" class="md-skel" role="status" aria-label="正在加载">
      <div class="md-skel__band"><span class="md-skel__logo">A</span></div>
      <div class="md-wrap md-skel__body">
        <div class="md-skel__poster"><span class="md-skel__logo">A</span></div>
        <div class="md-skel__lines">
          <span class="md-skel__bar" style="width: 46%"></span>
          <span class="md-skel__bar" style="width: 68%"></span>
          <span class="md-skel__bar" style="width: 30%"></span>
        </div>
      </div>
    </div>

    <template v-else-if="item">
      <!-- 剧照带 Hero：桌面高约 340 渐隐；移动端全幅 backdrop 大图
           （2026-11-14 用户截图亲点友站形态）：高约 40vh、底部渐隐入
           页底，类型/片名/信息行叠在渐隐尾上，无海报卡 -->
      <div class="md-hero">
        <!-- 无 backdrop 的条目用海报图兜底当顶图，避免顶图带空白；
             顶图失败二段降级（FE-15）：先换 poster，再失败落空态 -->
        <img v-if="heroSrc" :src="heroSrc" alt="" class="md-hero__img" @error="onHeroError" />
        <div v-else-if="backdrop || poster" class="md-hero__img md-hero__img--empty" aria-hidden="true"></div>
        <div class="md-hero__fade" aria-hidden="true"></div>
        <button type="button" class="md-back" aria-label="返回" @click="goBack">
          <ArrowLeft :size="20" />
        </button>
        <!-- 移动端右上玻璃胶囊：月牙主题切换 + X 关闭（仅移动端显示） -->
        <DetailTopPill @close="goBack" />
      </div>

      <div class="md-wrap">
        <p class="md-type">{{ typeLabel }}</p>
        <h1 class="md-title" :class="{ 'md-title--movie': !isSeries }">{{ item.Name }}</h1>
        <div class="md-main">
          <!-- 有顶图（backdrop 或海报兜底）时移动端隐藏海报卡（顶图就是
               主视觉，不重复摆卡）；两样图都没有、或顶图加载全失败
               （heroBroken，FE-15）时保留海报卡，由它自己兜底 -->
          <div class="md-poster" :class="{ 'md-poster--dup': !!(backdrop || poster) && !heroBroken }">
            <img v-if="poster && !posterBroken" :src="poster" :alt="item.Name" class="md-poster__img" @error="posterBroken = true" />
            <span v-else class="md-poster__fallback" aria-hidden="true">{{ posterInitial }}</span>
          </div>

          <div class="md-info">
            <!-- 信息行（友站形态）：年份 · 季数 · 时长 · ★评分纯文字一行；
                 其下类型与规格描边胶囊同流排布、同款 .md-chip（友站形态） -->
            <p class="md-meta">
              <template v-for="(p, i) in metaParts" :key="i">{{ i ? ' · ' : '' }}{{ p }}</template>
            </p>
            <div v-if="genreChips.length || specChips.length" class="md-chips">
              <span v-for="g in genreChips" :key="`g-${g}`" class="md-chip">{{ g }}</span>
              <span v-for="c in specChips" :key="c" class="md-chip">{{ c }}</span>
            </div>
            <p v-if="resumeText" class="md-resume">
              <!-- 实心三角 SVG：原 ▶ 字符在移动端渲染成蓝色 emoji 方块，已换掉 -->
              <svg class="md-resume__tri" width="9" height="10" viewBox="0 0 12 12" aria-hidden="true">
                <path d="M2 1.3v9.4c0 .8.9 1.3 1.6.9l7.2-4.7c.6-.4.6-1.4 0-1.8L3.6.4C2.9-.1 2 .5 2 1.3Z" fill="currentColor" />
              </svg>
              {{ resumeText }}
            </p>

            <!-- 按钮行高 52：主胶囊（文案三态见脚本 playButtonKindOf，
                 G-99：继续播放/播放下一集/立即播放）+ 收藏 + 第二副钮 + 状态。
                 第二副钮互斥状态机（2026-11-18 垃圾桶语义族定案 G-103/G-105）：
                 有观看记录→垃圾桶（移除观看记录）、无记录→追剧订阅铃铛，
                 二者永不同时出现；电影无铃铛（追剧是剧集专属），无记录时
                 只剩收藏一枚。追剧订阅原在概览「我的片子」组，2026-11-13
                 按用户令摘组后归位到详情页动作区（显隐口径见脚本
                 loadFollowState：拿不到数据整钮不显示）。「想看」钮
                 已随想看商店删除。
                 2026-11-13 用户新口径：页面组织/交互可按 UHDNow 打破重排，
                 详情页恢复播放主入口；播放走现行 PlayerSchemeSheet，
                 选播放器/起播状态/未绑盘引导都在弹层内，门禁口径不变。 -->
            <div class="md-actions">
              <button type="button" class="md-btn md-btn--play" @click="sheetOpen = true">
                <Play :size="17" :fill="'currentColor'" />
                {{ playLabel }}
              </button>
              <button
                type="button"
                class="md-btn md-btn--fav"
                :class="{ 'md-btn--fav-on': item.UserData?.IsFavorite }"
                :disabled="favBusy"
                @click="toggleFavorite"
              >
                <Heart :size="17" :fill="item.UserData?.IsFavorite ? 'currentColor' : 'none'" />
                {{ item.UserData?.IsFavorite ? '已收藏' : '加入收藏' }}
              </button>
              <button
                v-if="followVisible && !canRemoveRecord"
                type="button"
                class="md-btn md-btn--fav"
                :class="{ 'md-btn--fav-on': followSubscribed }"
                :disabled="followBusy"
                @click="toggleFollow"
              >
                <BellPlus :size="17" :fill="followSubscribed ? 'currentColor' : 'none'" />
                {{ followSubscribed ? '已追剧订阅' : '追剧订阅' }}
              </button>
              <span v-if="item.UserData?.Played" class="md-btn md-btn--state">已看完</span>
              <span v-else-if="resumeText" class="md-btn md-btn--state">正在观看</span>
              <button
                v-if="canRemoveRecord"
                type="button"
                class="md-btn md-btn--remove"
                aria-label="移除观看记录"
                @click="removeOpen = true"
              >
                <Trash2 :size="17" />
                <span class="md-btn--remove-txt">移除观看记录</span>
              </button>
            </div>

            <div v-if="item.Overview" class="md-overview-box">
              <p class="md-overview" :class="{ 'md-overview--open': overviewExpanded }">{{ item.Overview }}</p>
              <button type="button" class="md-expand" @click="overviewExpanded = !overviewExpanded">
                {{ overviewExpanded ? '收起' : '展开' }}
              </button>
            </div>
            <p class="md-note">点「立即播放」选个播放器就能看；本页也能看这部片齐不齐，缺的集可以直接求补，补齐后会通知你。</p>
          </div>

          <!-- 右侧信息栏（移动端排在演职人员之后）：竖分隔线 + 键值表。
               键集照友站：剧集＝剧名/首播/季数/类型/链接，
               电影＝片名/原名/上映/片长/类型/链接（「最近播出」无数据面，
               属 G-125 待定项，本批不造） -->
          <aside class="md-rail" :class="{ 'md-rail--series': isSeries }" aria-label="条目信息">
            <h2 class="md-rail__title">{{ isSeries ? '剧集信息' : '影片信息' }}</h2>
            <dl class="md-rail__list">
              <div v-if="isSeries" class="md-rail__row">
                <dt>剧名</dt><dd>{{ item.Name }}</dd>
              </div>
              <div v-if="!isSeries" class="md-rail__row">
                <dt>片名</dt><dd>{{ item.Name }}</dd>
              </div>
              <div v-if="!isSeries && item.OriginalTitle && item.OriginalTitle !== item.Name" class="md-rail__row">
                <dt>原名</dt><dd>{{ item.OriginalTitle }}</dd>
              </div>
              <div v-if="isSeries && item.PremiereDate" class="md-rail__row">
                <dt>首播</dt><dd>{{ item.PremiereDate.slice(0, 10) }}</dd>
              </div>
              <div v-if="!isSeries && item.PremiereDate" class="md-rail__row">
                <dt>上映</dt><dd>{{ item.PremiereDate.slice(0, 10) }}</dd>
              </div>
              <div v-if="isSeries && item.ChildCount" class="md-rail__row">
                <dt>季数</dt><dd>{{ item.ChildCount }} 季</dd>
              </div>
              <div v-if="!isSeries && item.RunTimeTicks" class="md-rail__row">
                <dt>片长</dt><dd>{{ formatDurationSpaced(ticksToSeconds(item.RunTimeTicks)) }}</dd>
              </div>
              <div v-if="item.Genres?.length" class="md-rail__row">
                <dt>类型</dt><dd>{{ item.Genres.join(' / ') }}</dd>
              </div>
              <div v-if="tmdbId || imdbId" class="md-rail__row">
                <dt>链接</dt>
                <dd class="md-rail__links">
                  <a v-if="tmdbId" class="md-idchip" :href="tmdbUrl" target="_blank" rel="noopener noreferrer">TMDB <ArrowUpRight :size="12" /></a>
                  <a v-if="imdbId" class="md-idchip" :href="imdbUrl" target="_blank" rel="noopener noreferrer">IMDb <ArrowUpRight :size="12" /></a>
                </dd>
              </div>
            </dl>
          </aside>
        </div>

        <!-- 演职人员（DTO 下发才渲染）：头像圆 48、姓名 15 粗、角色 13 灰、横滑 -->
        <section v-if="item.People?.length" class="md-cast">
          <h2 class="md-sec-title">演职人员</h2>
          <div class="md-cast__track">
            <RouterLink
              v-for="(p, i) in item.People.slice(0, 20)"
              :key="`${p.Name}-${i}`"
              class="md-cast__person"
              :to="{ name: 'person-detail', params: { key: personKey(p.Name) } }"
              :aria-label="`查看演职人员 ${p.Name}`"
            >
              <span class="md-cast__avatar">
                <img v-if="p.Id && p.PrimaryImageTag" :src="imageUrl(p.Id, 'Primary', 96)" alt="" loading="lazy" />
                <template v-else>{{ p.Name.charAt(0) }}</template>
              </span>
              <span class="md-cast__name">{{ p.Name }}</span>
              <span v-if="p.Role" class="md-cast__role">{{ p.Role }}</span>
            </RouterLink>
          </div>
        </section>

        <!-- 电影：可播放版本卡 580×70/圆角 18 -->
        <section v-if="versionCards.length" class="md-versions">
          <h2 class="md-sec-title">可播放版本</h2>
          <div class="md-version-list">
            <div v-for="v in versionCards" :key="v.key" class="md-version">
              <span class="md-version__body">
                <span class="md-version__label">{{ v.label }}</span>
                <span v-if="v.sub" class="md-version__sub">{{ v.sub }}</span>
              </span>
              <span v-if="v.size" class="md-version__size">{{ v.size }}</span>
            </div>
          </div>
        </section>

        <!-- 剧集：季与集（缺集求补在 SeriesEpisodeList 内，口径不变） -->
        <section v-if="isSeries" class="md-episodes">
          <h2 class="md-sec-title">
            剧集列表 <span v-if="item.ChildCount" class="md-sec-sub">{{ item.ChildCount }}季</span>
          </h2>
          <SeriesEpisodeList :series-id="item.Id" :tmdb-id="tmdbId" />
        </section>

        <!-- 字幕与弹幕（FE-14）：字幕轨只读清单（与播放弹层同源，
             数据口径见脚本 subtitleTracks），选轨/发弹幕的动作仍在
             播放弹层与播放器里，本页只做可达入口；弹幕卡只在站点
             开关开时渲染（关时接口 404、整卡不出现，与播放器同口径） -->
        <section class="md-av">
          <h2 class="md-sec-title">字幕与弹幕</h2>
          <div class="md-av__list">
            <div class="md-av__card">
              <span class="md-av__body">
                <span class="md-av__label">
                  <Captions :size="16" />
                  字幕
                  <span v-if="subtitleTracks.length" class="md-av__count">{{ subtitleTracks.length }} 条</span>
                </span>
                <span class="md-av__sub">
                  <template v-if="subtitleTracks.length">{{ subtitleSummary }}</template>
                  <template v-else>{{ isSeries ? '字幕按集提供，播放时选' : '暂未探测到字幕轨，播放时可再确认' }}</template>
                </span>
              </span>
              <button type="button" class="md-av__go" @click="sheetOpen = true">
                播放时选择
                <ChevronRight :size="15" />
              </button>
            </div>
            <ul v-if="subtitleTracks.length" class="md-av__tracks">
              <li v-for="t in subtitleTracks" :key="t.key" class="md-av__track">
                <span class="md-av__tag">{{ t.external ? '外挂' : '内嵌' }}</span>
                <span class="md-av__tlabel">{{ t.label }}</span>
                <span v-if="t.meta" class="md-av__tmeta">{{ t.meta }}</span>
              </li>
            </ul>
            <div v-if="danmakuOn" class="md-av__card">
              <span class="md-av__body">
                <span class="md-av__label">
                  <MessagesSquare :size="16" />
                  弹幕
                </span>
                <span class="md-av__sub">{{ danmakuNote }}</span>
              </span>
              <button type="button" class="md-av__go" @click="sheetOpen = true">
                播放时发送
                <ChevronRight :size="15" />
              </button>
            </div>
          </div>
        </section>
      </div>

      <!-- 更多推荐：与首页同款海报横滑行 -->
      <StreamRow v-if="recommendations.length" title="更多推荐" :count="recommendations.length">
        <StreamPosterCard
          v-for="rec in recommendations"
          :key="rec.Id"
          :title="rec.Name"
          :meta="recMeta(rec)"
          :rating="rec.CommunityRating ?? null"
          :image="posterUrl(rec, 520)"
          :to="`/media/${rec.Id}`"
        />
      </StreamRow>
      <div class="md-tail"></div>

      <!-- 选播放器弹层（现行组件的片库面皮）：起播/绑盘引导/状态条都在里面 -->
      <PlayerSchemeSheet
        v-model:open="sheetOpen"
        :title="item.Name"
        :item-id="item.Id"
      />

      <!-- 移除观看记录二次确认（真接口：DELETE PlayedItems，见脚本注释） -->
      <Modal v-model="removeOpen" size="sm">
        <template #header>
          <h3 class="modal-title">移除观看记录</h3>
        </template>
        <p class="md-confirm-text">
          <template v-if="isSeries && item.RecursiveItemCount">
            把《{{ item.Name }}》整部的观看记录清掉？全剧共 {{ item.RecursiveItemCount }} 集，各集的播放进度和「已看完」标记都会清除，这部剧也会从「继续观看」里消失。
          </template>
          <template v-else>
            把《{{ item.Name }}》的观看记录清掉？播放进度和「已看完」标记都会清除，这部片也会从「继续观看」里消失。
          </template>
        </p>
        <template #footer>
          <div class="md-confirm-actions">
            <button type="button" class="md-confirm-btn" @click="removeOpen = false">再想想</button>
            <button
              type="button"
              class="md-confirm-btn md-confirm-btn--danger"
              :disabled="removeBusy"
              @click="removeWatchRecord"
            >
              {{ removeBusy ? '移除中…' : '确定移除' }}
            </button>
          </div>
        </template>
      </Modal>
    </template>
  </StreamShell>
</template>

<style scoped>
.md-wrap {
  max-width: 1135px;
  margin: 0 auto;
  padding: 0 24px;
}
.md-state {
  padding-top: 120px;
  padding-bottom: 80px;
  font-size: 15px;
  color: var(--uhd-s-text-2);
  text-align: center;
}
.md-spinner {
  display: inline-block;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 2px solid var(--uhd-s-border);
  border-top-color: var(--uhd-s-text);
  animation: uhd-spin 0.8s linear infinite;
  vertical-align: -3px;
  margin-right: 8px;
}
.md-retry {
  margin-top: 14px;
  height: 44px;
  padding: 0 26px;
  border-radius: 22px;
  border: 1px solid var(--uhd-s-border);
  background: var(--uhd-s-pill-active-bg);
  color: var(--uhd-s-text);
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  font-family: inherit;
  cursor: pointer;
}
/* 剧照带：高约 340、渐隐至白（深色至近黑） */
.md-hero {
  position: relative;
  height: 340px;
  margin-top: -60px;
  overflow: hidden;
}
.md-hero__img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center 22%;
}
/* 顶图全失败的空态（FE-15）：只留剧照带底色，不出破图 */
.md-hero__img--empty {
  background: var(--uhd-s-card-soft);
}
.md-hero__fade {
  position: absolute;
  inset: 0;
  background: linear-gradient(to bottom, transparent 30%, var(--uhd-s-hero-fade) 98%);
}
/* 返回圆钮 48、白色毛玻璃 */
.md-back {
  position: absolute;
  top: 76px;
  left: max(24px, calc((100vw - 1135px) / 2 + 24px));
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  border: 1px solid var(--uhd-s-pill-border);
  background: var(--uhd-s-pill-bg);
  color: var(--uhd-s-text);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  cursor: pointer;
  z-index: 2;
}
.md-type {
  margin: 10px 0 0;
  font-size: 14px;
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-s-text-2);
}
.md-main {
  display: flex;
  gap: 28px;
  align-items: flex-start;
  margin-top: 10px;
}
/* 海报 220×330/圆角 16 */
.md-poster {
  flex: 0 0 220px;
  width: 220px;
  aspect-ratio: 2 / 3;
  border-radius: 16px;
  overflow: hidden;
  background: var(--uhd-s-card-soft);
  box-shadow: var(--uhd-shadow-card-l);
  display: flex;
  align-items: center;
  justify-content: center;
}
.md-poster__img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
/* 无海报/加载失败占位：片名首字大字 */
.md-poster__fallback {
  color: var(--uhd-s-text-2);
  font-size: 76px;
  font-weight: var(--uhd-fw-black);
  line-height: 1;
}
.md-info {
  flex: 1 1 auto;
  min-width: 0;
}
.md-title {
  margin: 0;
  font-size: 40px;
  font-weight: var(--uhd-fw-black);
  line-height: 1.12;
  color: var(--uhd-s-text);
  letter-spacing: -0.01em;
}
/* 电影版 H1 约 56px 特粗 */
.md-title--movie {
  font-size: 56px;
}
.md-wrap > .md-title {
  margin-top: 8px;
}
@media (min-width: 1200px) {
  .md-wrap {
    max-width: none;
    margin: 0;
    padding: 0 96px;
  }
  .md-back { left: 96px; }
}
.md-meta {
  margin: 12px 0 0;
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-s-text);
}
.md-rating {
  font-weight: var(--uhd-fw-bold);
}
.md-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.md-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 60px;
  height: 28px;
  padding: 0 12px;
  border-radius: 14px;
  border: 1px solid var(--uhd-s-border);
  font-size: 13px;
  color: var(--uhd-s-text-2);
}
/* 底部留白：桌面给页脚让位，移动端另在媒体查询里加高避开玻璃坞 */
.md-tail {
  height: 48px;
}
/* 续看提示胶囊：高 36/圆角 18/边框 */
.md-resume {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin: 16px 0 0;
  max-width: 100%;
  padding: 8px 16px;
  border: 1px solid var(--uhd-s-border);
  border-radius: 18px;
  font-size: 14px;
  color: var(--uhd-s-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.md-resume__tri {
  display: block;
  flex: none;
}
.md-actions {
  display: flex;
  /* 动作钮增至四颗（播放/收藏/想看/追剧订阅），窄屏允许换行不挤压 */
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 20px;
}
.md-actions .md-btn--play {
  flex: 0 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.md-actions .md-btn--ghost {
  flex: none;
}
.md-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 52px;
  padding: 0 26px;
  border-radius: 26px;
  font-size: 16px;
  font-weight: var(--uhd-fw-semibold);
  font-family: inherit;
}
/* 播放主胶囊：高 52/圆角 26 + 彩虹渐变边框 + 玻璃栈（2026-11-13 补齐七彩＋玻璃，
   做法同 Hero 播放胶囊：浅色淡彩粉彩底黑字、深色黑毛玻璃白字） */
.md-btn--play {
  position: relative;
  border: 2px solid transparent;
  background:
    var(--uhd-rainbow-soft) padding-box,
    var(--uhd-rainbow-border-flow) border-box;
  /* 描边流动（G-100）：flow 令牌一图含两周期，200% 宽下位移 0%→100%
     恰为一个周期、无缝衔接；底色层 size auto 不受位移影响 */
  background-size: auto, 200% 100%;
  animation: md-rainbow-flow 6s linear infinite;
  color: #111111;
  cursor: pointer;
  min-width: 135px;
  padding: 0 22px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.65),
    var(--uhd-s-shadow-pill);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
}
@keyframes md-rainbow-flow {
  from {
    background-position: 0 0, 0% 0;
  }
  to {
    background-position: 0 0, 100% 0;
  }
}
@media (prefers-reduced-motion: reduce) {
  .md-btn--play {
    animation: none;
  }
}
/* 深色版：黑毛玻璃底 + 白字（同 StreamHero .sh-play 深色口径） */
.stream-scope[data-stream-theme="dark"] .md-btn--play {
  background:
    linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box,
    var(--uhd-rainbow-border-flow) border-box;
  background-size: auto, 200% 100%;
  color: #ffffff;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.22),
    var(--uhd-s-shadow-pill);
}
.md-btn--fav {
  border: 1px solid var(--uhd-s-border);
  background: var(--uhd-s-pill-active-bg);
  color: var(--uhd-s-text);
  cursor: pointer;
  min-width: 148px;
}
.md-btn--fav-on {
  border-color: var(--uhd-s-text);
}
.md-btn--fav:disabled {
  opacity: 0.55;
  cursor: wait;
}
.md-btn--state {
  background: var(--uhd-s-card-soft);
  color: var(--uhd-s-text-2);
  border: 1px solid transparent;
  flex: none;
  white-space: nowrap;
}
/* 移除观看记录：幽灵钮 + 危险色字，不与播放/收藏抢视觉 */
.md-btn--remove {
  border: 1px solid var(--uhd-s-border);
  background: transparent;
  color: var(--uhd-danger);
  cursor: pointer;
  flex: none;
  white-space: nowrap;
}
.md-btn--remove:hover {
  border-color: var(--uhd-danger);
}
/* 移除确认弹窗（ui/Modal 槽内） */
.md-confirm-text {
  margin: 0;
  font-size: 15px;
  line-height: 1.65;
  color: var(--uhd-s-text);
}
.md-confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
.md-confirm-btn {
  height: 42px;
  padding: 0 20px;
  border-radius: 21px;
  border: 1px solid var(--uhd-s-border);
  background: transparent;
  color: var(--uhd-s-text);
  font-size: 14.5px;
  font-weight: var(--uhd-fw-semibold);
  font-family: inherit;
  cursor: pointer;
}
.md-confirm-btn--danger {
  background: var(--uhd-danger);
  border-color: var(--uhd-danger);
  color: #fff;
}
.md-confirm-btn--danger:disabled {
  opacity: 0.55;
  cursor: wait;
}
/* 简介：16px/行高 28/3 行截断 */
.md-overview-box {
  margin-top: 20px;
  max-width: 645px;
}
.md-overview {
  margin: 0;
  font-size: 16px;
  line-height: 28px;
  color: var(--uhd-s-text);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.md-overview--open {
  -webkit-line-clamp: unset;
}
.md-expand {
  margin-top: 6px;
  padding: 4px 0;
  border: none;
  background: none;
  color: var(--uhd-s-text-2);
  font-size: 14px;
  font-family: inherit;
  cursor: pointer;
}
.md-note {
  margin: 14px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--uhd-s-text-2);
  max-width: 645px;
}
/* 右侧信息栏：竖分隔线 */
.md-rail {
  flex: 0 0 240px;
  border-left: 1px solid var(--uhd-s-divider);
  padding-left: 24px;
}
.md-rail__title {
  margin: 4px 0 12px;
  font-size: 15px;
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-s-text);
}
.md-rail__list {
  margin: 0;
}
.md-rail__row {
  display: flex;
  gap: 12px;
  padding: 5px 0;
  font-size: 15px;
  line-height: 28px;
}
.md-rail__row dt {
  flex: 0 0 44px;
  color: var(--uhd-s-text-2);
  font-size: 14px;
}
.md-rail__row dd {
  margin: 0;
  color: var(--uhd-s-text);
}
/* 「链接」行：TMDB/IMDb 描边胶囊外链（友站形态） */
.md-rail__links {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.md-idchip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  height: 28px;
  padding: 0 10px;
  border-radius: 14px;
  border: 1px solid var(--uhd-s-border);
  font-size: 12px;
  color: var(--uhd-s-text-2);
  text-decoration: none;
}
.md-sec-title {
  margin: 0;
  font-size: 28px;
  font-weight: var(--uhd-fw-extrabold);
  color: var(--uhd-s-text);
}
.md-versions {
  margin-top: 44px;
}
.md-version-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 18px;
}
/* 可播放版本卡：580×70、圆角 18、浅灰底（深色转深灰 + 浅边框） */
.md-version {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  width: min(580px, 100%);
  min-height: 70px;
  padding: 12px 20px;
  border-radius: 18px;
  background: var(--uhd-s-card-soft);
  border: 1px solid var(--uhd-s-border);
}
.md-version__body {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.md-version__label {
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-s-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.md-version__sub {
  font-size: 12px;
  color: var(--uhd-s-text-2);
}
.md-version__size {
  flex: none;
  font-size: 14px;
  color: var(--uhd-s-text);
}
.md-episodes {
  margin-top: 44px;
}
/* 集数条按原站数值全幅 1135：.md-wrap 有 24px 内边距，条带负边距吃掉它 */
.md-episodes :deep(.ep-strip) {
  width: calc(100% + 48px);
  margin-left: -24px;
}
/* 字幕与弹幕（FE-14）：卡面与可播放版本卡同款（580×70/圆角 18/浅灰底），
   不另起样式体系；轨清单是卡下的只读细行 */
.md-av {
  margin-top: 44px;
}
.md-av__list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 18px;
}
.md-av__card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  width: min(580px, 100%);
  min-height: 70px;
  padding: 12px 20px;
  border-radius: 18px;
  background: var(--uhd-s-card-soft);
  border: 1px solid var(--uhd-s-border);
}
.md-av__body {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.md-av__label {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-s-text);
}
.md-av__count {
  font-size: 12px;
  font-weight: 400;
  color: var(--uhd-s-text-2);
}
.md-av__sub {
  font-size: 12px;
  color: var(--uhd-s-text-2);
}
.md-av__go {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 36px;
  padding: 0 14px;
  border-radius: 18px;
  border: 1px solid var(--uhd-s-border);
  background: transparent;
  color: var(--uhd-s-text);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.md-av__tracks {
  list-style: none;
  margin: 0;
  padding: 0 4px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(580px, 100%);
}
.md-av__track {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  min-width: 0;
}
.md-av__tag {
  flex: none;
  font-size: 11px;
  line-height: 18px;
  padding: 0 8px;
  border-radius: 10px;
  border: 1px solid var(--uhd-s-border);
  color: var(--uhd-s-text-2);
}
.md-av__tlabel {
  flex: none;
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-s-text);
  white-space: nowrap;
}
.md-av__tmeta {
  color: var(--uhd-s-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 演职人员横滑 */
.md-cast {
  margin-top: 44px;
}
.md-cast__track {
  display: flex;
  gap: 24px;
  margin-top: 18px;
  overflow-x: auto;
  scrollbar-width: none;
  padding-bottom: 4px;
  /* 两端渐隐：与首页横滑行同口径（mask 淡出到页面底色）；卡窄，渐隐带也窄 */
  -webkit-mask-image: linear-gradient(to right, transparent 0, #000 20px, #000 calc(100% - 20px), transparent 100%);
  mask-image: linear-gradient(to right, transparent 0, #000 20px, #000 calc(100% - 20px), transparent 100%);
}
.md-cast__track::-webkit-scrollbar {
  display: none;
}
.md-cast__person {
  flex: 0 0 106px;
  width: 106px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  text-decoration: none;
  cursor: pointer;
}
.md-cast__avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  overflow: hidden;
  background: var(--uhd-s-card-soft);
  color: var(--uhd-s-text-2);
  font-size: 17px;
  font-weight: var(--uhd-fw-bold);
}
.md-cast__avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.md-cast__name {
  margin-top: 6px;
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-s-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}
.md-cast__role {
  font-size: 13px;
  color: var(--uhd-s-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}
@media (max-width: 900px) {
  .md-main {
    flex-wrap: wrap;
  }
  .md-rail {
    flex: 1 1 100%;
    border-left: none;
    border-top: 1px solid var(--uhd-s-divider);
    padding-left: 0;
    padding-top: 18px;
  }
}
.md-sec-sub {
  font-size: 15px;
  font-weight: var(--uhd-fw-regular);
  color: var(--uhd-s-text-2);
}
/* 详情骨架屏 */
.md-skel__band {
  height: 340px;
  background: var(--uhd-s-card-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: md-pulse 1.4s ease-in-out infinite;
}
.md-skel__body {
  display: flex;
  gap: 28px;
  margin-top: 31px;
}
.md-skel__poster {
  flex: 0 0 220px;
  aspect-ratio: 2 / 3;
  border-radius: 16px;
  background: var(--uhd-s-card-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: md-pulse 1.4s ease-in-out infinite;
}
.md-skel__lines {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-top: 10px;
}
.md-skel__bar {
  height: 22px;
  border-radius: 6px;
  background: var(--uhd-s-card-soft);
}
.md-skel__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: var(--uhd-s-border);
  color: var(--uhd-s-text-2);
  font-size: 20px;
  font-weight: var(--uhd-fw-black);
}
@keyframes md-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}

/* 手机详情 = 友站 app 形态（2026-11-14 用户截图亲点）：全幅 backdrop
   顶图（高约 40vh、底部渐隐入页底）→ 类型小字/大标题/信息行叠在渐隐
   尾上 → 全宽播放行（吸顶）→ 简介 → 演职人员 → 影片信息 → 版本/剧集
   → 推荐。有 backdrop 时不再摆海报卡（顶图即主视觉）；无 backdrop 时
   海报卡仍在内容首位兜底（2026-11-13「详情页没有显示海报」教训）。 */
@media (max-width: 767px) {
  .md-hero {
    height: 36vh;
    min-height: 272px;
    max-height: 330px;
    margin-top: 0;
    padding-top: 0;
  }
  /* 返回钮：圆形玻璃，悬在顶图左侧约垂直居中（截图采样位） */
  .md-back {
    position: absolute;
    top: 50%;
    left: 16px;
    transform: translateY(-50%);
    width: 44px;
    height: 44px;
    margin-left: 0;
  }
  .md-wrap {
    display: flex;
    flex-direction: column;
    padding: 0 20px;
    /* 内容整体压过剧照带：hero 是定位元素、绘制层在普通流之上，
       标题等与渐隐区重叠的元素不抬层就会被渐隐盖掉（实测标题消失） */
    position: relative;
    z-index: 2;
  }
  .md-main {
    display: contents;
  }
  /* 有 backdrop 时海报卡与顶图重复，隐藏；无 backdrop 时仍居首位兜底 */
  .md-poster {
    order: -1;
    flex: none;
    width: 132px;
    margin-top: 12px;
  }
  .md-poster--dup {
    display: none;
  }
  .md-poster__fallback {
    font-size: 44px;
  }
  /* 类型小字叠在顶图渐隐尾上（负边距拉进渐隐区） */
  .md-type {
    order: 0;
    position: relative;
    z-index: 2;
    margin-top: -64px;
    font-size: 13.5px;
  }
  /* info 也打散：让播放行以整页（.md-wrap）为吸顶容器，全程常驻 */
  .md-info {
    display: contents;
  }
  .md-info > * {
    order: 1;
  }
  /* 段序（友站截图）：演职人员 → 影片信息 → 版本/剧集 → 推荐；
     剧集时信息栏仍排在剧集列表之后（先看集、后看信息） */
  .md-cast {
    order: 2;
  }
  .md-rail {
    order: 3;
    border-left: none;
    border-top: 1px solid var(--uhd-s-divider);
    padding-left: 0;
    margin-top: 28px;
    width: auto;
  }
  .md-rail--series {
    order: 5;
  }
  .md-versions {
    order: 4;
  }
  .md-episodes {
    order: 4;
  }
  .md-av {
    order: 4;
  }
  .sr-row {
    order: 6;
  }
  .md-title {
    font-size: 26px;
    line-height: 1.15;
  }
  .md-title--movie {
    font-size: 27px;
  }
  /* 信息行（年份/时长/评分）收小到友站量级 */
  .md-meta {
    margin-top: 10px;
    font-size: 14px;
  }
  .md-sec-title {
    font-size: 21px;
    line-height: 1.3;
  }
  .md-sec-sub {
    font-size: 13.5px;
  }
  /* 简介收小到友站量级（14.5px/23px，仍 3 行截断） */
  .md-overview-box {
    margin-top: 16px;
  }
  .md-overview {
    font-size: 14.5px;
    line-height: 23px;
  }
  /* 友站移动端简介下无说明段，段落直接接演职人员 */
  .md-note {
    display: none;
  }
  /* 演职人员：头像横滑收小（88→72），段距同步收紧 */
  .md-cast {
    margin-top: 32px;
  }
  .md-cast__track {
    gap: 14px;
    margin-top: 14px;
  }
  .md-cast__person {
    flex-basis: 92px;
    width: 92px;
  }
  .md-cast__avatar {
    width: 72px;
    height: 72px;
    font-size: 22px;
  }
  .md-cast__name {
    font-size: 14px;
  }
  .md-cast__role {
    font-size: 12.5px;
  }
  /* 影片信息栏：行高/字号收小，段距收紧 */
  .md-rail__title {
    font-size: 14px;
  }
  .md-rail__row {
    padding: 4px 0;
    font-size: 14px;
    line-height: 24px;
  }
  .md-rail__row dt {
    font-size: 13px;
  }
  /* 版本/剧集段距收紧；详情页版本卡同步收小 */
  .md-versions,
  .md-episodes,
  .md-av {
    margin-top: 32px;
  }
  .md-version-list {
    gap: 10px;
    margin-top: 14px;
  }
  .md-version {
    min-height: 60px;
    padding: 10px 16px;
    border-radius: 16px;
  }
  .md-av__list {
    gap: 10px;
    margin-top: 14px;
  }
  .md-av__card {
    min-height: 60px;
    padding: 10px 16px;
    border-radius: 16px;
  }
  .md-av__label {
    font-size: 14px;
  }
  .md-version__label {
    font-size: 14px;
  }
  .md-version__size {
    font-size: 13px;
  }
  .md-resume {
    padding: 7px 14px;
    font-size: 13.5px;
  }
  /* 底部留白避开全站玻璃坞 */
  .md-tail {
    height: 120px;
  }
  /* 播放行吸顶（录屏 f03 形态）：毛玻璃条常驻顶部（手机已无顶栏） */
  .md-actions {
    position: sticky;
    top: 8px;
    z-index: 8;
    gap: 10px;
    margin-top: 14px;
    margin-left: -20px;
    margin-right: -20px;
    padding: 8px 20px;
    background: var(--uhd-s-pill-bg);
    backdrop-filter: blur(18px) saturate(160%);
    -webkit-backdrop-filter: blur(18px) saturate(160%);
  }
  /* 全宽彩虹边框播放钮（D2-02：录屏形态为彩虹边框胶囊）+ 右侧圆形收藏钮；
     2026-11-13 补齐玻璃：淡彩粉彩底 + 玻璃栈，不再关 backdrop */
  .md-actions .md-btn--play {
    flex: 1 1 auto;
    min-width: 0;
    border: 2px solid transparent;
    background:
      var(--uhd-rainbow-soft) padding-box,
      var(--uhd-rainbow-border-flow) border-box;
    background-size: auto, 200% 100%;
    color: var(--uhd-s-pill-active-text);
    box-shadow:
      inset 0 1px 0 rgba(255, 255, 255, 0.65),
      var(--uhd-s-shadow-pill);
    backdrop-filter: var(--uhd-glass-filter);
    -webkit-backdrop-filter: var(--uhd-glass-filter);
    white-space: normal;
    line-height: 1.25;
    text-align: center;
  }
  /* 手机深色版：黑毛玻璃底 + 白字 */
  .stream-scope[data-stream-theme="dark"] .md-actions .md-btn--play {
    background:
      linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box,
      var(--uhd-rainbow-border-flow) border-box;
    background-size: auto, 200% 100%;
    color: #ffffff;
    box-shadow:
      inset 0 1px 0 rgba(255, 255, 255, 0.22),
      var(--uhd-s-shadow-pill);
  }
  .md-btn--fav {
    flex: 0 0 48px;
    width: 48px;
    min-width: 48px;
    height: 48px;
    padding: 0;
    border-radius: 50%;
    font-size: 0;
    gap: 0;
  }
  /* 移除钮在吸顶条里与收藏钮同形：圆形图标钮（设施与桌面一致，只变排版） */
  .md-btn--remove {
    flex: 0 0 48px;
    width: 48px;
    min-width: 48px;
    height: 48px;
    padding: 0;
    border-radius: 50%;
    gap: 0;
  }
  /* 播放钮高度/字号收小（52→50、16→15） */
  .md-actions .md-btn--play {
    height: 50px;
    font-size: 15px;
  }
  .md-btn--remove-txt {
    display: none;
  }
  /* 状态标手机端恢复显示（FE-26）：设施与桌面一致（同色板同形态），
     只按小尺度收成小字胶囊并入动作行，不再整件隐藏 */
  .md-btn--state {
    height: 34px;
    padding: 0 12px;
    border-radius: 999px;
    font-size: 12.5px;
  }
  /* 集数条全幅出血量随页边距 20px 同步（基规则按 24px 写） */
  .md-episodes :deep(.ep-strip) {
    width: calc(100% + 40px);
    margin-left: -20px;
  }
  .md-skel__band {
    height: 120px;
  }
  .md-skel__poster {
    display: none;
  }
}
</style>
