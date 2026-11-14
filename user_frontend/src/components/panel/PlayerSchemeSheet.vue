<!-- 播放前弹层（批 3 录屏形态）：底部升起的大圆角弹层——
     顶部剧照头图 + 选集播放小字 + 标题（仅手机；桌面存档弹窗无标题头）；
     影片版本单选卡（标题行在最上、第二行单选圆点 + 规格 chip、第三行
     码率·大小·时长，右侧复制/展开圆钮垂直居中）；外挂字幕卡（语言·
     格式 + 文件名，点播前可选，选中带进网页播放器）；播放线路行
     （账号卡 realms + 专属线路，选哪条本次播放的 EA 请求就走哪条
     入口）；玻璃粉彩填充「开始播放 · 版本大标题」（网页播放器主
     入口，线路未就绪时钮内显示「正在准备线路」；文案双色：「开始
     播放」粗体 + 版本名细体灰）。
     2026-11-14 用户亲点友站弹层形态追加对齐：弹层固定一屏不滚动——
     只有版本区内部滚动，字幕/线路/开始播放三行固定在底部；
     字幕压成一行胶囊 + 向上弹选单（#134 能力不删）；版本卡复制钮改
     复制该版本播放链接；展开态改两列信息网格 + 音轨行（音轨只展示：
     网页播放器无选音轨能力，不做假选择），展开时版本区自动滚动把
     展开内容带进视口；头图右下加眼睛钮（标记已看，PlayedItems 真
     接口）；开始播放下的说明文案按 58 条总账 7-10/7-12「多余」裁定
     撤除。「用播放器打开」第三方播放器行已按 2026-11-14 09:39 用户
     亲点删除（友站弹层没有这一行；该能力在详情页的入口保持不动）。
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
        <!-- 头图区（仅手机显示）：右下眼睛钮 = 标记已看/取消（Emby
             PlayedItems 真接口，与详情页移除记录同一口径；剧集时标记
             的是本次起播的那一集，与起播目标一致） -->
        <div class="psp-hero-wrap">
          <div class="psp-hero">
            <img v-if="heroImg" :src="heroImg" alt="" class="psp-hero__img" />
            <div class="psp-hero__fade" aria-hidden="true"></div>
            <div class="psp-hero__meta">
              <span class="psp-hero__label">{{ heroLabel }}</span>
              <div id="psp-title" class="psp-hero__title">{{ heroTitle }}</div>
              <div v-if="heroSub" class="psp-hero__sub">{{ heroSub }}</div>
            </div>
          </div>
          <button
            v-if="canMarkPlayed"
            type="button"
            class="psp-eye"
            :class="{ 'psp-eye--on': playedState }"
            :aria-pressed="playedState"
            :aria-label="playedState ? '取消已看标记' : '标记为已看'"
            :disabled="playedBusy"
            @click="togglePlayed"
          >
            <EyeOff v-if="playedState" :size="19" />
            <Eye v-else :size="19" />
          </button>
        </div>

        <div class="psp-body">
          <!-- 滚动区：状态条 + 版本卡（版本多/展开时只许这里内部滚动，
               弹层本体与底部固定区不动——2026-11-14 用户亲点「固定的」） -->
          <div ref="scrollEl" class="psp-scroll">
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
                  <!-- 卡内三行（友站顺序）：第一行标题独占（左缘与胶囊列
                       对齐）、第二行单选圆点 + 胶囊、第三行码率信息；
                       右侧工具列跨三行垂直居中（网格定位见样式） -->
                  <div class="psp-ver__name">{{ v.title }}</div>
                  <span class="psp-ver__radio" aria-hidden="true"></span>
                  <div v-if="v.chips.length" class="psp-ver__chips">
                    <span v-for="c in v.chips" :key="c" class="psp-ver__chip">{{ c }}</span>
                  </div>
                  <div v-if="v.metaParts.length" class="psp-ver__meta"><span v-for="(mp, mi) in v.metaParts" :key="mi" class="psp-ver__metapart">{{ mp }}</span></div>
                  <div class="psp-ver__tools">
                    <button type="button" class="psp-tool" aria-label="复制该版本的播放链接" title="复制播放链接" @click.stop="copyVersionLink(v)">
                      <Copy :size="16" />
                    </button>
                    <button
                      v-if="v.details.length || v.audios.length"
                      type="button"
                      class="psp-tool"
                      :aria-label="expandedId === v.id ? '收起版本详情' : '展开版本详情'"
                      @click.stop="toggleExpand(v, $event)"
                    >
                      <ChevronDown :size="16" :style="{ transform: expandedId === v.id ? 'rotate(180deg)' : '' }" />
                    </button>
                  </div>
                  <!-- 展开态：两列信息网格（真实探测字段）+ 音轨行。
                       音轨只展示清单与默认标记、不做选择：网页播放器
                       没有选音轨能力，做成可选就是假造（详情见脚本注） -->
                  <div v-if="expandedId === v.id && (v.details.length || v.audios.length)" class="psp-ver__detail">
                      <div v-if="v.details.length" class="psp-grid">
                        <div v-for="d in v.details" :key="d.label" class="psp-grid__cell">
                          <span class="psp-grid__label">{{ d.label }}</span>
                          <span class="psp-grid__value">{{ d.value }}</span>
                        </div>
                      </div>
                      <div v-if="v.audios.length" class="psp-audio">
                        <button
                          type="button"
                          class="psp-audio__head"
                          :aria-expanded="audioOpenId === v.id"
                          @click.stop="toggleAudio(v, $event)"
                        >
                          <span class="psp-audio__name">音轨</span>
                          <span class="psp-audio__lang">{{ v.audioLang }}</span>
                          <span class="psp-audio__count">共 {{ v.audios.length }} 条</span>
                          <ChevronDown :size="15" :style="{ transform: audioOpenId === v.id ? 'rotate(180deg)' : '' }" />
                        </button>
                        <div v-if="audioOpenId === v.id" class="psp-audio__list">
                          <div v-for="(a, ai) in v.audios" :key="ai" class="psp-audio__row">
                            <span>{{ a.label }}</span>
                            <span v-if="a.detail" class="psp-audio__rowmeta">{{ a.detail }}</span>
                            <span v-if="a.isDefault" class="psp-audio__badge">默认</span>
                          </div>
                        </div>
                      </div>
                    </div>
                </div>
              </div>
              <p v-else class="psp-note">这个条目的版本信息没拉到，开始播放会用默认版本。</p>
            </template>
          </div>

          <!-- 固定区（不随版本区滚动）：字幕行 / 线路行 / 开始播放 -->
          <div v-if="!loading && !needsBinding" class="psp-fixed">
            <!-- 内嵌字幕（只展示：内嵌轨随流内嵌，弹层不选轨、不改取流
                 逻辑；图片类字幕后端回 415 的事不在本单内，行上不做可播
                 承诺。没有内嵌轨整行不渲染） -->
            <div v-if="embeddedSubs.length" class="psp-subrow">
              <span class="psp-subrow__label">内嵌字幕</span>
              <span class="psp-subrow__pill psp-subrow__pill--static">
                <span class="psp-subrow__name">{{ embeddedSubs.map((s) => s.label).join(' / ') }}</span>
                <span class="psp-subrow__count">{{ embeddedSubs.length }} 个</span>
              </span>
            </div>

            <!-- 外挂字幕（#134 能力保留，压成一行）：胶囊显示当前选择，
                 点开向上弹清单选轨；选中带进网页播放器，再选「不挂
                 字幕」= 本次不挂。没有外挂字幕整行不渲染 -->
            <div v-if="externalSubs.length" class="psp-subrow">
              <span class="psp-subrow__label">外挂字幕</span>
              <button
                type="button"
                class="psp-subrow__pill"
                :aria-expanded="subPickerOpen"
                @click="subPickerOpen = !subPickerOpen"
              >
                <span class="psp-subrow__name">{{ selectedSub ? selectedSub.label : '不挂字幕' }}</span>
                <span class="psp-subrow__count">{{ externalSubs.length }} 个</span>
                <ChevronDown :size="15" :style="{ transform: subPickerOpen ? 'rotate(180deg)' : '' }" />
              </button>
              <div v-if="subPickerOpen" class="psp-pop">
                <button
                  type="button"
                  class="psp-pop__opt"
                  :class="{ 'psp-pop__opt--on': selectedSubIndex === null }"
                  @click="chooseSub(null)"
                >
                  <span class="psp-pop__radio" aria-hidden="true"></span>
                  <span>不挂字幕</span>
                </button>
                <button
                  v-for="s in externalSubs"
                  :key="s.index"
                  type="button"
                  class="psp-pop__opt"
                  :class="{ 'psp-pop__opt--on': selectedSubIndex === s.index }"
                  @click="chooseSub(s.index)"
                >
                  <span class="psp-pop__radio" aria-hidden="true"></span>
                  <span class="psp-pop__main">
                    <span>{{ s.label }}</span>
                    <span v-if="s.file" class="psp-pop__file">{{ s.file }}</span>
                  </span>
                </button>
              </div>
            </div>

            <!-- 播放线路（与测速页同源：账号卡 realms + 专属线路。
                 选哪条，本次网页播放的 EA 请求就走哪条入口。胶囊文案
                 = 线路名 · 已就绪 + 下拉箭头（友站形态，一条线路时点开
                 选单里只有那一条）；清单本身就是账号下发的可选线路
                 （无权限的在上游已被滤掉），加载中显示「准备中」；
                 清单为空整行不渲染，播放走默认入口。选单向上弹层
                 展开，不撑动固定一屏布局） -->
            <div v-if="lines.length || linesLoading" class="psp-line-sec">
              <div class="psp-line-row">
                <span class="psp-line-label">播放线路</span>
                <!-- 胶囊恒为可点钮带下拉箭头（友站形态：名 · 已就绪 ∨）；
                     只有一条线路时点开选单里就那一条，口径与多条一致 -->
                <button
                  v-if="lines.length"
                  type="button"
                  class="psp-line-pill"
                  :aria-expanded="linePickerOpen"
                  @click="linePickerOpen = !linePickerOpen"
                >
                  <span class="psp-line-pill__name">{{ selectedLine?.name || '默认线路' }}<template v-if="linesLoading"> · 准备中</template><template v-else-if="selectedLine"> · 已就绪</template></span>
                  <span v-if="linesLoading" class="psp-spinner psp-spinner--sm" aria-hidden="true"></span>
                  <ChevronDown v-else :size="15" :style="{ transform: linePickerOpen ? 'rotate(180deg)' : '' }" />
                </button>
                <span v-else class="psp-line-pill psp-line-pill--static">
                  <span class="psp-line-pill__name"><template v-if="linesLoading && !selectedLine">准备中</template><template v-else>{{ selectedLine?.name || '默认线路' }}<template v-if="linesLoading"> · 准备中</template><template v-else-if="selectedLine"> · 已就绪</template></template></span>
                  <span v-if="linesLoading" class="psp-spinner psp-spinner--sm" aria-hidden="true"></span>
                </span>
              </div>
              <div v-if="linePickerOpen && lines.length" class="psp-pop psp-pop--line">
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

            <!-- 开始播放：玻璃粉彩填充全宽钮（网页播放器主入口，契约 web-player）；
                 说明文案段已按 58 条总账 7-10/7-12「多余」裁定撤掉。
                 文案双色（友站形态）：「开始播放」粗体 +「· 版本名」细体灰 -->
            <div class="psp-start-sec">
              <span class="psp-start-label">开始播放</span>
              <button type="button" class="psp-start" :class="{ 'psp-start--prep': startPreparing }" @click="start">
                <Play :size="17" fill="currentColor" />
                <span class="psp-start__main">{{ startPreparing ? '正在准备线路' : '开始播放' }}</span>
                <span v-if="!startPreparing && selectedVersion" class="psp-start__ver"> · {{ selectedVersion.title }}</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ChevronDown, Copy, Eye, EyeOff, Play, X } from 'lucide-vue-next'
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
const loading = ref(false)
// 播放门禁引导：名下无 active 绑定时为 true，弹层只展示绑定引导，
// 不直接播放（播也会被服务端门禁拦下）。
const needsBinding = ref(false)
const panelEl = ref<HTMLElement | null>(null)
/** 版本区滚动容器：展开版本卡/音轨时自动滚动，把展开内容带进视口 */
const scrollEl = ref<HTMLElement | null>(null)

/* ---------- 条目与版本（详情 DTO 口径，不新造接口） ---------- */
const itemDetail = ref<EmbyItem | null>(null)
/** 版本来源：电影/单集 = 条目本身；剧集 = 续看集（无续看取第一集）的详情 */
const versionSource = ref<EmbyItem | null>(null)
/** 实际起播目标 id（剧集时为集 id），web-player 契约用它 */
const playTargetId = ref('')
const epInfo = ref<{ season?: number | null; episode?: number | null; name?: string } | null>(null)

interface VersionDetail {
  label: string
  value: string
}
interface VersionAudio {
  /** 语言标注（轨本身没标语言时为「未标注语言」） */
  label: string
  /** 编码 · 声道布局等真实字段拼成的小字 */
  detail: string
  isDefault: boolean
}
interface VersionCard {
  id: string
  name: string
  /** 大标题口径（分辨率 + 动态范围 + 编码，如「1080p SDR H264」）：
      版本卡标题与开始播放钮的版本名都用它，与友站同形态 */
  title: string
  chips: string[]
  /** 码率 · 大小 · 时长（逐段渲染、段内不折行，折行只落在段与段之间） */
  metaParts: string[]
  /** 展开态两列网格：真实探测字段，缺的格不出现 */
  details: VersionDetail[]
  /** 展开态音轨行清单；空则不渲染音轨行 */
  audios: VersionAudio[]
  /** 音轨行摘要：默认音轨的语言标注 */
  audioLang: string
  /** 复制链接形态：source = MediaSource（/play/目标?ms=源id）；
      version = 独立条目行（/play/版本id） */
  kind: 'source' | 'version'
}

/** 帧率文案：整数不带小数，23.976 这类保留有效小数 */
function fmtFps(v?: number | null): string {
  if (!v || v <= 0) return ''
  const s = Number.isInteger(v) ? String(v) : v.toFixed(3).replace(/0+$/, '').replace(/\.$/, '')
  return `${s} fps`
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
  video: { Width?: number | null; Height?: number | null; VideoRange?: string | null; DisplayTitle?: string; Codec?: string } | undefined,
  fallback: string,
): string {
  if (!video) return fallback
  const parts = [
    resChip(video.Height ?? null, video.Width ?? null),
    rangeLabel(video),
    video.Codec ? video.Codec.toUpperCase() : '',
  ].filter(Boolean)
  return parts.length ? parts.join(' ') : fallback
}

/** 分辨率档（与网页播放器 resolutionLabel 同口径：宽或高任一达标即算，
    3840×1608 这类宽银幕 4K 不能只看高判成 1080p） */
function resChip(height?: number | null, width?: number | null): string {
  const h = height ?? 0
  const w = width ?? 0
  if (!h && !w) return ''
  if (h >= 2000 || w >= 3800) return '4K'
  if (h >= 1000 || w >= 1900) return '1080p'
  if (h >= 700 || w >= 1200) return '720p'
  return h ? `${h}p` : `${w}p`
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
    // 多版本形态：版本是独立条目行，只有宽高/容器/大小是真字段，
    // 展开网格就只给这几格，不拿主源的轨道冒充它的。
    return it.Versions.map((v): VersionCard => {
      const details: VersionDetail[] = []
      if (v.Width && v.Height) details.push({ label: '分辨率', value: `${v.Width} × ${v.Height}` })
      if (v.Container) details.push({ label: '容器', value: v.Container.toUpperCase() })
      if (v.SizeBytes) details.push({ label: '文件大小', value: sizeText(v.SizeBytes) })
      return {
        id: v.Id,
        name: v.VersionLabel || v.Name || '默认版本',
        title: v.VersionLabel || v.Name || '默认版本',
        chips: [resChip(v.Height, v.Width).toUpperCase()].filter(Boolean),
        metaParts: [sizeText(v.SizeBytes)].filter(Boolean),
        details,
        audios: [],
        audioLang: '',
        kind: 'version',
      }
    })
  }
  return (it.MediaSources || []).map((s, i) => {
    const streams = s.MediaStreams || []
    const video = streams.find((x) => x.Type === 'Video')
    // 规格小胶囊（友站口径）：分辨率 / 动态范围 / 编码，不含容器
    // （容器只进展开网格）——与详情页规格胶囊同一套字段口径
    const chips = [
      resChip(video?.Height ?? null, video?.Width ?? null).toUpperCase(),
      rangeLabel(video),
      video?.Codec ? video.Codec.toUpperCase() : '',
    ].filter(Boolean)
    const metaParts: string[] = []
    if (s.Bitrate) metaParts.push(`${(s.Bitrate / 1_000_000).toFixed(1)} Mbps`)
    if (s.Size) metaParts.push(sizeText(s.Size))
    const ticks = s.RunTimeTicks || it.RunTimeTicks
    if (ticks) metaParts.push(formatDuration(ticksToSeconds(ticks)))
    const name = s.Name || video?.DisplayTitle || `版本 ${i + 1}`
    // 展开网格（截图序：分辨率/动态范围、编码/容器、帧率/文件大小、
    // 时长/位深），全部真实探测字段，缺的格不出现
    const details: VersionDetail[] = []
    if (video?.Width && video?.Height) details.push({ label: '分辨率', value: `${video.Width} × ${video.Height}` })
    details.push({ label: '动态范围', value: rangeLabel(video) })
    if (video?.Codec) details.push({ label: '编码', value: video.Codec.toUpperCase() })
    if (s.Container) details.push({ label: '容器', value: s.Container.toUpperCase() })
    const fps = fmtFps(video?.FrameRate)
    if (fps) details.push({ label: '帧率', value: fps })
    if (s.Size) details.push({ label: '文件大小', value: sizeText(s.Size) })
    if (ticks) details.push({ label: '时长', value: formatDuration(ticksToSeconds(ticks)) })
    if (video?.BitDepth) details.push({ label: '位深', value: `${video.BitDepth}-bit` })
    const audioStreams = streams.filter((x) => x.Type === 'Audio')
    const audios: VersionAudio[] = audioStreams.map((a) => ({
      label: audioLangLabel(a.Language),
      detail: [
        a.Codec ? a.Codec.toUpperCase() : '',
        a.ChannelLayout || (a.Channels ? `${a.Channels} 声道` : ''),
      ].filter(Boolean).join(' · '),
      isDefault: !!a.IsDefault,
    }))
    const dftAudio = audioStreams.find((a) => a.IsDefault) || audioStreams[0]
    return {
      id: s.Id,
      name,
      title: versionTitle(video, name),
      chips: [...new Set(chips)],
      metaParts,
      details,
      audios,
      audioLang: audios.length ? audioLangLabel(dftAudio?.Language) : '',
      kind: 'source',
    }
  })
})

const selectedVersionId = ref('')
const selectedVersion = computed(() => versions.value.find((v) => v.id === selectedVersionId.value) || null)
const expandedId = ref('')
/** 版本卡内音轨清单展开的是哪张卡 */
const audioOpenId = ref('')

/** 展开后把目标元素底部滚进版本区视口（固定一屏下展开内容容易落在
    折叠线之下、看起来像被裁掉——2026-11-14 09:39 用户实报缺陷）。
    元素比视口还高时只滚到其底部恰好可见，顶部让用户自己上滑。 */
async function revealBottom(el: HTMLElement | null) {
  if (!el) return
  await nextTick()
  const scroller = scrollEl.value
  if (!scroller) return
  const sRect = scroller.getBoundingClientRect()
  const eRect = el.getBoundingClientRect()
  const delta = eRect.bottom - sRect.bottom
  if (delta > 0) scroller.scrollBy({ top: delta + 16, behavior: 'smooth' })
}

/** 展开/收起版本详情：展开后自动滚动让网格与音轨行完整可见 */
function toggleExpand(v: VersionCard, ev: MouseEvent) {
  const card = (ev.currentTarget as HTMLElement | null)?.closest('.psp-ver') as HTMLElement | null
  const opening = expandedId.value !== v.id
  expandedId.value = opening ? v.id : ''
  if (opening) void revealBottom(card)
}

/** 展开/收起音轨清单：展开后同样把清单底部带进视口 */
function toggleAudio(v: VersionCard, ev: MouseEvent) {
  const block = (ev.currentTarget as HTMLElement | null)?.closest('.psp-audio') as HTMLElement | null
  const opening = audioOpenId.value !== v.id
  audioOpenId.value = opening ? v.id : ''
  if (opening) void revealBottom(block)
}

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

/** 音轨语言标注：与字幕共用语言表；轨没标语言就直说未标注，不猜 */
function audioLangLabel(lang?: string | null): string {
  const code = (lang || '').trim().toLowerCase()
  if (!code) return '未标注语言'
  if (code === 'chi' || code === 'zho' || code === 'zh') return '中文'
  return SUB_LANG_LABELS[code] || code.toUpperCase()
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

/** 内嵌字幕标签：DisplayTitle 优先；后端 DisplayTitle 缺 display_title
    时回填了语言码（chi/eng…），先翻译回中文名再显示；都没有才用
    Title / 语言兜底，仍为空时只写「字幕」 */
function embeddedSubLabel(s: { DisplayTitle?: string; Title?: string | null; Language?: string }): string {
  const title = (s.DisplayTitle || '').trim()
  if (title) {
    const code = title.toLowerCase()
    if (code === 'chi' || code === 'zho' || code === 'zh' || SUB_LANG_LABELS[code]) {
      return subLangLabel(title, s.Title || '')
    }
    return title
  }
  const t = (s.Title || '').trim()
  if (t) return t
  const lang = (s.Language || '').trim()
  if (!lang) return '字幕'
  return subLangLabel(lang, '')
}

/** 内嵌字幕（所选版本的内嵌字幕轨）：与 externalSubs 同源
    （subsSource 的 MediaStreams），只取 Type=Subtitle 且 !IsExternal
    的轨；只渲染、不选轨、不改取流逻辑 */
const embeddedSubs = computed<SubCard[]>(() => {
  const src = subsSource.value
  if (!src) return []
  return (src.MediaStreams || [])
    .filter((s) => s.Type === 'Subtitle' && !s.IsExternal)
    .map((s) => ({ index: s.Index, label: embeddedSubLabel(s), file: '' }))
})

/** 选中的外挂字幕流 Index；null = 本次不挂外挂字幕 */
const selectedSubIndex = ref<number | null>(null)
/** 字幕行当前选择（胶囊文案用） */
const selectedSub = computed(() => externalSubs.value.find((s) => s.index === selectedSubIndex.value) || null)
/** 字幕选单（固定一屏：向上弹层，不撑布局）开合 */
const subPickerOpen = ref(false)

function chooseSub(index: number | null) {
  selectedSubIndex.value = index
  subPickerOpen.value = false
}

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
  return t === 'Episode' || t === 'Series' ? '选集播放' : '立即播放'
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
  // 电影头图副行 = 原名（总账 7-1：友站片名下有一行原名；后端
  // _item_dto 有 original_title 才下发 OriginalTitle，无值/与片名相同则不渲染）
  if (it.Type === 'Movie') {
    const orig = (it.OriginalTitle || '').trim()
    return orig && orig !== it.Name ? orig : ''
  }
  const parts: string[] = []
  if (it.ProductionYear) parts.push(String(it.ProductionYear))
  if (it.Type === 'Series' && it.ChildCount) parts.push(`${it.ChildCount} 季`)
  if (it.Genres?.length) parts.push(it.Genres.slice(0, 2).join(' / '))
  return parts.join(' · ')
})

/* ---------- 头图眼睛钮：标记已看（Emby PlayedItems 真接口，与详情页
   「移除观看记录」同一口径的反向）。标记对象 = 本次起播目标（电影是
   本条目、剧集是续看/首集那一集），不整剧乱标。 ---------- */
const playedState = ref(false)
const playedBusy = ref(false)
const playedTargetId = computed(() =>
  playTargetId.value || (props.itemId !== null && props.itemId !== undefined ? String(props.itemId) : ''),
)
const canMarkPlayed = computed(() => !!playedTargetId.value && !!itemDetail.value && !loading.value)

async function togglePlayed() {
  const id = playedTargetId.value
  if (!id || playedBusy.value) return
  playedBusy.value = true
  const next = !playedState.value
  try {
    await embyProtocol.setPlayed(id, next)
    playedState.value = next
    if (versionSource.value && String(versionSource.value.Id) === id) {
      versionSource.value = {
        ...versionSource.value,
        UserData: { ...versionSource.value.UserData, Played: next },
      }
    }
    toast.success(next ? '已标记为看过' : '已取消看过标记')
  } catch {
    toast.info('标记没改成，请稍后再试')
  } finally {
    playedBusy.value = false
  }
}

watch(open, (now) => {
  if (!now) return
  expandedId.value = ''
  audioOpenId.value = ''
  subPickerOpen.value = false
  linePickerOpen.value = false
  void refresh()
  void nextTick(() => panelEl.value?.focus())
})

async function refresh() {
  loading.value = true
  // 线路清单与条目并行拉：开始播放钮的「正在准备线路」态以它落定为准
  void loadLines()
  try {
    const id = props.itemId
    const [bindingsResult, itemResult] = await Promise.allSettled([
      storageBindingApi.list(),
      id !== null && id !== undefined && String(id) !== ''
        ? embyProtocol.getItem(String(id))
        : Promise.resolve(null),
    ])
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
    playedState.value = !!(versionSource.value?.UserData?.Played ?? itemDetail.value?.UserData?.Played)
    syncSubSelection()
  } finally {
    loading.value = false
  }
}

function close() {
  open.value = false
}

/** 版本卡复制钮（友站形态 = 复制链接）：复制该版本的网页播放链接
    （站内 /play 路由 + 版本参数）。链接不带任何凭据，打开仍走登录与
    绑盘门禁，不外泄流地址/令牌。 */
async function copyVersionLink(v: VersionCard) {
  const target = playedTargetId.value
  const path =
    v.kind === 'version'
      ? `/play/${v.id}`
      : target
        ? `/play/${target}?ms=${encodeURIComponent(v.id)}`
        : ''
  if (!path) {
    toast.info('这个版本暂时没有可复制的播放链接')
    return
  }
  try {
    await navigator.clipboard.writeText(`${window.location.origin}${path}`)
    toast.success('已复制该版本的播放链接')
  } catch {
    toast.info('复制失败，请稍后再试')
  }
}

/**
 * 开始播放（网页播放器主入口）：走播放器单契约 —— 路由名
 * web-player、params.id=起播目标 id（剧集为集 id）、query.ms=
 * 选定版本 MediaSource Id（PR #85）；query.line=弹层选定的播放
 * 线路入口（与默认线路相同则不带，播放器侧会再按账号下发清单
 * 校验，手改不认）；query.sub=选定的外挂字幕流 Index（取消
 * 字幕时为 off；该版本没有外挂字幕时不带，播放器保持默认轨
 * 口径）。路由未落地时不假装能播，给诚实提示。第三方播放器
 * 入口不在本弹层（2026-11-14 已删该行），走详情页的播放器入口。
 */
async function start() {
  const id = playTargetId.value || props.itemId
  if (id === null || id === undefined || String(id) === '') {
    toast.info('这个入口没有带条目，暂时打不开')
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
    toast.info('网页播放器马上就来')
  }
}
</script>

<style scoped>
/* 批 3 播放前弹层（录屏形态）：手机 = 悬浮大卡（左右/底部留 12、圆角 28）；
   桌面 = 居中 610 宽、圆角 24（存档第 7 节）。头图 + 版本单选卡 +
   外挂字幕卡 + 播放线路行 + 玻璃粉彩开始播放钮（双色文案）。
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
  /* 固定一屏（2026-11-14 用户亲点）：上留约一截呼吸位、下贴边，
     弹层本体不滚动，只有版本区内部滚动 */
  top: calc(env(safe-area-inset-top, 0px) + 44px);
  bottom: calc(env(safe-area-inset-bottom) + 12px);
  display: flex;
  flex-direction: column;
  background: var(--uhd-l-surface, #ffffff);
  color: var(--uhd-l-text, #111111);
  border-radius: 28px;
  overflow: hidden;
  font-family: var(--uhd-font-sans, sans-serif);
  box-shadow: var(--uhd-shadow-modal, 0 24px 64px -24px rgba(0, 0, 0, 0.35));
}
/* 头图区 */
.psp-hero-wrap {
  position: relative;
  flex: none;
}
.psp-hero {
  position: relative;
  height: 208px;
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
  font-size: 13px;
  color: rgba(255, 255, 255, 0.78);
}
/* 头图右下眼睛钮（标记已看）：深色毛玻璃圆钮，压在图内右下角 */
.psp-eye {
  position: absolute;
  right: 14px;
  bottom: 12px;
  z-index: 2;
  width: 38px;
  height: 38px;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.28);
  background: rgba(20, 20, 22, 0.45);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  color: #ffffff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  padding: 0;
}
.psp-eye:disabled {
  opacity: 0.6;
  cursor: wait;
}
/* 弹层主体：滚动区（版本） + 固定区（字幕/线路/播放器/开始播放） */
.psp-body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.psp-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 14px 20px 8px;
}
.psp-fixed {
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 6px 20px 16px;
}
/* 加载态：正在获取资源和线路 */
.psp-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 26px 0;
  font-size: 14px;
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
/* 版本区：段头为灰字（友站形态），计数同色 */
.psp-sec-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  font-size: 14px;
  font-weight: 600;
  color: var(--uhd-l-text-2, #6b7280);
  margin-top: 4px;
}
.psp-sec-count {
  font-size: 13px;
  font-weight: 400;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-vers {
  display: flex;
  flex-direction: column;
  gap: 9px;
  margin-top: 10px;
}
/* 卡内三行网格（友站顺序，2026-11-14 09:39 用户亲点改）：
   第一行标题（第 2 列，左缘与胶囊列齐）、第二行圆点（第 1 列）+
   胶囊（第 2 列）、第三行码率（第 2 列）；工具列第 3 列跨三行
   垂直居中；展开详情整行全宽落在第四行 */
.psp-ver {
  display: grid;
  grid-template-columns: 19px minmax(0, 1fr) auto;
  column-gap: 10px;
  row-gap: 5px;
  align-items: center;
  padding: 12px 14px;
  border-radius: 18px;
  border: 1.5px solid var(--uhd-l-border, #e5e5e5);
  background: var(--uhd-l-input-bg, #f5f5f5);
  cursor: pointer;
}
.psp-ver--on {
  border-color: var(--uhd-l-text, #111111);
  background: var(--uhd-l-surface, #ffffff);
}
.psp-ver__radio {
  grid-column: 1;
  grid-row: 2;
  width: 19px;
  height: 19px;
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
.psp-ver__name {
  grid-column: 2;
  grid-row: 1;
  min-width: 0;
  font-size: 17px;
  font-weight: 700;
  line-height: 1.3;
}
.psp-ver__chips {
  grid-column: 2;
  grid-row: 2;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
/* 规格小胶囊：描边 + 微底、粗字（友站卡内形态） */
.psp-ver__chip {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.02em;
  line-height: 1;
  padding: 3px 8px;
  border-radius: 6px;
  border: 1px solid var(--uhd-l-border, #e5e5e5);
  background: rgba(0, 0, 0, 0.04);
  color: var(--uhd-l-text, #111111);
}
.psp-ver__meta {
  grid-column: 2;
  grid-row: 3;
  font-size: 13px;
  line-height: 1.5;
  color: var(--uhd-l-text-2, #6b7280);
}
/* 码率/大小/时长逐段成粒：段内不折行，换行只落在段间分隔点上 */
.psp-ver__metapart {
  white-space: nowrap;
}
.psp-ver__metapart + .psp-ver__metapart::before {
  content: ' · ';
}
.psp-ver__tools {
  grid-column: 3;
  grid-row: 1 / 4;
  align-self: center;
  display: flex;
  gap: 6px;
}
.psp-tool {
  width: 35px;
  height: 35px;
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
/* 展开态：分隔线 + 两列信息网格 + 音轨行（整卡全宽，与友站展开卡同形态） */
.psp-ver__detail {
  grid-column: 1 / -1;
  grid-row: 4;
  min-width: 0;
  margin-top: 4px;
  padding-top: 12px;
  border-top: 1px solid var(--uhd-l-border, #e5e5e5);
}
.psp-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  column-gap: 18px;
  row-gap: 12px;
}
.psp-grid__cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.psp-grid__label {
  font-size: 12.5px;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-grid__value {
  font-size: 15px;
  font-weight: 600;
  color: var(--uhd-l-text, #111111);
}
.psp-audio {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--uhd-l-border, #e5e5e5);
}
.psp-audio__head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 2px 0;
  border: none;
  background: none;
  color: var(--uhd-l-text, #111111);
  font-family: inherit;
  cursor: pointer;
  text-align: left;
}
.psp-audio__name {
  font-size: 14px;
  font-weight: 700;
}
.psp-audio__lang {
  font-size: 13px;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-audio__count {
  margin-left: auto;
  font-size: 13px;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-audio__list {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
}
.psp-audio__row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
  font-size: 13.5px;
  color: var(--uhd-l-text, #111111);
}
.psp-audio__rowmeta {
  font-size: 12.5px;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-audio__badge {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.07);
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-note {
  margin: 12px 0 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-note a {
  color: var(--uhd-l-text, #111111);
  font-weight: 700;
}
.psp-tip {
  margin: 14px 0 0;
  font-size: 12px;
  line-height: 1.65;
  color: var(--uhd-l-text-2, #6b7280);
}
/* 开始播放 */
.psp-start-sec {
  margin-top: 0;
}
.psp-start-label {
  font-size: 12.5px;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-start {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  height: 56px;
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
/* 钮内版本名段：细体灰（与「开始播放」粗体成双色，友站形态） */
.psp-start__ver {
  font-size: 15px;
  font-weight: 500;
  color: var(--uhd-l-text-2, #6b7280);
}
/* 准备态（线路未落定）：形态不变，只换钮内文案，指针示意等待 */
.psp-start--prep {
  cursor: progress;
}
/* 外挂字幕一行：左标签 + 右选择胶囊；选单向上弹层 */
.psp-subrow {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.psp-subrow__label {
  font-size: 13px;
  font-weight: 600;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-subrow__pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-width: 46%;
  max-width: 68%;
  min-height: 38px;
  padding: 7px 13px;
  border: 1.5px solid var(--uhd-l-border, #e5e5e5);
  border-radius: 12px;
  background: var(--uhd-l-input-bg, #f5f5f5);
  color: var(--uhd-l-text, #111111);
  font-size: 14px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
}
/* 内嵌字幕行复用同一胶囊样式，只展示不点按：去掉手形指针 */
.psp-subrow__pill--static {
  cursor: default;
}
.psp-subrow__name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.psp-subrow__count {
  flex: none;
  font-size: 12.5px;
  font-weight: 400;
  color: var(--uhd-l-text-2, #6b7280);
}
/* 向上弹出的选单（字幕/线路共用）：浮在固定区上方，不撑动布局 */
.psp-pop {
  position: absolute;
  right: 0;
  bottom: calc(100% + 8px);
  z-index: 30;
  width: min(330px, 84vw);
  max-height: 38dvh;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 6px;
  border-radius: 16px;
  border: 1px solid var(--uhd-l-border, #e5e5e5);
  background: var(--uhd-l-surface, #ffffff);
  box-shadow: var(--uhd-shadow-modal, 0 24px 64px -24px rgba(0, 0, 0, 0.35));
}
.psp-pop__opt {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 11px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--uhd-l-text, #111111);
  font-size: 14px;
  font-family: inherit;
  cursor: pointer;
  text-align: left;
}
.psp-pop__opt:hover {
  background: var(--uhd-l-input-bg, #f5f5f5);
}
.psp-pop__radio {
  flex: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 2px solid #b6bcc4;
  position: relative;
}
.psp-pop__opt--on .psp-pop__radio {
  border-color: var(--uhd-l-text, #111111);
}
.psp-pop__opt--on .psp-pop__radio::after {
  content: '';
  position: absolute;
  inset: 2.5px;
  border-radius: 50%;
  background: var(--uhd-l-text, #111111);
}
.psp-pop__main {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
.psp-pop__file {
  font-size: 12px;
  color: var(--uhd-l-text-2, #6b7280);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 播放线路（选单向上弹层，容器作定位锚） */
.psp-line-sec {
  position: relative;
}
.psp-line-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.psp-line-label {
  font-size: 13px;
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
  min-height: 38px;
  padding: 7px 13px;
  border: 1.5px solid var(--uhd-l-border, #e5e5e5);
  border-radius: 12px;
  background: var(--uhd-l-input-bg, #f5f5f5);
  color: var(--uhd-l-text, #111111);
  font-size: 14px;
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
/* 线路选项在弹层里降为无边框行（弹层自带边框与阴影） */
.psp-pop--line .psp-line-opt {
  border: none;
  border-radius: 10px;
  background: transparent;
  padding: 9px 11px;
}
.psp-pop--line .psp-line-opt:hover {
  background: var(--uhd-l-input-bg, #f5f5f5);
}
.psp-line-opt {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 10px 14px;
  border: 1.5px solid var(--uhd-l-border, #e5e5e5);
  border-radius: 14px;
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
  font-size: 14px;
  font-weight: 600;
}
.psp-line-opt__host {
  font-size: 12px;
  color: var(--uhd-l-text-2, #6b7280);
}
.psp-sheet:focus-visible,
.psp-x:focus-visible,
.psp-eye:focus-visible,
.psp-tool:focus-visible,
.psp-subrow__pill:focus-visible,
.psp-pop__opt:focus-visible,
.psp-audio__head:focus-visible,
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
    max-height: 88dvh;
    border-radius: 24px;
  }
  /* D3-05：原站桌面弹窗无标题头（存档 610 弹窗只有版本/线路/开始播放），
     剧照头图只保留在手机底部弹层（录屏形态） */
  .psp-hero-wrap {
    display: none;
  }
  /* 关闭钮浮在弹层右上，版块头右侧计数给它让位 */
  .psp-sec-head {
    padding-right: 44px;
  }
  .psp-scroll {
    padding: 24px 28px 10px;
  }
  .psp-fixed {
    padding: 8px 28px 26px;
  }
}
</style>

<style>
/* 片库面深色态（body 标记桥接） */
body.uhd-stream-dark .psp-sheet {
  background: #1c1c1c;
  color: #ffffff;
}
body.uhd-stream-dark .psp-sec-head,
body.uhd-stream-dark .psp-sec-count,
body.uhd-stream-dark .psp-ver__meta,
body.uhd-stream-dark .psp-note,
body.uhd-stream-dark .psp-loading,
body.uhd-stream-dark .psp-start-label,
body.uhd-stream-dark .psp-grid__label,
body.uhd-stream-dark .psp-audio__lang,
body.uhd-stream-dark .psp-audio__count,
body.uhd-stream-dark .psp-audio__rowmeta,
body.uhd-stream-dark .psp-subrow__label,
body.uhd-stream-dark .psp-subrow__count,
body.uhd-stream-dark .psp-start__ver,
body.uhd-stream-dark .psp-pop__file {
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
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.16);
  color: #ffffff;
}
body.uhd-stream-dark .psp-tool {
  background: #262626;
  border-color: rgba(255, 255, 255, 0.14);
  color: #ffffff;
}
body.uhd-stream-dark .psp-ver__detail,
body.uhd-stream-dark .psp-audio {
  border-top-color: rgba(255, 255, 255, 0.12);
}
body.uhd-stream-dark .psp-grid__value {
  color: #ffffff;
}
body.uhd-stream-dark .psp-audio__head,
body.uhd-stream-dark .psp-audio__row {
  color: #ffffff;
}
body.uhd-stream-dark .psp-audio__badge {
  background: rgba(255, 255, 255, 0.12);
  color: #9ca3af;
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
body.uhd-stream-dark .psp-subrow__pill,
body.uhd-stream-dark .psp-line-pill {
  background: #262626;
  border-color: rgba(255, 255, 255, 0.14);
  color: #ffffff;
}
body.uhd-stream-dark .psp-pop {
  background: #26262b;
  border-color: rgba(255, 255, 255, 0.14);
}
body.uhd-stream-dark .psp-pop__opt {
  color: #ffffff;
}
body.uhd-stream-dark .psp-pop__opt:hover,
body.uhd-stream-dark .psp-pop--line .psp-line-opt:hover {
  background: rgba(255, 255, 255, 0.06);
}
body.uhd-stream-dark .psp-pop__radio {
  border-color: rgba(255, 255, 255, 0.4);
}
body.uhd-stream-dark .psp-pop__opt--on .psp-pop__radio {
  border-color: #ffffff;
}
body.uhd-stream-dark .psp-pop__opt--on .psp-pop__radio::after {
  background: #ffffff;
}
body.uhd-stream-dark .psp-line-opt {
  background: #222222;
  border-color: rgba(255, 255, 255, 0.14);
  color: #ffffff;
}
body.uhd-stream-dark .psp-pop--line .psp-line-opt {
  background: transparent;
  border-color: transparent;
}
body.uhd-stream-dark .psp-line-opt--on {
  border-color: #ffffff;
}
body.uhd-stream-dark .psp-pop--line .psp-line-opt--on {
  border-color: transparent;
}
body.uhd-stream-dark .psp-line-opt__host {
  color: #9ca3af;
}
body.uhd-stream-dark .psp-line-label {
  color: #9ca3af;
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
