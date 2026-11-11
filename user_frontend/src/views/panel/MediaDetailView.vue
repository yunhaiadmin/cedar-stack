<template>
  <div class="v4-scope">
    <div class="v4-page">
      <div class="v4-col-top">
        <h1>
          <button type="button" class="v4-back" aria-label="返回" @click="goBack">‹</button>
          {{ item?.Name ?? '详情' }}
        </h1>
      </div>

      <!-- 顶层错误卡 -->
      <div v-if="error" class="v4-sec">
        <div class="v4-err" role="alert">{{ error }}<br><button class="v4-retry" :disabled="loading" @click="load">{{ loading ? '正在重试…' : '重试' }}</button></div>
      </div>

      <div v-else-if="loading && !item" class="v4-sec">
        <div class="v4-skeleton" aria-hidden="true" style="height: 180px"></div>
      </div>

      <template v-else-if="item">
        <section class="v4-sec">
          <div class="detail-head">
            <div class="detail-poster">
              <img v-if="poster && !posterBroken" :src="poster" alt="" class="detail-img" @error="posterBroken = true" />
              <Clapperboard v-else :size="30" :stroke-width="1.5" class="detail-fallback" aria-hidden="true" />
            </div>
            <div class="detail-info">
              <div class="detail-name">{{ item.Name }}</div>
              <div class="detail-meta">{{ metaLine }}</div>
              <p v-if="item.Overview" class="detail-overview">{{ item.Overview }}</p>
            </div>
          </div>
          <p class="detail-note">播放请在播放器里打开；本页看这部片齐不齐，缺的集可以直接求补，补齐后会通知你。</p>
        </section>

        <section v-if="item.Type === 'Series'" class="v4-sec">
          <div class="v4-sec-title">季与集</div>
          <div class="v4-sec-sub">灰色的集是还没入库的，点「求补这集」即可求补</div>
          <SeriesEpisodeList :series-id="item.Id" :tmdb-id="tmdbId" />
        </section>
        <section v-else class="v4-sec">
          <div class="v4-empty">这部是单片，没有季集列表</div>
        </section>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 条目详情页（/media/:id）：本页只承接「信息 + 季集齐全度」——
 * 播放走第三方播放器（本站不放播放入口，与收藏页同一口径）；
 * 剧集的季/集列表与缺集求补在 SeriesEpisodeList 里。
 * 老 /media/:id 曾在 2026-10-13 清理时删除，本页是按新口径复活：
 * 只做季集与缺集，不恢复媒体浏览/播放页。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Clapperboard } from 'lucide-vue-next'
import { embyApi as embyProtocol, posterUrl, type EmbyItem } from '@/api/emby'
import SeriesEpisodeList from '@/components/panel/SeriesEpisodeList.vue'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const error = ref('')
const item = ref<EmbyItem | null>(null)
const posterBroken = ref(false)

const itemId = computed(() => String(route.params.id || ''))

const tmdbId = computed<string | null>(() => {
  const ids = item.value?.ProviderIds || {}
  return ids.Tmdb || ids.TMDB || null
})

const poster = computed(() => (item.value ? posterUrl(item.value, 240) : ''))

const metaLine = computed(() => {
  const it = item.value
  if (!it) return ''
  const parts: string[] = []
  parts.push(it.Type === 'Series' ? '剧集' : it.Type === 'Movie' ? '电影' : it.Type)
  if (it.ProductionYear) parts.push(String(it.ProductionYear))
  if (it.CommunityRating) parts.push(`★ ${it.CommunityRating.toFixed(1)}`)
  if (it.Genres?.length) parts.push(it.Genres.slice(0, 3).join(' / '))
  return parts.join(' · ')
})

function errText(err: unknown): string {
  const msg = (err as { userMessage?: string })?.userMessage
  if (msg) return msg
  if (err instanceof Error) return err.message
  return '加载失败'
}

async function load() {
  if (!itemId.value) {
    error.value = '条目地址不对'
    return
  }
  loading.value = true
  error.value = ''
  posterBroken.value = false
  try {
    item.value = await embyProtocol.getItem(itemId.value)
  } catch (err) {
    item.value = null
    error.value = `详情没加载出来：${errText(err)}`
  } finally {
    loading.value = false
  }
}

function goBack() {
  if (window.history.length > 1) router.back()
  else void router.push('/')
}

onMounted(() => {
  void load()
})

watch(itemId, () => {
  item.value = null
  void load()
})
</script>

<style scoped>
.v4-back {
  border: none;
  background: none;
  font: inherit;
  color: inherit;
  font-size: 28px;
  line-height: 1;
  margin-right: 4px;
  padding: 0;
  cursor: pointer;
  vertical-align: baseline;
}
.detail-head {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}
.detail-poster {
  flex: 0 0 92px;
  width: 92px;
  aspect-ratio: 2 / 3;
  border: 1px solid var(--paper2, #e3e3e2);
  border-radius: 10px;
  background: var(--au-bg-soft, #f4f3f0);
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}
.detail-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.detail-fallback {
  color: var(--au-text-3, #a8a29a);
}
.detail-info {
  flex: 1 1 auto;
  min-width: 0;
}
.detail-name {
  font-size: 17px;
  font-weight: 700;
  line-height: 1.35;
  color: var(--ink, #27251e);
}
.detail-meta {
  margin-top: 4px;
  font-size: 12.5px;
  color: var(--ink2, #6f6a5e);
}
.detail-overview {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink2, #6f6a5e);
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.detail-note {
  margin: 12px 0 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--ink2, #6f6a5e);
}
</style>
