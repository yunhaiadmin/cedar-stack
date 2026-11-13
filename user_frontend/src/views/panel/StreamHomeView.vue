<script setup lang="ts">
/**
 * 片库首页（/library，批 3 —— UHDNow /stream 首页对应面）
 * 骨架：全幅 Hero 轮播 + 继续观看 16:9 卡行 + 海报横滑行（数据驱动）。
 * 数据口径全部复用现行接口，不新造：
 * - Hero / 海报行：Emby 协议口 getLatest + getViews 逐库 getItems；
 * - 继续观看：门户口径 getResume（与发现页同一只读口）；
 * - 分区行：getHomeSections 的 recently_updated 分区（有才渲染）。
 * 播放入口：Hero「立即播放」打开 PlayerSchemeSheet（批 3 底部弹层形态：
 * 选版本 + 选线路，开始播放默认进网页播放器 /watch、第三方播放器保留），
 * 海报卡只进详情页、不挂播放入口。
 */
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { Clapperboard, Trash2 } from 'lucide-vue-next'
import { useToast } from '@/composables/useToast'
import StreamShell from '@/components/stream/StreamShell.vue'
import StreamHero, { type StreamHeroItem } from '@/components/stream/StreamHero.vue'
import StreamRow from '@/components/stream/StreamRow.vue'
import StreamPosterCard from '@/components/stream/StreamPosterCard.vue'
import PlayerSchemeSheet from '@/components/panel/PlayerSchemeSheet.vue'
import { embyApi, type PortalResumeItem } from '@/api'
import {
  embyApi as embyProtocol,
  backdropUrl,
  formatDuration,
  posterUrl,
  ticksToSeconds,
  type EmbyItem,
} from '@/api/emby'
import { imageAuthUrl } from '@/api/emby'

interface PosterRowData {
  key: string
  title: string
  count: number | null
  moreTo?: string
  items: Array<{ id: string; title: string; meta: string; rating: number | null; image: string }>
}

const toast = useToast()

const heroItems = ref<StreamHeroItem[]>([])
const resumeItems = ref<PortalResumeItem[]>([])
const rows = ref<PosterRowData[]>([])
const loading = ref(true)
const loadError = ref('')

const pickerOpen = ref(false)
const pickerTitle = ref('')
const pickerItemId = ref<string | number | null>(null)

function metaOf(item: EmbyItem): string {
  const parts: string[] = []
  if (item.ProductionYear) parts.push(String(item.ProductionYear))
  if (item.Genres?.length) parts.push(item.Genres.slice(0, 2).join(' / '))
  return parts.join(' · ')
}

function toPoster(item: EmbyItem) {
  return {
    id: item.Id,
    title: item.Name,
    meta: metaOf(item),
    rating: item.CommunityRating ?? null,
    image: posterUrl(item, 520),
  }
}

function resumeTitle(m: PortalResumeItem): string {
  return m.series_name || m.name
}
function resumeSub(m: PortalResumeItem): string {
  const parts: string[] = []
  if (m.type === 'series' && m.season_number != null && m.episode_number != null) {
    parts.push(`S${m.season_number} E${m.episode_number}`)
  }
  if (m.episode_name) parts.push(m.episode_name)
  return parts.join(' · ') || m.name
}
/** 进度行（录屏形态）：已看 X% · 已看时长 */
function resumeProgress(m: PortalResumeItem): string {
  const parts: string[] = []
  if (typeof m.progress === 'number') parts.push(`已看 ${Math.round(m.progress)}%`)
  if (m.position_ticks) parts.push(formatDuration(ticksToSeconds(m.position_ticks)))
  return parts.join(' · ')
}
const hidingId = ref('')
/** 继续观看卡右上垃圾桶：隐藏这条（现行 hideResumeItem 口径，可在别处恢复） */
async function hideResume(m: PortalResumeItem) {
  if (hidingId.value) return
  hidingId.value = m.id
  try {
    await embyApi.hideResumeItem(m.id)
    resumeItems.value = resumeItems.value.filter((x) => x.id !== m.id)
    toast.success('已从继续观看里移除')
  } catch {
    toast.error('移除失败，请稍后再试')
  } finally {
    hidingId.value = ''
  }
}
function resumeImage(m: PortalResumeItem): string {
  return m.poster_url ? imageAuthUrl(m.poster_url, 640) : ''
}
/** 缩略图加载失败（个别条目图 404 等）的卡：撤下 img 换占位，不留浏览器裂图图标 */
const brokenResumeThumbs = ref(new Set<string>())

function onHeroPlay(item: StreamHeroItem) {
  pickerTitle.value = item.title
  pickerItemId.value = item.id
  pickerOpen.value = true
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const [latest, resume, sections, views] = await Promise.allSettled([
      embyProtocol.getLatest(12),
      embyApi.getResume(12),
      embyApi.getHomeSections(),
      embyProtocol.getViews(),
    ])

    if (latest.status === 'fulfilled') {
      heroItems.value = (latest.value || [])
        .filter((it) => it.BackdropImageTags?.length || it.ImageTags?.Primary)
        .slice(0, 5)
        .map((it) => ({
          id: it.Id,
          title: it.Name,
          meta: metaOf(it),
          rating: it.CommunityRating ?? null,
          backdrop: backdropUrl(it, 1600) || posterUrl(it, 1080),
          overview: it.Overview || '',
        }))
    }
    if (resume.status === 'fulfilled') {
      resumeItems.value = resume.value?.items || []
    }

    const out: PosterRowData[] = []
    if (sections.status === 'fulfilled') {
      for (const sec of sections.value?.sections || []) {
        if (sec.key === 'continue_watching') continue
        const items = (sec.items || []) as Array<{
          guid?: string
          id?: number | string
          name: string
          production_year?: number | null
          poster_url?: string | null
        }>
        if (!items.length) continue
        out.push({
          key: `sec-${sec.key}`,
          title: sec.title,
          count: null,
          items: items.slice(0, 14).map((it): PosterRowData['items'][number] => ({
            id: String(it.guid || it.id || ''),
            title: it.name,
            meta: it.production_year ? String(it.production_year) : '',
            rating: null,
            image: it.poster_url ? imageAuthUrl(it.poster_url, 520) : '',
          })).filter((it) => it.id),
        })
      }
    }
    if (views.status === 'fulfilled') {
      const viewList = (views.value || []).slice(0, 8)
      const viewRows = await Promise.allSettled(
        viewList.map(async (view) => {
          const res = await embyProtocol.getItems({
            parentId: view.Id,
            includeTypes: ['Movie', 'Series'],
            sortBy: 'DateCreated',
            sortOrder: 'Descending',
            limit: 14,
          })
          return { view, res }
        }),
      )
      for (const r of viewRows) {
        if (r.status !== 'fulfilled') continue
        const items = (r.value.res.Items || []).map(toPoster)
        if (!items.length) continue
        out.push({
          key: `view-${r.value.view.Id}`,
          title: r.value.view.Name,
          count: r.value.res.TotalRecordCount || null,
          moreTo:
            r.value.view.CollectionType === 'movies'
              ? '/library/movies'
              : r.value.view.CollectionType === 'tvshows'
                ? '/library/series'
                : undefined,
          items,
        })
      }
    }
    rows.value = out
    if (!heroItems.value.length && !rows.value.length && !resumeItems.value.length) {
      loadError.value = '片库暂时没有拉到内容'
    }
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <StreamShell>
    <!-- 骨架屏（录屏形态）：灰色圆角块 + 居中站标占位，尺寸与真实卡一致，
         内容到位后整体淡入替换，不白屏不跳动 -->
    <div v-if="loading" class="sl-skel" role="status" aria-label="正在加载">
      <div class="sl-skel__hero"><span class="sl-skel__logo">A</span></div>
      <div class="sl-skel__rowhead"><span class="sl-skel__bar" style="width: 120px"></span></div>
      <div class="sl-skel__track">
        <div v-for="i in 5" :key="`p${i}`" class="sl-skel__poster">
          <span class="sl-skel__logo">A</span>
        </div>
      </div>
      <div class="sl-skel__rowhead"><span class="sl-skel__bar" style="width: 88px"></span></div>
      <div class="sl-skel__track">
        <div v-for="i in 5" :key="`q${i}`" class="sl-skel__poster">
          <span class="sl-skel__logo">A</span>
        </div>
      </div>
    </div>
    <div v-else class="sl-content">
      <StreamHero :items="heroItems" @play="onHeroPlay" />

      <!-- 继续观看：16:9 卡约 340×191、圆角 12、无进度条（存档第 3 节）；
           录屏补充：标题带总数、卡面右上垃圾桶（隐藏）、进度文字行 -->
      <section v-if="resumeItems.length" class="sl-continue">
        <div class="sl-continue__head">
          <h2 class="sl-continue__title">
            继续观看 <span class="sl-continue__count">{{ resumeItems.length }} 部</span>
          </h2>
        </div>
        <div class="sl-continue__track">
          <RouterLink
            v-for="m in resumeItems"
            :key="m.id"
            class="sl-ccard"
            :to="{ name: 'media-detail', params: { id: m.id } }"
          >
            <span class="sl-ccard__thumb">
              <img
                v-if="resumeImage(m) && !brokenResumeThumbs.has(m.id)"
                :src="resumeImage(m)"
                alt=""
                loading="lazy"
                @error="brokenResumeThumbs.add(m.id)"
              />
              <Clapperboard
                v-else
                :size="30"
                :stroke-width="1.5"
                class="sl-ccard__fallback"
                aria-hidden="true"
              />
              <button
                type="button"
                class="sl-ccard__trash"
                :disabled="hidingId === m.id"
                aria-label="从继续观看移除"
                title="从继续观看移除"
                @click.prevent.stop="hideResume(m)"
              >
                <Trash2 :size="15" />
              </button>
            </span>
            <span class="sl-ccard__title">{{ resumeTitle(m) }}</span>
            <span class="sl-ccard__sub">{{ resumeSub(m) }}</span>
            <span v-if="resumeProgress(m)" class="sl-ccard__prog">{{ resumeProgress(m) }}</span>
          </RouterLink>
        </div>
      </section>

      <StreamRow
        v-for="row in rows"
        :key="row.key"
        :title="row.title"
        :count="row.count"
        :more-to="row.moreTo"
      >
        <StreamPosterCard
          v-for="item in row.items"
          :key="item.id"
          :title="item.title"
          :meta="item.meta"
          :rating="item.rating"
          :image="item.image"
          :to="`/media/${item.id}`"
        />
      </StreamRow>

      <div v-if="loadError && !rows.length" class="sl-error">
        {{ loadError }}<br />
        <button type="button" class="sl-retry" @click="load">重试</button>
      </div>
      <div style="height: 40px"></div>
    </div>

    <PlayerSchemeSheet v-model:open="pickerOpen" :title="pickerTitle" :item-id="pickerItemId" />
  </StreamShell>
</template>

<style scoped>
/* 内容淡入（骨架屏替换时不跳） */
.sl-content {
  animation: sl-in 0.45s ease both;
}
@keyframes sl-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
/* 骨架屏：灰块尺寸与真实卡一致（Hero 占位 + 海报行占位） */
.sl-skel {
  padding: 76px clamp(16px, 5vw, 96px) 40px;
}
.sl-skel__hero {
  height: min(560px, 62vh);
  border-radius: 16px;
  background: var(--uhd-s-card-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: sl-pulse 1.4s ease-in-out infinite;
}
.sl-skel__logo {
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
.sl-skel__rowhead {
  margin: 34px 0 14px;
}
.sl-skel__bar {
  display: block;
  height: 20px;
  border-radius: 6px;
  background: var(--uhd-s-card-soft);
}
.sl-skel__track {
  display: flex;
  gap: 25px;
  overflow: hidden;
}
.sl-skel__poster {
  flex: 0 0 260px;
  aspect-ratio: 2 / 3;
  border-radius: 13px;
  background: var(--uhd-s-card-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: sl-pulse 1.4s ease-in-out infinite;
}
@keyframes sl-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}
@media (max-width: 767px) {
  .sl-skel__poster {
    flex-basis: 152px;
  }
  .sl-skel__hero {
    height: 300px;
  }
}
.sl-continue {
  margin-top: 8px;
}
.sl-continue__head {
  padding: 0 clamp(16px, 5vw, 96px);
}
.sl-continue__title {
  margin: 0;
  font-size: 29px;
  font-weight: var(--uhd-fw-extrabold);
  color: var(--uhd-s-text);
}
.sl-continue__track {
  display: flex;
  gap: 20px;
  margin-top: 18px;
  padding: 0 clamp(16px, 5vw, 96px) 8px;
  overflow-x: auto;
  scrollbar-width: none;
}
.sl-continue__track::-webkit-scrollbar {
  display: none;
}
.sl-ccard {
  flex: 0 0 340px;
  width: 340px;
  text-decoration: none;
  color: var(--uhd-s-text);
}
.sl-ccard__thumb {
  position: relative;
  display: block;
  aspect-ratio: 16 / 9;
  border-radius: 12px;
  overflow: hidden;
  background: var(--uhd-s-card-soft);
}
/* 卡面右上垃圾桶（录屏形态）：半透明深色圆钮 */
.sl-ccard__trash {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.45);
  color: #ffffff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.sl-ccard__trash:disabled {
  opacity: 0.5;
}
.sl-continue__count {
  font-size: 14px;
  font-weight: var(--uhd-fw-regular);
  color: var(--uhd-s-text-2);
}
.sl-ccard__prog {
  display: block;
  margin-top: 2px;
  font-size: 13px;
  color: var(--uhd-s-text-2);
}
.sl-ccard__thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
/* 无图 / 加载失败占位（与详情页、海报卡同款 Clapperboard，居中落在底色上） */
.sl-ccard__fallback {
  position: absolute;
  inset: 0;
  margin: auto;
  color: var(--uhd-s-text-2);
}
.sl-ccard__title {
  display: block;
  margin-top: 12px;
  font-size: 17px;
  font-weight: var(--uhd-fw-bold);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sl-ccard__sub {
  display: block;
  margin-top: 3px;
  font-size: 14px;
  color: var(--uhd-s-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sl-error {
  margin: 60px clamp(16px, 5vw, 96px);
  font-size: 15px;
  color: var(--uhd-s-text-2);
  text-align: center;
}
.sl-retry {
  margin-top: 12px;
  padding: 8px 22px;
  border-radius: 999px;
  border: 1px solid var(--uhd-s-border);
  background: var(--uhd-s-pill-active-bg);
  color: var(--uhd-s-text);
  font-size: 14px;
  cursor: pointer;
}
@media (max-width: 767px) {
  .sl-continue__title {
    font-size: 22px;
  }
  /* D2-06 复核：录屏帧实测原站继续观看卡约占屏宽 55%（≈216pt@390），取 220 */
  .sl-ccard {
    flex-basis: 220px;
    width: 220px;
  }
}
</style>
