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
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, BellPlus, BookmarkPlus, Clapperboard, Heart, Play } from 'lucide-vue-next'
import {
  embyApi as embyProtocol,
  backdropUrl,
  formatDuration,
  imageUrl,
  posterUrl,
  ticksToSeconds,
  type EmbyItem,
} from '@/api/emby'
import { embyApi, mediaSeekApi, storeApi, type PortalResumeItem } from '@/api'
import { seriesFollowApi } from '@/api/economy'
import SeriesEpisodeList from '@/components/panel/SeriesEpisodeList.vue'
import PlayerSchemeSheet from '@/components/panel/PlayerSchemeSheet.vue'
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
const backdrop = computed(() => (item.value ? backdropUrl(item.value, 1600) : ''))
const isSeries = computed(() => item.value?.Type === 'Series')
const typeLabel = computed(() => (isSeries.value ? '剧集' : item.value?.Type === 'Movie' ? '电影' : item.value?.Type || ''))

const metaParts = computed(() => {
  const it = item.value
  if (!it) return [] as string[]
  const parts: string[] = []
  if (it.ProductionYear) parts.push(String(it.ProductionYear))
  if (isSeries.value && it.ChildCount) parts.push(`${it.ChildCount}季`)
  if (it.RunTimeTicks) parts.push(formatDuration(ticksToSeconds(it.RunTimeTicks)))
  return parts
})

/** 门户续看条目（按条目 id 匹配）：给播放钮与续看胶囊提供季/集/集名 */
const resumeEntry = ref<PortalResumeItem | null>(null)
async function loadResume() {
  resumeEntry.value = null
  try {
    const res = await embyApi.getResume(50)
    resumeEntry.value = (res?.items || []).find((x) => String(x.id) === itemId.value) || null
  } catch {
    resumeEntry.value = null
  }
}

/** 播放主按钮文案（录屏续看态）：上次看到第X季第X集 · 集名 */
const playLabel = computed(() => {
  const e = resumeEntry.value
  if (e && e.type === 'series' && e.season_number != null && e.episode_number != null) {
    return `上次看到第${e.season_number}季第${e.episode_number}集${e.episode_name ? ` · ${e.episode_name}` : ''}`
  }
  if (e) return '继续播放'
  return resumeText.value ? '继续播放' : '立即播放'
})

const resumeText = computed(() => {
  const e = resumeEntry.value
  if (e && e.type === 'series' && e.season_number != null && e.episode_number != null) {
    const pct = typeof e.progress === 'number' ? ` · 已看 ${Math.round(e.progress)}%` : ''
    return `续看 S${e.season_number}：E${e.episode_number}${e.episode_name ? `《${e.episode_name}》` : ''}${pct}`
  }
  const pos = item.value?.UserData?.PlaybackPositionTicks || 0
  if (!pos || !item.value?.RunTimeTicks) return ''
  const pct = Math.min(99, Math.round((pos / item.value.RunTimeTicks) * 100))
  return `上次看到 ${pct}% · ${formatDuration(ticksToSeconds(pos))} 处`
})

const specChips = computed(() => {
  const it = item.value
  if (!it) return [] as string[]
  const chips: string[] = []
  if (it.Width && it.Height) {
    chips.push(it.Height >= 2000 ? '4K' : it.Height >= 1000 ? '1080p' : `${it.Height}p`)
  }
  if (it.OfficialRating) chips.push(it.OfficialRating)
  for (const s of it.MediaSources || []) {
    if (s.Container) chips.push(s.Container.toUpperCase())
  }
  return [...new Set(chips)].slice(0, 4)
})

interface VersionCard {
  key: string
  label: string
  sub: string
  size: string
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

function errText(err: unknown): string {
  const msg = (err as { userMessage?: string })?.userMessage
  if (msg) return msg
  if (err instanceof Error) return err.message
  return '加载失败'
}

async function loadRecommendations(it: EmbyItem) {
  recommendations.value = []
  const genre = it.Genres?.[0]
  if (!genre) return
  try {
    const res = await embyProtocol.getItems({
      genres: [genre],
      includeTypes: [it.Type === 'Series' ? 'Series' : 'Movie'],
      limit: 12,
    })
    recommendations.value = (res.Items || []).filter((x) => x.Id !== it.Id).slice(0, 10)
  } catch {
    recommendations.value = []
  }
}

async function load() {
  if (!itemId.value) {
    error.value = '条目地址不对'
    return
  }
  loading.value = true
  error.value = ''
  posterBroken.value = false
  overviewExpanded.value = false
  try {
    item.value = await embyProtocol.getItem(itemId.value)
    void loadRecommendations(item.value)
    void loadResume()
    void loadWantState()
    void loadFollowState()
  } catch (err) {
    item.value = null
    error.value = `详情没加载出来：${errText(err)}`
  } finally {
    loading.value = false
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

/** 想看（概览「我的片子」组摘除后归位到详情页动作区）：与求片页同一个
 * postWant 口，按 tmdb 匹配本人想看清单；商店闸未开/非会员时 getWants
 * 失败，整钮不显示（与求片页想看区同口径）。 */
const wantVisible = ref(false)
const wanted = ref(false)
const wantBusy = ref(false)
async function loadWantState() {
  wantVisible.value = false
  wanted.value = false
  const tid = tmdbId.value
  if (!tid) return
  try {
    const res = await storeApi.getWants()
    wantVisible.value = true
    wanted.value = (res.items || []).some(
      i => i.tmdb_id === tid && i.state !== 'failed' && i.state !== 'missing_source',
    )
  } catch { /* 拿不到就不显示 */ }
}
async function handleWant() {
  const tid = tmdbId.value
  if (!tid || wantBusy.value || wanted.value) return
  wantBusy.value = true
  try {
    const r = await storeApi.postWant(tid)
    toast.success(r.message || '已加入想看')
    if (r.state !== 'failed' && r.state !== 'missing_source') wanted.value = true
  } catch (err) {
    toast.error(`想看没加上：${errText(err)}`)
  } finally {
    wantBusy.value = false
  }
}

/** 追剧订阅（同上归位；管理面板在账号设置页）：仅剧集。订阅口只认数字
 * 主键，走站内查库口按条目 GUID 对位取 item_id（与订阅面板同口径）；
 * 接口 404（未启用）或对不上主键时整钮不显示。 */
const followVisible = ref(false)
const followBusy = ref(false)
const followSubscribed = ref(false)
const followSeriesId = ref<number | null>(null)
async function loadFollowState() {
  followVisible.value = false
  followSubscribed.value = false
  followSeriesId.value = null
  const it = item.value
  if (!it || it.Type !== 'Series') return
  let follows
  try {
    const res = await seriesFollowApi.list()
    follows = res.follows || []
  } catch { return /* 未启用/失败：整钮不显示 */ }
  try {
    const res = await mediaSeekApi.lookup(it.Name)
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
      <!-- 剧照带 Hero：高约 340、底部渐隐至页面底 -->
      <div class="md-hero">
        <img v-if="backdrop" :src="backdrop" alt="" class="md-hero__img" />
        <div class="md-hero__fade" aria-hidden="true"></div>
        <button type="button" class="md-back" aria-label="返回" @click="goBack">
          <ArrowLeft :size="20" />
        </button>
      </div>

      <div class="md-wrap">
        <p class="md-type">{{ typeLabel }}</p>
        <div class="md-main">
          <div class="md-poster">
            <img v-if="poster && !posterBroken" :src="poster" alt="" class="md-poster__img" @error="posterBroken = true" />
            <Clapperboard v-else :size="36" :stroke-width="1.5" class="md-poster__fallback" aria-hidden="true" />
          </div>

          <div class="md-info">
            <h1 class="md-title" :class="{ 'md-title--movie': !isSeries }">{{ item.Name }}</h1>
            <p class="md-meta">
              <template v-for="(p, i) in metaParts" :key="i">{{ i ? ' · ' : '' }}{{ p }}</template>
              <b v-if="item.CommunityRating" class="md-rating"> · ★ {{ item.CommunityRating.toFixed(1) }}</b>
            </p>
            <div v-if="item.Genres?.length" class="md-chips">
              <span v-for="g in item.Genres.slice(0, 4)" :key="g" class="md-chip">{{ g }}</span>
            </div>
            <div v-if="specChips.length" class="md-chips">
              <span v-for="c in specChips" :key="c" class="md-chip">{{ c }}</span>
            </div>
            <p v-if="resumeText" class="md-resume">
              <!-- 实心三角 SVG：原 ▶ 字符在移动端渲染成蓝色 emoji 方块，已换掉 -->
              <svg class="md-resume__tri" width="9" height="10" viewBox="0 0 12 12" aria-hidden="true">
                <path d="M2 1.3v9.4c0 .8.9 1.3 1.6.9l7.2-4.7c.6-.4.6-1.4 0-1.8L3.6.4C2.9-.1 2 .5 2 1.3Z" fill="currentColor" />
              </svg>
              {{ resumeText }}
            </p>

            <!-- 按钮行高 52：「立即播放」主胶囊 + 收藏 + 想看 + 追剧订阅 + 状态。
                 想看/追剧订阅原在概览「我的片子」组，2026-11-13 按用户令
                 摘组后归位到详情页动作区（显隐口径见脚本 loadWantState /
                 loadFollowState：拿不到数据整钮不显示）。
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
                v-if="wantVisible"
                type="button"
                class="md-btn md-btn--fav"
                :class="{ 'md-btn--fav-on': wanted }"
                :disabled="wantBusy || wanted"
                @click="handleWant"
              >
                <BookmarkPlus :size="17" :fill="wanted ? 'currentColor' : 'none'" />
                {{ wanted ? '已想看' : '想看' }}
              </button>
              <button
                v-if="followVisible"
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
            </div>

            <div v-if="item.Overview" class="md-overview-box">
              <p class="md-overview" :class="{ 'md-overview--open': overviewExpanded }">{{ item.Overview }}</p>
              <button type="button" class="md-expand" @click="overviewExpanded = !overviewExpanded">
                {{ overviewExpanded ? '收起' : '展开' }}
              </button>
            </div>
            <p class="md-note">点「立即播放」选个播放器就能看；本页也能看这部片齐不齐，缺的集可以直接求补，补齐后会通知你。</p>
          </div>

          <!-- 右侧信息栏：竖分隔线 + TMDB/IMDb chip -->
          <aside class="md-rail" aria-label="条目信息">
            <h2 class="md-rail__title">{{ isSeries ? '剧集信息' : '影片信息' }}</h2>
            <dl class="md-rail__list">
              <div v-if="item.ProductionYear" class="md-rail__row">
                <dt>年份</dt><dd>{{ item.ProductionYear }}</dd>
              </div>
              <div v-if="item.PremiereDate" class="md-rail__row">
                <dt>上映</dt><dd>{{ item.PremiereDate.slice(0, 10) }}</dd>
              </div>
              <div v-if="item.RunTimeTicks" class="md-rail__row">
                <dt>片长</dt><dd>{{ formatDuration(ticksToSeconds(item.RunTimeTicks)) }}</dd>
              </div>
              <div v-if="item.Genres?.length" class="md-rail__row">
                <dt>类型</dt><dd>{{ item.Genres.join(' / ') }}</dd>
              </div>
              <div v-if="item.OfficialRating" class="md-rail__row">
                <dt>分级</dt><dd>{{ item.OfficialRating }}</dd>
              </div>
              <div v-if="item.Studios?.length" class="md-rail__row">
                <dt>出品</dt><dd>{{ item.Studios.slice(0, 2).join(' / ') }}</dd>
              </div>
            </dl>
            <div class="md-rail__ids">
              <span v-if="tmdbId" class="md-idchip">TMDB {{ tmdbId }}</span>
              <span v-if="imdbId" class="md-idchip">IMDb {{ imdbId }}</span>
            </div>
          </aside>
        </div>

        <!-- 演职人员（DTO 下发才渲染）：头像圆 48、姓名 15 粗、角色 13 灰、横滑 -->
        <section v-if="item.People?.length" class="md-cast">
          <h2 class="md-sec-title">演职人员</h2>
          <div class="md-cast__track">
            <div v-for="(p, i) in item.People.slice(0, 20)" :key="`${p.Name}-${i}`" class="md-cast__person">
              <span class="md-cast__avatar">
                <img v-if="p.Id && p.PrimaryImageTag" :src="imageUrl(p.Id, 'Primary', 96)" alt="" loading="lazy" />
                <template v-else>{{ p.Name.charAt(0) }}</template>
              </span>
              <span class="md-cast__name">{{ p.Name }}</span>
              <span v-if="p.Role" class="md-cast__role">{{ p.Role }}</span>
            </div>
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
      </div>

      <!-- 更多推荐：与首页同款海报横滑行 -->
      <StreamRow v-if="recommendations.length" title="更多推荐" :count="recommendations.length">
        <StreamPosterCard
          v-for="rec in recommendations"
          :key="rec.Id"
          :title="rec.Name"
          :meta="rec.ProductionYear ? String(rec.ProductionYear) : ''"
          :rating="rec.CommunityRating ?? null"
          :image="posterUrl(rec, 520)"
          :to="`/media/${rec.Id}`"
        />
      </StreamRow>
      <div style="height: 48px"></div>

      <!-- 选播放器弹层（现行组件的片库面皮）：起播/绑盘引导/状态条都在里面 -->
      <PlayerSchemeSheet
        v-model:open="sheetOpen"
        :title="item.Name"
        :item-id="item.Id"
      />
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
.md-poster__fallback {
  color: var(--uhd-s-text-2);
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
    var(--uhd-rainbow-border) border-box;
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
/* 深色版：黑毛玻璃底 + 白字（同 StreamHero .sh-play 深色口径） */
.stream-scope[data-stream-theme="dark"] .md-btn--play {
  background:
    linear-gradient(var(--uhd-glass-btn-d), var(--uhd-glass-btn-d)) padding-box,
    var(--uhd-rainbow-border) border-box;
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
.md-rail__ids {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}
.md-idchip {
  display: inline-flex;
  align-items: center;
  height: 28px;
  padding: 0 10px;
  border-radius: 14px;
  border: 1px solid var(--uhd-s-border);
  font-size: 12px;
  color: var(--uhd-s-text-2);
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

/* 手机详情 = 录屏的 app 形态：无剧照带/无海报，顺序为
   返回 → 类型小字 → 大标题 → 元信息 → 全宽播放行（吸顶）→ 简介
   → 演职员 → 集数 → 信息表 → 推荐 */
@media (max-width: 767px) {
  .md-hero {
    height: auto;
    margin-top: 0;
    /* D2-01 后手机无顶栏，回退钮不再需要给胶囊让位 */
    padding-top: 16px;
    background: none;
  }
  .md-hero__img,
  .md-hero__fade {
    display: none;
  }
  .md-back {
    position: static;
    margin-left: 20px;
    width: 44px;
    height: 44px;
    background: var(--uhd-s-card-soft);
    border: 1px solid var(--uhd-s-border);
    color: var(--uhd-s-text);
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }
  .md-wrap {
    display: flex;
    flex-direction: column;
  }
  .md-main {
    display: contents;
  }
  .md-poster {
    display: none;
  }
  .md-type {
    order: 0;
  }
  /* info 也打散：让播放行以整页（.md-wrap）为吸顶容器，全程常驻 */
  .md-info {
    display: contents;
  }
  .md-info > * {
    order: 1;
  }
  .md-versions {
    order: 2;
  }
  .md-cast {
    order: 3;
  }
  .md-episodes {
    order: 4;
  }
  .md-rail {
    order: 5;
    border-left: none;
    border-top: 1px solid var(--uhd-s-divider);
    padding-left: 0;
    margin-top: 36px;
    width: auto;
  }
  .sr-row {
    order: 6;
  }
  .md-title {
    font-size: 34px;
  }
  .md-title--movie {
    font-size: 36px;
  }
  /* 播放行吸顶（录屏 f03 形态）：毛玻璃条常驻顶部（手机已无顶栏） */
  .md-actions {
    position: sticky;
    top: 8px;
    z-index: 8;
    margin-left: -24px;
    margin-right: -24px;
    padding: 8px 24px;
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
      var(--uhd-rainbow) border-box;
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
      var(--uhd-rainbow) border-box;
    color: #ffffff;
    box-shadow:
      inset 0 1px 0 rgba(255, 255, 255, 0.22),
      var(--uhd-s-shadow-pill);
  }
  .md-btn--fav {
    flex: 0 0 52px;
    width: 52px;
    min-width: 52px;
    padding: 0;
    border-radius: 50%;
    font-size: 0;
    gap: 0;
  }
  .md-btn--state {
    display: none;
  }
  .md-skel__band {
    height: 120px;
  }
  .md-skel__poster {
    display: none;
  }
}
</style>
