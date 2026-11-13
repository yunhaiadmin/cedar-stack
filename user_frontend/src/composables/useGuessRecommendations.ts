/**
 * 猜你想看（搜索浮层空输入态推荐，友站口径 5 条）——桌面顶栏与手机
 * 全屏搜索层共用同一份数据与排序，信号全部取现行只读口、不新造：
 * 1) 在播优先：门户续看 GET /api/user/emby/resume（剧已按剧聚合、
 *    用户藏掉的已剔，与片库首页「继续观看」同一口）；
 * 2) 最近添加补齐：协议口 Latest（后端只出 movie/series 且已过身份门
 *    与副本去重）；
 * 3) 高分补齐：协议口 Items 按 CommunityRating 降序（无评分条目在
 *    前端再滤一层，不让空评分占位）。
 * 按序合并、按条目 id 去重、取满 5 条；某一路拉不到或为空就用其余路
 * 补，全空则不渲染。不新建任何用户行为追踪。
 */
import { ref } from 'vue'
import { embyApi as embyProtocol, imageAuthUrl, imageUrl } from '@/api/emby'
import { embyApi as portalApi } from '@/api'

export interface GuessCard {
  id: string
  title: string
  image: string
  meta: string
}

/** 模块级单例：顶栏与底坞同时挂载也只拉一轮；三路全空时不落盘，
 *  下次打开搜索再试（新入库/新进度能及时进来）。 */
const guessItems = ref<GuessCard[]>([])
let guessLoading = false

function guessMeta(year?: number | null, rating?: number | null): string {
  const parts: string[] = []
  if (year) parts.push(String(year))
  if (rating) parts.push(`★ ${rating.toFixed(1)}`)
  return parts.join(' · ')
}

async function loadGuess(): Promise<void> {
  if (guessLoading || guessItems.value.length) return
  guessLoading = true
  try {
    const [resume, latest, rated] = await Promise.allSettled([
      portalApi.getResume(5),
      embyProtocol.getLatest(10),
      embyProtocol.getItems({
        includeTypes: ['Movie', 'Series'],
        sortBy: 'CommunityRating',
        sortOrder: 'Descending',
        limit: 20,
        recursive: true,
      }),
    ])
    const seen = new Set<string>()
    const out: GuessCard[] = []
    const push = (card: GuessCard) => {
      if (!card.id || seen.has(card.id) || out.length >= 5) return
      seen.add(card.id)
      out.push(card)
    }
    if (resume.status === 'fulfilled') {
      for (const m of resume.value?.items || []) {
        push({
          id: m.id,
          title: m.series_name || m.name,
          image: m.poster_url ? imageAuthUrl(m.poster_url, 300) : '',
          meta: guessMeta(m.year, m.rating),
        })
      }
    }
    if (latest.status === 'fulfilled') {
      for (const it of latest.value || []) {
        push({
          id: it.Id,
          title: it.Name,
          image: it.ImageTags?.Primary ? imageUrl(it.Id, 'Primary', 300) : '',
          meta: guessMeta(it.ProductionYear, it.CommunityRating),
        })
      }
    }
    if (rated.status === 'fulfilled') {
      for (const it of rated.value?.Items || []) {
        if (!it.CommunityRating) continue
        push({
          id: it.Id,
          title: it.Name,
          image: it.ImageTags?.Primary ? imageUrl(it.Id, 'Primary', 300) : '',
          meta: guessMeta(it.ProductionYear, it.CommunityRating),
        })
      }
    }
    guessItems.value = out
  } finally {
    guessLoading = false
  }
}

export function useGuessRecommendations() {
  return { guessItems, loadGuess }
}
