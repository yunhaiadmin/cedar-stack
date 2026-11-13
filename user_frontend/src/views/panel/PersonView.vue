<template>
  <StreamShell>
    <div class="pv-wrap">
      <p class="pv-eyebrow">演职人员</p>

      <div v-if="error" class="pv-error" role="alert">
        {{ error }}<br />
        <button type="button" class="pv-retry" :disabled="loading" @click="load">{{ loading ? '正在重试…' : '重试' }}</button>
      </div>
      <div v-else-if="loading" class="pv-loading" role="status">正在加载…</div>

      <template v-else-if="detail">
        <header class="pv-head">
          <span class="pv-avatar">
            <img v-if="detail.image && !avatarBroken" :src="detail.image" alt="" @error="avatarBroken = true" />
            <template v-else>{{ detail.name.charAt(0) }}</template>
          </span>
          <div class="pv-head__main">
            <h1 class="pv-h1">{{ detail.name }}</h1>
            <p class="pv-sub">参演 {{ detail.works.length }} 部作品</p>
          </div>
          <button
            type="button"
            class="pv-fav"
            :class="{ 'pv-fav--on': detail.is_favorite }"
            :disabled="toggling"
            :aria-pressed="detail.is_favorite"
            @click="toggleFav"
          >
            <Star :size="16" :stroke-width="2" :fill="detail.is_favorite ? 'currentColor' : 'none'" />
            {{ detail.is_favorite ? '已收藏' : '收藏' }}
          </button>
        </header>

        <h2 class="pv-sec">参演作品</h2>
        <div v-if="detail.works.length === 0" class="pv-empty">
          暂无可展示的参演作品
        </div>
        <div v-else class="pv-grid">
          <article v-for="w in detail.works" :key="w.id" class="pv-card">
            <div class="pv-poster">
              <img
                v-if="posterSrc(w)"
                class="pv-img"
                :src="posterSrc(w)"
                alt=""
                loading="lazy"
                decoding="async"
                @error="onPosterError(w.id)"
              />
              <Clapperboard v-else :size="28" :stroke-width="1.5" class="pv-fallback" aria-hidden="true" />
              <RouterLink
                class="pv-link"
                :to="{ name: 'media-detail', params: { id: w.id } }"
                :aria-label="`查看《${w.name}》详情`"
              />
            </div>
            <div class="pv-body">
              <RouterLink class="pv-name" :title="w.name" :to="{ name: 'media-detail', params: { id: w.id } }">{{ w.name }}</RouterLink>
              <div class="pv-meta">{{ metaLine(w) }}</div>
              <div v-if="w.role" class="pv-meta">饰 {{ w.role }}</div>
            </div>
          </article>
        </div>
      </template>
    </div>
  </StreamShell>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { Clapperboard, Star } from 'lucide-vue-next'
import { embyApi, type PortalPersonDetail, type PortalPersonWork } from '@/api'
import { imageAuthUrl } from '@/api/emby'
import { useToast } from '@/composables/useToast'
import StreamShell from '@/components/stream/StreamShell.vue'

const route = useRoute()
const toast = useToast()

const loading = ref(false)
const error = ref('')
const detail = ref<PortalPersonDetail | null>(null)
const toggling = ref(false)
const avatarBroken = ref(false)
const brokenPosters = ref<Set<string>>(new Set())

function errText(err: unknown): string {
  const msg = (err as { userMessage?: string })?.userMessage
  if (msg) return msg
  if (err instanceof Error) return err.message
  return '加载失败'
}

function posterSrc(w: PortalPersonWork): string {
  if (brokenPosters.value.has(w.id)) return ''
  const url = w.poster_url
  return typeof url === 'string' && url.trim() !== '' ? imageAuthUrl(url, 456) : ''
}

function onPosterError(id: string) {
  brokenPosters.value = new Set(brokenPosters.value).add(id)
}

function metaLine(w: PortalPersonWork): string {
  const parts: string[] = []
  parts.push(w.type === 'movie' ? '电影' : w.type === 'series' ? '剧集' : w.type)
  if (w.year) parts.push(String(w.year))
  if (w.rating) parts.push(`★ ${w.rating.toFixed(1)}`)
  return parts.join(' · ')
}

async function load() {
  const key = String(route.params.key || '')
  if (!key) {
    error.value = '人员键缺失'
    return
  }
  loading.value = true
  error.value = ''
  avatarBroken.value = false
  try {
    detail.value = await embyApi.getPersonDetail(key)
  } catch (err) {
    detail.value = null
    error.value = `这位演职人员没加载出来：${errText(err)}`
  } finally {
    loading.value = false
  }
}

async function toggleFav() {
  if (!detail.value || toggling.value) return
  toggling.value = true
  try {
    // 用详情返回的规范键切换，与后端收藏判定同一口径
    const res = await embyApi.togglePersonFavorite(detail.value.key)
    detail.value = { ...detail.value, is_favorite: res.is_favorite }
    toast.success(res.is_favorite ? `已收藏 ${detail.value.name}` : `已取消收藏 ${detail.value.name}`)
  } catch (err) {
    toast.error(`操作失败：${errText(err)}`)
  } finally {
    toggling.value = false
  }
}

watch(() => route.params.key, load)
onMounted(load)
</script>

<style scoped>
.pv-wrap {
  padding: 26px clamp(16px, 5vw, 96px) 56px;
}
.pv-eyebrow {
  margin: 0;
  font-size: 14px;
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-s-text-2);
}
.pv-error,
.pv-loading {
  margin-top: 36px;
  font-size: 15px;
  color: var(--uhd-s-text-2);
}
.pv-retry {
  margin-top: 12px;
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
.pv-head {
  display: flex;
  align-items: center;
  gap: 22px;
  margin-top: 18px;
}
.pv-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 96px;
  height: 96px;
  flex: 0 0 96px;
  border-radius: 50%;
  overflow: hidden;
  background: var(--uhd-s-card-soft);
  color: var(--uhd-s-text-2);
  font-size: 34px;
  font-weight: var(--uhd-fw-bold);
}
.pv-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.pv-head__main {
  flex: 1;
  min-width: 0;
}
.pv-h1 {
  margin: 0;
  font-size: 44px;
  font-weight: var(--uhd-fw-black);
  line-height: 1.1;
  letter-spacing: -0.01em;
  color: var(--uhd-s-text);
}
.pv-sub {
  margin: 8px 0 0;
  font-size: 15px;
  color: var(--uhd-s-text-2);
}
.pv-fav {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 44px;
  padding: 0 22px;
  border-radius: 22px;
  border: 1px solid var(--uhd-s-pill-border);
  background: var(--uhd-s-pill-bg);
  color: var(--uhd-s-text);
  font-size: 15px;
  font-weight: var(--uhd-fw-semibold);
  font-family: inherit;
  cursor: pointer;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}
.pv-fav--on {
  background: var(--uhd-s-pill-active-bg);
  color: var(--uhd-s-pill-active-text);
}
.pv-fav:disabled {
  opacity: 0.55;
  cursor: wait;
}
.pv-sec {
  margin: 40px 0 0;
  font-size: 22px;
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-s-text);
}
.pv-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 230px;
  margin-top: 26px;
  padding: 24px;
  border: 1.5px dashed var(--uhd-s-border);
  border-radius: 12px;
  font-size: 15px;
  color: var(--uhd-s-text-2);
  text-align: center;
}
/* 作品网格与收藏页同款：7 列口径，窄屏递减 */
.pv-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 34px 21px;
  margin-top: 26px;
}
@media (max-width: 1599px) {
  .pv-grid { grid-template-columns: repeat(6, 1fr); }
}
@media (max-width: 1279px) {
  .pv-grid { grid-template-columns: repeat(5, 1fr); }
}
@media (max-width: 1023px) {
  .pv-grid { grid-template-columns: repeat(4, 1fr); }
}
@media (max-width: 767px) {
  .pv-grid { grid-template-columns: repeat(3, 1fr); gap: 22px 12px; }
  .pv-h1 { font-size: 32px; }
  .pv-avatar { width: 72px; height: 72px; flex-basis: 72px; font-size: 26px; }
}
.pv-card {
  min-width: 0;
}
.pv-poster {
  position: relative;
  aspect-ratio: 2 / 3;
  border-radius: 13px;
  background: var(--uhd-s-card-soft);
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}
.pv-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.pv-fallback {
  color: var(--uhd-s-text-2);
}
.pv-link {
  position: absolute;
  inset: 0;
  z-index: 1;
}
.pv-body {
  padding-top: 12px;
}
.pv-name {
  display: block;
  font-size: 17px;
  font-weight: var(--uhd-fw-bold);
  line-height: 1.3;
  color: var(--uhd-s-text);
  text-decoration: none;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pv-meta {
  margin-top: 4px;
  font-size: 14px;
  color: var(--uhd-s-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
