<!-- 播放前弹层（批 3 录屏形态）：底部升起的大圆角弹层——
     顶部剧照头图 + 选集播放小字 + 标题（仅手机；桌面存档弹窗无标题头）；
     影片版本单选卡（单选圆点 + 规格 chip + 码率·大小·时长 + 复制/展开
     圆钮）；外挂字幕卡（语言·格式 + 文件名，点播前可选，选中带进网页
     播放器）；播放线路行（账号卡 realms + 专属线路，选哪条本次播放的
     EA 请求就走哪条入口）；玻璃粉彩填充「开始播放 · 版本大标题」
     （网页播放器主入口，线路未就绪时钮内显示「正在准备线路」）；
     第三方播放器以「用播放器打开」行列保留（既有业务入口，点行即打开）。
     D3-05 旧裁定（省略播放线路段）已被 2026-11-13 用户亲点推翻：
     线路数据与测速页同源（账号卡 realms + 专属线路），选线真生效
     只限网页播放路径；第三方导入 scheme 的服务器地址由后端账号卡
     按当前服生成，弹层选线带不过去（导入是一次性账号设置）。
     口径：未绑盘先出绑盘引导（门禁不变）；第三方播放器走现行导入
     scheme（pick）；网页播放走播放器单契约（PR #85）：路由名
     web-player（/play/:id），params.id=起播目标 id（剧集为集 id）、
     query.ms=选定 MediaSource Id（可选）、query.at=起始秒（可选，
     本弹层不传、播放器用续看位置），路由未落地时点按给诚实提示。 -->
<template>
  <Teleport to="body">
    <div v-if="open" class="psp" @keydown.esc.prevent="close">
      <button type="button" class="psp-mask" aria-label="关闭" @click="close"></button>
      <div
        ref="panelEl"
        class="psp-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="psp-title"
        tabindex="-1"
      >
        <!-- 关闭钮挂在弹层本体上（桌面无头图时也必须在） -->
        <button type="button" class="psp-x" aria-label="关闭" @click="close">
          <X :size="17" />
        </button>
        <!-- 头图区（仅手机显示） -->
        <div class="psp-hero">
          <img v-if="heroImg" :src="heroImg" alt="" class="psp-hero__img" />
          <div class="psp-hero__fade" aria-hidden="true"></div>
          <div class="psp-hero__meta">
            <span class="psp-hero__label">{{ heroLabel }}</span>
            <div id="psp-title" class="psp-hero__title">{{ heroTitle }}</div>
            <div v-if="heroSub" class="psp-hero__sub">{{ heroSub }}</div>
          </div>
        </div>

        <div class="psp-body">
          <PlayStatusBar v-if="!needsBinding && itemId !== null && itemId !== undefined" :item-id="itemId" />

          <div v-if="loading" class="psp-loading" role="status">
            <span class="psp-spinner" aria-hidden="true"></span> 正在获取资源和线路…
          </div>

          <BindDriveGuide v-else-if="needsBinding" :title="title" @navigate="close" />

          <template v-else>
            <!-- 影片版本（单选） -->
            <div class="psp-sec-head">
              <span>影片版本</span>
              <span v-if="versions.length" class="psp-sec-count">{{ versions.length }} 个可选版本</span>
            </div>
            <div v-if="versions.length" class="psp-vers">
              <div
                v-for="v in versions"
                :key="v.id"
                class="psp-ver"
                :class="{ 'psp-ver--on': v.id === selectedVersionId }"
                role="radio"
                :aria-checked="v.id === selectedVersionId"
                tabindex="0"
                @click="selectedVersionId = v.id"
                @keydown.enter.prevent="selectedVersionId = v.id"
              >
                <span class="psp-ver__radio" aria-hidden="true"></span>
                <div class="psp-ver__main">
                  <div class="psp-ver__name">{{ v.title }}</div>
                  <div v-if="v.chips.length" class="psp-ver__chips">
                    <span v-for="c in v.chips" :key="c" class="psp-ver__chip">{{ c }}</span>
                  </div>
                  <div v-if="v.meta" class="psp-ver__meta">{{ v.meta }}</div>
                  <div v-if="expandedId === v.id && v.streams.length" class="psp-ver__streams">
                    <div v-for="(s, i) in v.streams" :key="i">{{ s }}</div>
                  </div>
                </div>
                <div class="psp-ver__tools">
                  <button type="button" class="psp-tool" aria-label="复制片名去播放器搜" title="复制片名" @click.stop="copyTitle">
                    <Copy :size="15" />
                  </button>
                  <button
                    v-if="v.streams.length"
                    type="button"
                    class="psp-tool"
                    :aria-label="expandedId === v.id ? '收起轨道详情' : '展开轨道详情'"
                    @click.stop="expandedId = expandedId === v.id ? '' : v.id"
                  >
                    <ChevronDown :size="15" :style="{ transform: expandedId === v.id ? 'rotate(180deg)' : '' }" />
                  </button>
                </div>
              </div>
            </div>
            <p v-else class="psp-note">这个条目的版本信息没拉到，开始播放会用默认版本。</p>

            <!-- 外挂字幕（所选版本的外挂字幕轨；点选后带进网页播放器，
                 再点一次已选卡 = 本次不挂字幕。没有外挂字幕整段不渲染） -->
            <template v-if="externalSubs.length">
              <div class="psp-sec-head">
                <span>外挂字幕</span>
                <span class="psp-sec-count">{{ externalSubs.length }} 个字幕</span>
              </div>
              <div class="psp-subs">
                <button
                  v-for="s in externalSubs"
                  :key="s.index"
                  type="button"
                  class="psp-sub"
                  :class="{ 'psp-sub--on': selectedSubIndex === s.index }"
                  :aria-pressed="selectedSubIndex === s.index"
                  @click="toggleSub(s.index)"
                >
                  <span class="psp-sub__name">{{ s.label }}</span>
                  <span v-if="s.file" class="psp-sub__file">{{ s.file }}</span>
                </button>
              </div>
            </template>

            <!-- 播放线路（与测速页同源：账号卡 realms + 专属线路。
                 选哪条，本次网页播放的 EA 请求就走哪条入口；只有一条时
                 只展示不给选。清单加载中先占位显示「准备中」药丸；
                 加载完为空整段不渲染，播放走默认入口） -->
            <div v-if="lines.length || linesLoading" class="psp-line-sec">
              <div class="psp-line-row">
                <span class="psp-line-label">播放线路</span>
                <button
                  v-if="lines.length > 1"
                  type="button"
                  class="psp-line-pill"
                  :aria-expanded="linePickerOpen"
                  @click="linePickerOpen = !linePickerOpen"
                >
                  <span class="psp-line-pill__name">{{ selectedLine?.name || '默认线路' }}<template v-if="linesLoading"> · 准备中</template></span>
                  <span v-if="linesLoading" class="psp-spinner psp-spinner--sm" aria-hidden="true"></span>
                  <ChevronDown v-else :size="15" :style="{ transform: linePickerOpen ? 'rotate(180deg)' : '' }" />
                </button>
                <span v-else class="psp-line-pill psp-line-pill--static">
                  <span class="psp-line-pill__name"><template v-if="linesLoading && !selectedLine">准备中</template><template v-else>{{ selectedLine?.name || '默认线路' }}<template v-if="linesLoading"> · 准备中</template></template></span>
                  <span v-if="linesLoading" class="psp-spinner psp-spinner--sm" aria-hidden="true"></span>
                </span>
              </div>
              <div v-if="linePickerOpen && lines.length > 1" class="psp-line-list">
                <button
                  v-for="l in lines"
                  :key="l.key"
                  type="button"
                  class="psp-line-opt"
                  :class="{ 'psp-line-opt--on': l.url === selectedLineUrl }"
                  @click="chooseLine(l)"
                >
                  <span class="psp-line-opt__radio" aria-hidden="true"></span>
                  <span class="psp-line-opt__main">
                    <span class="psp-line-opt__name">{{ l.name }}</span>
                    <span class="psp-line-opt__host">{{ lineHost(l.url) }}</span>
                  </span>
                </button>
              </div>
            </div>

            <!-- 开始播放：玻璃粉彩填充全宽钮（网页播放器主入口，契约 web-player） -->
            <div class="psp-start-sec">
              <span class="psp-start-label">开始播放</span>
              <button type="button" class="psp-start" :class="{ 'psp-start--prep': startPreparing }" @click="start">
                <Play :size="17" fill="currentColor" />
                <template v-if="startPreparing">正在准备线路</template><template v-else>开始播放</template><template v-if="selectedVersion"> · {{ selectedVersion.title }}</template>
              </button>
              <p class="psp-tip">
                在网页里直接播放；个别版本浏览器播不了时，播放页会提示你换个版本或改用下面的播放器打开。
              </p>
            </div>

            <!-- 第三方播放器（既有业务入口）：点行即按现行 scheme 打开 -->
            <div v-if="schemes.length" class="psp-sec-head">
              <span>用播放器打开</span>
            </div>
            <div v-if="schemes.length" class="psp-players">
              <button
                v-for="s in schemes"
                :key="s.name"
                type="button"
                class="psp-player"
                @click="pick(s)"
              >
                <span class="psp-player__name">{{ s.name }}</span>
                <ChevronRight :size="16" />
              </button>
            </div>
            <p v-if="schemes.length" class="psp-tip">
              首播说明：没转存过的片子，用播放器首播会先转存到你自己的盘，不走共享池。
              播放器里转圈、报 409（转存还没完成）或 503（转存失败）时，关掉播放器回到这里再点一次就是重试，不用重绑。
            </p>
            <p v-if="schemes.length === 0" class="psp-note">
              还没有导入第三方播放器；要导入去
              <RouterLink to="/game/operator" @click="close">播放账号页</RouterLink>。
            </p>
          </template>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ChevronDown, ChevronRight, Copy, Play, X } from 'lucide-vue-next'
import BindDriveGuide from '@/components/panel/BindDriveGuide.vue'
import { useToast } from '@/composables/useToast'
import PlayStatusBar from '@/components/panel/PlayStatusBar.vue'
import { storageBindingApi } from '@/api/economy'
import {
  embyApi as embyProtocol,
  backdropUrl,
  formatDuration,
  ticksToSeconds,
  type EmbyItem,
} from '@/api/emby'
import {
  assignImportScheme,
  loadImportSchemes,
  type PlayerScheme,
} from '@/composables/usePlayerSchemes'
import {
  loadPlayLines,
  type PlayLine,
} from '@/composables/usePlayLines'

const open = defineModel<boolean>('open', { default: false })
const props = defineProps<{
  title?: string
  /** 条目 id（可选）：给了才拉版本/头图并展示起播状态条 */
  itemId?: string | number | null
}>()

const toast = useToast()
const router = useRouter()
const schemes = ref<PlayerScheme[]>([])
const loading = ref(false)
// 播放门禁引导：名下无 active 绑定时为 true，弹层只展示绑定引导，
// 不直接播放（播也会被服务端门禁拦下）。
const needsBinding = ref(false)
const panelEl = ref<HTMLElement | null>(null)
let jumpTimer = 0

/* ---------- 条目与版本（详情 DTO 口径，不新造接口） ---------- */
const itemDetail = ref<EmbyItem | null>(null)
/** 版本来源：电影/单集 = 条目本身；剧集 = 续看集（无续看取第一集）的详情 */
const versionSource = ref<EmbyItem | null>(null)
/** 实际起播目标 id（剧集时为集 id），web-player 契约用它 */
const playTargetId = ref('')
const epInfo = ref<{ season?: number | null; episode?: number | null; name?: string } | null>(null)

interface VersionCard {
  id: string
  name: string
  /** 大标题口径（分辨率 + 动态范围 + 编码，如「1080p SDR H264」）：
      版本卡标题与开始播放钮的版本名都用它，与友站同形态 */
  title: string
  chips: string[]
  meta: string
  streams: string[]
}

/** 动态范围标签：VideoRange 原值优先，判不出再从 DisplayTitle 里认，
    都没有按 SDR（与网页播放器 specHeadline 同口径） */
function rangeLabel(video?: { VideoRange?: string | null; DisplayTitle?: string } | null): string {
  const raw = (video?.VideoRange || '').trim()
  if (raw) {
    if (/dolby/i.test(raw)) return 'Dolby Vision'
    if (/hdr10\+/i.test(raw)) return 'HDR10+'
    if (/hdr10/i.test(raw)) return 'HDR10'
    if (/hlg/i.test(raw)) return 'HLG'
    if (/hdr/i.test(raw)) return 'HDR'
    return 'SDR'
  }
  const t = video?.DisplayTitle || ''
  for (const tok of ['Dolby Vision', 'HDR10+', 'HDR10', 'HLG', 'HDR']) {
    if (t.includes(tok)) return tok
  }
  return 'SDR'
}

function versionTitle(
  video: { Height?: number | null; VideoRange?: string | null; DisplayTitle?: string; Codec?: string } | undefined,
  fallback: string,
): string {
  if (!video) return fallback
  const parts = [
    resChip(video.Height ?? null),
    rangeLabel(video),
    video.Codec ? video.Codec.toUpperCase() : '',
  ].filter(Boolean)
  return parts.length ? parts.join(' ') : fallback
}

function resChip(height?: number | null): string {
  if (!height) return ''
  if (height >= 2000) return '4K'
  if (height >= 1000) return '1080p'
  if (height >= 700) return '720p'
  return `${height}p`
}
function sizeText(bytes?: number | null): string {
  if (!bytes) return ''
  const gb = bytes / 1_000_000_000
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1_000_000))} MB`
}

const versions = computed<VersionCard[]>(() => {
  const it = versionSource.value || itemDetail.value
  if (!it) return []
  if (it.Versions?.length) {
    return it.Versions.map((v): VersionCard => ({
      id: v.Id,
      name: v.VersionLabel || v.Name || '默认版本',
      title: v.VersionLabel || v.Name || '默认版本',
      chips: [resChip(v.Height), v.Container ? v.Container.toUpperCase() : ''].filter(Boolean),
      meta: sizeText(v.SizeBytes),
      streams: [],
    }))
  }
  return (it.MediaSources || []).map((s, i) => {
    const video = (s.MediaStreams || []).find((x) => x.Type === 'Video')
    const chips = [
      resChip(video?.Height ?? null),
      video?.VideoRange || '',
      video?.Codec ? video.Codec.toUpperCase() : '',
      s.Container ? s.Container.toUpperCase() : '',
    ].filter(Boolean)
    const metaParts: string[] = []
    if (s.Bitrate) metaParts.push(`${(s.Bitrate / 1_000_000).toFixed(1)} Mbps`)
    if (s.Size) metaParts.push(sizeText(s.Size))
    const ticks = s.RunTimeTicks || it.RunTimeTicks
    if (ticks) metaParts.push(formatDuration(ticksToSeconds(ticks)))
    const name = s.Name || video?.DisplayTitle || `版本 ${i + 1}`
    return {
      id: s.Id,
      name,
      title: versionTitle(video, name),
      chips: [...new Set(chips)],
      meta: metaParts.join(' · '),
      streams: (s.MediaStreams || []).map((x) => x.DisplayTitle || `${x.Type} ${x.Codec || ''}`).filter(Boolean),
    }
  })
})

const selectedVersionId = ref('')
const selectedVersion = computed(() => versions.value.find((v) => v.id === selectedVersionId.value) || null)
const expandedId = ref('')

/* ---------- 外挂字幕（所选版本的外挂字幕轨，数据来自条目详情 DTO
   的 MediaStreams：扫描器把外挂字幕登记为 IsExternal 字幕轨，
   DisplayTitle 即字幕文件名、Language 为 ISO 语言码、Codec 为格式） ---------- */
interface SubCard {
  index: number
  label: string
  file: string
}

const SUB_LANG_LABELS: Record<string, string> = {
  eng: '英文', en: '英文',
  jpn: '日文', ja: '日文',
  kor: '韩文', ko: '韩文',
  fra: '法文', fre: '法文', fr: '法文',
  deu: '德文', ger: '德文', de: '德文',
  spa: '西班牙文', es: '西班牙文',
  rus: '俄文', ru: '俄文',
  ita: '意大利文', it: '意大利文',
  por: '葡萄牙文', pt: '葡萄牙文',
  tha: '泰文', th: '泰文',
  vie: '越南文', vi: '越南文',
  ind: '印尼文', id: '印尼文',
  ara: '阿拉伯文', ar: '阿拉伯文',
}

function subLangLabel(lang?: string | null, file = ''): string {
  const code = (lang || '').trim().toLowerCase()
  if (code === 'chi' || code === 'zho' || code === 'zh') {
    // 中文再按文件名里的简/繁标记细分（标记来自文件名本身，不猜）
    if (/cht|zh-tw|zh-hk|zh-hant|big5|繁/.test(file)) return '繁中'
    if (/chs|zh-cn|zh-hans|简/.test(file)) return '简中'
    return '中文'
  }
  if (SUB_LANG_LABELS[code]) return SUB_LANG_LABELS[code]
  return code ? code.toUpperCase() : '外挂'
}

/** 当前所选版本对应的 MediaSource（字幕挂在源上）。
    多版本（Versions）形态下版本是独立条目行，只有选中版本恰是本条目
    主源时才拿得到它的字幕，拿不到就不渲染字幕区，不拿别人的凑数。 */
const subsSource = computed(() => {
  const it = versionSource.value || itemDetail.value
  if (!it) return null
  const list = it.MediaSources || []
  if (!list.length) return null
  const hit = list.find((s) => s.Id === selectedVersionId.value) || null
  if (it.Versions?.length) return hit
  return hit || list[0]
})

const externalSubs = computed<SubCard[]>(() => {
  const src = subsSource.value
  if (!src) return []
  return (src.MediaStreams || [])
    .filter((s) => s.Type === 'Subtitle' && s.IsExternal)
    .map((s) => {
      const file = s.DisplayTitle || ''
      const codec = (s.Codec || file.split('.').pop() || '').toLowerCase()
      return {
        index: s.Index,
        label: `${subLangLabel(s.Language, file)}${codec ? ` · ${codec}` : ''}`,
        file,
      }
    })
})

/** 选中的外挂字幕流 Index；null = 本次不挂外挂字幕 */
const selectedSubIndex = ref<number | null>(null)

function syncSubSelection() {
  const subs = externalSubs.value
  if (!subs.length) {
    selectedSubIndex.value = null
    return
  }
  // 默认选中：源的默认字幕轨（DefaultSubtitleStreamIndex）若是外挂轨，
  // 否则第一条外挂轨里被标 IsDefault 的；都没有就不预选（保持现状：
  // 播放器仍会按服务端默认轨起播，弹层不强加选择）
  const src = subsSource.value
  const dft = src?.DefaultSubtitleStreamIndex
  if (dft != null && subs.some((s) => s.index === dft)) {
    selectedSubIndex.value = dft
    return
  }
  const marked = (src?.MediaStreams || []).find(
    (s) => s.Type === 'Subtitle' && s.IsExternal && s.IsDefault,
  )
  selectedSubIndex.value = marked ? marked.Index : null
}

function toggleSub(index: number) {
  selectedSubIndex.value = selectedSubIndex.value === index ? null : index
}

watch(selectedVersionId, () => syncSubSelection())

/* ---------- 播放线路（与测速页同源；选线只影响网页播放路径：
   起播时把所选入口带给播放器，播放器校验后本次 EA 请求全走该入口） ---------- */
const lines = ref<PlayLine[]>([])
const linesLoading = ref(false)
const linesDefaultUrl = ref('')
const selectedLineUrl = ref('')
const linePickerOpen = ref(false)
const selectedLine = computed(() => lines.value.find((l) => l.url === selectedLineUrl.value) || null)
/** 开始播放钮的准备态：线路清单还没定下来时钮内显示「正在准备线路」 */
const startPreparing = computed(() => linesLoading.value)

async function loadLines() {
  linesLoading.value = true
  linePickerOpen.value = false
  try {
    const res = await loadPlayLines()
    lines.value = res.lines
    linesDefaultUrl.value = res.defaultUrl
    const stillValid = selectedLineUrl.value && res.lines.some((l) => l.url === selectedLineUrl.value)
    if (!stillValid) {
      selectedLineUrl.value =
        (res.defaultUrl && res.lines.some((l) => l.url === res.defaultUrl)
          ? res.defaultUrl
          : res.lines[0]?.url) || ''
    }
  } catch {
    lines.value = []
  } finally {
    linesLoading.value = false
  }
}

function chooseLine(l: PlayLine) {
  selectedLineUrl.value = l.url
  linePickerOpen.value = false
}

function lineHost(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

/* ---------- 头图区文案 ---------- */
const heroImg = computed(() => (itemDetail.value ? backdropUrl(itemDetail.value, 1280) : ''))
const heroTitle = computed(() => props.title || itemDetail.value?.Name || '播放')
const heroLabel = computed(() => {
  const t = itemDetail.value?.Type
  return t === 'Episode' || t === 'Series' ? '选集播放' : '播放'
})
const heroSub = computed(() => {
  const it = itemDetail.value
  if (!it) return ''
  if (it.Type === 'Episode') {
    const s = it.ParentIndexNumber
    const e = it.IndexNumber
    const series = (it as { SeriesName?: string }).SeriesName || props.title || ''
    return [series, s != null && e != null ? `第 ${s} 季第 ${e} 集` : ''].filter(Boolean).join(' · ')
  }
  if (it.Type === 'Series' && epInfo.value) {
    const { season: s, episode: e, name } = epInfo.value
    return [s != null && e != null ? `第 ${s} 季第 ${e} 集` : '', name || ''].filter(Boolean).join(' · ')
  }
  const parts: string[] = []
  if (it.ProductionYear) parts.push(String(it.ProductionYear))
  if (it.Type === 'Series' && it.ChildCount) parts.push(`${it.ChildCount} 季`)
  if (it.Genres?.length) parts.push(it.Genres.slice(0, 2).join(' / '))
  return parts.join(' · ')
})

watch(open, (now) => {
  if (!now) return
  expandedId.value = ''
  void refresh()
  void nextTick(() => panelEl.value?.focus())
})

async function refresh() {
  loading.value = true
  // 线路清单与条目并行拉：开始播放钮的「正在准备线路」态以它落定为准
  void loadLines()
  try {
    const id = props.itemId
    const [schemesResult, bindingsResult, itemResult] = await Promise.allSettled([
      loadImportSchemes(),
      storageBindingApi.list(),
      id !== null && id !== undefined && String(id) !== ''
        ? embyProtocol.getItem(String(id))
        : Promise.resolve(null),
    ])
    if (schemesResult.status === 'fulfilled') schemes.value = schemesResult.value
    // 绑定列表拉取失败时保持旧行为（服务端门禁仍会拦截），引导只是尽力而为。
    if (bindingsResult.status === 'fulfilled') {
      const bindings = bindingsResult.value?.bindings ?? []
      needsBinding.value = !bindings.some((b) => b.status === 'active')
    } else {
      needsBinding.value = false
    }
    itemDetail.value = itemResult.status === 'fulfilled' ? itemResult.value : null
    versionSource.value = itemDetail.value
    playTargetId.value = id !== null && id !== undefined ? String(id) : ''
    epInfo.value = null
    // 剧集本身没有版本：版本挂在集上，取续看集（无续看取第一集）
    if (itemDetail.value?.Type === 'Series') {
      try {
        const eps = await embyProtocol.getEpisodes(itemDetail.value.Id)
        const ep =
          eps.find((e) => (e.UserData?.PlaybackPositionTicks ?? 0) > 0) ||
          eps.find((e) => !e.UserData?.Played && (e.UserData?.PlayedPercentage ?? 0) > 0) ||
          eps.find((e) => !e.UserData?.Played) ||
          eps[0]
        if (ep) {
          epInfo.value = {
            season: ep.ParentIndexNumber,
            episode: ep.IndexNumber,
            name: ep.Name,
          }
          playTargetId.value = ep.Id
          const epDetail = await embyProtocol.getItem(ep.Id)
          if (epDetail) versionSource.value = epDetail
        }
      } catch { /* 集列表拉不到就只给线路，版本区留空 */ }
    }
    const primary = versionSource.value?.Versions?.find((v) => v.IsPrimary)
    selectedVersionId.value =
      primary?.Id || versions.value[0]?.id || ''
    syncSubSelection()
  } finally {
    loading.value = false
  }
}

function close() {
  window.clearTimeout(jumpTimer)
  open.value = false
}

async function copyTitle() {
  const t = (props.title || itemDetail.value?.Name || '').trim()
  if (!t) return
  try {
    await navigator.clipboard.writeText(t)
    toast.success('片名已复制，去播放器里粘贴搜索')
  } catch {
    toast.info('复制失败，请手动复制上面的片名')
  }
}

async function pick(s: PlayerScheme) {
  // 跳转前先把提示留在页面上：没装 App 时 scheme 不会离开本页
  toast.info(`正在打开${s.name}。没装的话去播放账号页导入`)
  await nextTick()
  await new Promise<void>((resolve) => {
    jumpTimer = window.setTimeout(resolve, 600)
  })
  if (!open.value) return
  if (!assignImportScheme(s.url)) toast.info('没装的话去播放账号页导入')
}

/**
 * 开始播放（网页播放器主入口）：走播放器单契约 —— 路由名
 * web-player、params.id=起播目标 id（剧集为集 id）、query.ms=
 * 选定版本 MediaSource Id（PR #85）；query.line=弹层选定的播放
 * 线路入口（与默认线路相同则不带，播放器侧会再按账号下发清单
 * 校验，手改不认）；query.sub=选定的外挂字幕流 Index（取消
 * 字幕时为 off；该版本没有外挂字幕时不带，播放器保持默认轨
 * 口径）。路由未落地时不假装能播，给诚实提示。第三方播放器
 * 走下方播放器行 pick（导入 scheme 的服务器地址由后端按账号
 * 当前服生成，弹层选线带不过去，保持现行口径）。
 */
async function start() {
  const id = playTargetId.value || props.itemId
  if (id === null || id === undefined || String(id) === '') {
    toast.info('这个入口没有带条目，暂时只能用播放器打开')
    return
  }
  if (linesLoading.value) {
    toast.info('线路还在准备，稍等一下')
    return
  }
  if (router.hasRoute('web-player')) {
    const query: Record<string, string> = {}
    if (selectedVersionId.value) query.ms = selectedVersionId.value
    if (selectedLineUrl.value && selectedLineUrl.value !== linesDefaultUrl.value) {
      query.line = selectedLineUrl.value
    }
    if (externalSubs.value.length) {
      query.sub = selectedSubIndex.value != null ? String(selectedSubIndex.value) : 'off'
    }
    close()
    void router.push({
      name: 'web-player',
      params: { id: String(id) },
      query,
    })
  } else {
    toast.info('网页播放器马上就来；先用下面的播放器打开')
  }
}
</script>

<style scoped>
/* 批 3 播放前弹层（录屏形态）：手机 = 悬浮大卡（左右/底部留 12、圆角 28）；
   桌面 = 居中 610 宽、圆角 24（存档第 7 节）。头图 + 版本单选卡 +
   外挂字幕卡 + 播放线路行 + 玻璃粉彩开始播放钮 + 第三方播放器行列。
   Teleport 到 body：浅色走 --uhd-l-* :root 令牌；片库面深色态由
   StreamShell 在 body 打 .uhd-stream-dark 标记桥接深色值。 */
.psp {
  position: fixed;
  inset: 0;
  z-index: 90;
}
/* 弹层 Teleport 在 .stream-scope 之外，全局 mobile.css 的 min-height:44px
   触控规则会撑高关闭钮等小控件——在弹层自己的作用域里中和（同 StreamShell 口径） */
.psp button,
.psp a {
  min-height: 0;
}
.psp-mask {
  position: absolute;
  inset: 0;
  border: 0;
  padding: 0;
  background: rgba(10, 10, 12, 0.45);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  cursor: pointer;
}
.psp-sheet {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: calc(env(safe-area-inset-bottom) + 12px);
  background: var(--uhd-l-surface, #ffffff);
  color: var(--uhd-l-text, #111111);
  border-radius: 28px;
  max-height: 88dvh;
  overflow: auto;
  font-family: var(--uhd-font-sans, sans-serif);
  box-shadow: var(--uhd-shadow-modal, 0 24px 64px -24px rgba(0, 0, 0, 0.35));
}
/* 头图区 */
.psp-hero {
  position: relative;
  height: 196px;
  background: linear-gradient(120deg, #3a3f4a, #191b20);
  overflow: hidden;
}
.psp-hero__img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.psp-hero__fade {
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, rgba(0, 0, 0, 0.05) 40%, rgba(0, 0, 0, 0.55) 100%);
}
.psp-x {
  position: absolute;
  top: 14px;
  right: 14px;
  z-index: 2;
  width: 38px;
  height: 38px;
  border: none;
  border-radius: 50%;
  background: rgba(20, 20, 22, 0.5);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  color: #ffffff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.psp-hero__meta {
  position: absolute;
  left: 20px;
  right: 20px;
  bottom: 16px;
  z-index: 1;
  color: #ffffff;
}
.psp-hero__label {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.85);
}
.psp-hero__title {
  margin-top: 4px;
  font-size: 24px;
  font-weight: 700;
  line-height: 1.25;
}
.psp-hero__sub {
  margin-top: 4px;
  font-size: 13.5px;
  color: rgba(255, 255, 255, 0.78);
}
.psp-body {
  padding: 18px 20px 22px;
}
/* 加载态：正在获取资源和线路 */
.psp-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 26px 0;
  font-size: 14.5px;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-spinner {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 2px solid var(--uhd-l-border, #e5e5e5);
  border-top-color: var(--uhd-l-text, #111111);
  animation: uhd-spin 0.8s linear infinite;
}
/* 版本区 */
.psp-sec-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  font-size: 15px;
  font-weight: 700;
  margin-top: 6px;
}
.psp-sec-count {
  font-size: 13px;
  font-weight: 400;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-vers {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 12px;
}
.psp-ver {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 14px 16px;
  border-radius: 16px;
  border: 1.5px solid var(--uhd-l-border, #e5e5e5);
  background: var(--uhd-l-input-bg, #f5f5f5);
  cursor: pointer;
}
.psp-ver--on {
  border-color: var(--uhd-l-text, #111111);
  background: var(--uhd-l-surface, #ffffff);
}
.psp-ver__radio {
  flex: none;
  width: 20px;
  height: 20px;
  margin-top: 2px;
  border-radius: 50%;
  border: 2px solid #b6bcc4;
  position: relative;
}
.psp-ver--on .psp-ver__radio {
  border-color: var(--uhd-l-text, #111111);
}
.psp-ver--on .psp-ver__radio::after {
  content: '';
  position: absolute;
  inset: 3px;
  border-radius: 50%;
  background: var(--uhd-l-text, #111111);
}
.psp-ver__main {
  flex: 1 1 auto;
  min-width: 0;
}
.psp-ver__name {
  font-size: 15.5px;
  font-weight: 700;
}
.psp-ver__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 7px;
}
.psp-ver__chip {
  font-size: 11.5px;
  line-height: 1;
  padding: 4px 8px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.07);
  color: var(--uhd-l-text, #111111);
}
.psp-ver__meta {
  margin-top: 7px;
  font-size: 13px;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-ver__streams {
  margin-top: 8px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-ver__tools {
  flex: none;
  display: flex;
  gap: 8px;
}
.psp-tool {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 1px solid var(--uhd-l-border, #e5e5e5);
  background: var(--uhd-l-surface, #ffffff);
  color: var(--uhd-l-text-2, #6b7280);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  padding: 0;
}
.psp-note {
  margin: 12px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-note a {
  color: var(--uhd-l-text, #111111);
  font-weight: 700;
}
.psp-tip {
  margin: 14px 0 0;
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--uhd-l-text-2, #6b7280);
}
/* 第三方播放器行列（既有业务入口，点行即打开） */
.psp-players {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 12px;
}
.psp-player {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  min-height: 52px;
  padding: 10px 16px;
  border-radius: 16px;
  border: 1.5px solid var(--uhd-l-border, #e5e5e5);
  background: var(--uhd-l-surface, #ffffff);
  color: var(--uhd-l-text, #111111);
  font-size: 15px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  text-align: left;
}
.psp-player:active {
  transform: scale(0.99);
}
/* 开始播放 */
.psp-start-sec {
  margin-top: 20px;
}
.psp-start-label {
  font-size: 13px;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-start {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  height: 52px;
  margin-top: 8px;
  /* 友站形态（2026-11-13 用户亲点）：玻璃粉彩填充＋弱化彩虹边——
     与全站主按钮 .btn-primary 浅色配方同一套（粉彩底 padding-box +
     彩虹 border-box + 玻璃栈：blur/saturate + 内高光 + 柔影） */
  border: 1.5px solid transparent;
  border-radius: 26px;
  background:
    var(--uhd-rainbow-soft) padding-box,
    var(--uhd-rainbow) border-box;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.65),
    var(--uhd-shadow-card-l);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
  color: var(--uhd-l-text, #111111);
  font-size: 16px;
  font-weight: 700;
  font-family: inherit;
  cursor: pointer;
}
.psp-start:active {
  transform: scale(0.99);
}
/* 准备态（线路未落定）：形态不变，只换钮内文案，指针示意等待 */
.psp-start--prep {
  cursor: progress;
}
/* 外挂字幕卡 */
.psp-subs {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.psp-sub {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  width: 100%;
  padding: 12px 16px;
  border: 1.5px solid var(--uhd-l-border, #e5e5e5);
  border-radius: 16px;
  background: var(--uhd-l-surface, #ffffff);
  color: var(--uhd-l-text, #111111);
  font-family: inherit;
  cursor: pointer;
  text-align: left;
}
.psp-sub--on {
  border-color: var(--uhd-l-text, #111111);
}
.psp-sub__name {
  font-size: 15px;
  font-weight: 700;
}
.psp-sub__file {
  max-width: 100%;
  font-size: 12.5px;
  color: var(--uhd-l-text-2, #6b7280);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 播放线路 */
.psp-line-sec {
  margin-top: 20px;
}
.psp-line-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.psp-line-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-line-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-width: 46%;
  max-width: 68%;
  padding: 10px 16px;
  border: 1.5px solid var(--uhd-l-border, #e5e5e5);
  border-radius: 999px;
  background: var(--uhd-l-input-bg, #f5f5f5);
  color: var(--uhd-l-text, #111111);
  font-size: 14.5px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
}
.psp-line-pill--static {
  cursor: default;
}
.psp-line-pill__name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.psp-spinner--sm {
  width: 14px;
  height: 14px;
  border-width: 2px;
  flex: none;
}
.psp-line-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
}
.psp-line-opt {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 12px 16px;
  border: 1.5px solid var(--uhd-l-border, #e5e5e5);
  border-radius: 16px;
  background: var(--uhd-l-surface, #ffffff);
  color: var(--uhd-l-text, #111111);
  font-family: inherit;
  cursor: pointer;
  text-align: left;
}
.psp-line-opt--on {
  border-color: var(--uhd-l-text, #111111);
}
.psp-line-opt__radio {
  flex: none;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 2px solid #d1d5db;
  position: relative;
}
.psp-line-opt--on .psp-line-opt__radio {
  border-color: var(--uhd-l-text, #111111);
}
.psp-line-opt--on .psp-line-opt__radio::after {
  content: '';
  position: absolute;
  inset: 3px;
  border-radius: 50%;
  background: var(--uhd-l-text, #111111);
}
.psp-line-opt__main {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
.psp-line-opt__name {
  font-size: 15px;
  font-weight: 600;
}
.psp-line-opt__host {
  font-size: 12.5px;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-sheet:focus-visible,
.psp-x:focus-visible,
.psp-tool:focus-visible,
.psp-player:focus-visible,
.psp-sub:focus-visible,
.psp-line-pill:focus-visible,
.psp-line-opt:focus-visible,
.psp-start:focus-visible {
  outline: 2px solid var(--uhd-l-link, #2563eb);
  outline-offset: 2px;
}
@media (min-width: 768px) {
  .psp-sheet {
    left: 50%;
    right: auto;
    bottom: auto;
    top: 50%;
    transform: translate(-50%, -50%);
    width: min(610px, calc(100vw - 48px));
    border-radius: 24px;
  }
  /* D3-05：原站桌面弹窗无标题头（存档 610 弹窗只有版本/线路/开始播放），
     剧照头图只保留在手机底部弹层（录屏形态） */
  .psp-hero {
    display: none;
  }
  /* 关闭钮浮在弹层右上，版块头右侧计数给它让位 */
  .psp-sec-head {
    padding-right: 44px;
  }
  .psp-body {
    padding: 24px 28px 26px;
  }
}
</style>

<style>
/* 片库面深色态（body 标记桥接） */
body.uhd-stream-dark .psp-sheet {
  background: #1c1c1c;
  color: #ffffff;
}
body.uhd-stream-dark .psp-sec-count,
body.uhd-stream-dark .psp-ver__meta,
body.uhd-stream-dark .psp-ver__streams,
body.uhd-stream-dark .psp-note,
body.uhd-stream-dark .psp-tip,
body.uhd-stream-dark .psp-loading,
body.uhd-stream-dark .psp-start-label {
  color: #9ca3af;
}
body.uhd-stream-dark .psp-ver {
  background: #222222;
  border-color: rgba(255, 255, 255, 0.14);
  color: #ffffff;
}
body.uhd-stream-dark .psp-ver--on {
  border-color: #ffffff;
}
body.uhd-stream-dark .psp-ver__radio {
  border-color: rgba(255, 255, 255, 0.4);
}
body.uhd-stream-dark .psp-ver--on .psp-ver__radio {
  border-color: #ffffff;
}
body.uhd-stream-dark .psp-ver--on .psp-ver__radio::after {
  background: #ffffff;
}
body.uhd-stream-dark .psp-ver__chip {
  background: rgba(255, 255, 255, 0.12);
  color: #ffffff;
}
body.uhd-stream-dark .psp-tool {
  background: #262626;
  border-color: rgba(255, 255, 255, 0.14);
  color: #ffffff;
}
body.uhd-stream-dark .psp-player {
  background: #222222;
  border-color: rgba(255, 255, 255, 0.14);
  color: #ffffff;
}
/* 深色开始播放：深色玻璃填充＋弱化彩虹边（与 .btn-primary 深色配方同口径） */
body.uhd-stream-dark .psp-start {
  background:
    linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box,
    var(--uhd-rainbow) border-box;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.22),
    0 12px 28px -12px rgba(0, 0, 0, 0.55);
  color: #ffffff;
}
body.uhd-stream-dark .psp-sub,
body.uhd-stream-dark .psp-line-opt {
  background: #222222;
  border-color: rgba(255, 255, 255, 0.14);
  color: #ffffff;
}
body.uhd-stream-dark .psp-sub--on,
body.uhd-stream-dark .psp-line-opt--on {
  border-color: #ffffff;
}
body.uhd-stream-dark .psp-sub__file,
body.uhd-stream-dark .psp-line-opt__host,
body.uhd-stream-dark .psp-line-label {
  color: #9ca3af;
}
body.uhd-stream-dark .psp-line-pill {
  background: #262626;
  border-color: rgba(255, 255, 255, 0.14);
  color: #ffffff;
}
body.uhd-stream-dark .psp-line-opt__radio {
  border-color: rgba(255, 255, 255, 0.4);
}
body.uhd-stream-dark .psp-line-opt--on .psp-line-opt__radio {
  border-color: #ffffff;
}
body.uhd-stream-dark .psp-line-opt--on .psp-line-opt__radio::after {
  background: #ffffff;
}
body.uhd-stream-dark .psp-note a {
  color: #ffffff;
}
body.uhd-stream-dark .psp-spinner {
  border-color: rgba(255, 255, 255, 0.25);
  border-top-color: #ffffff;
}
@media (prefers-reduced-motion: reduce) {
  .psp-start:active { transform: none; }
}
</style>
