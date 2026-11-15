/**
 * 官网片库数字（GET /api/site/library-stats）
 *
 * 官网首页片库数据行的四个实时数：影视作品 / 剧集单集 / 条目总计 /
 * 启用媒体库数，后端按 ORM 可见口径（排除软删除与合并副本）统计、
 * 缓存 10 分钟。此前这行是写死在 LandingView.vue 里的一次性快照，
 * 数字随入库漂移失真，故改读接口。
 *
 * 回落：初始值就是 2026-11-15 的生产快照。拉取失败（后端没起、老版本
 * 后端没有这个端点）或后端回全 0（DB 故障兜底）时保持快照，官网不开天窗。
 */
import { reactive } from 'vue'
import { libraryStatsApi, type LibraryStats } from '@/api'

export const libraryStats = reactive<LibraryStats>({
  works: 22482,
  episodes: 294126,
  total_items: 332085,
  libraries: 11,
})

let initialized = false

export async function initLibraryStats(): Promise<void> {
  if (initialized) return
  initialized = true
  try {
    const data = await libraryStatsApi.get()
    const works = Number(data?.works) || 0
    const episodes = Number(data?.episodes) || 0
    const totalItems = Number(data?.total_items) || 0
    const libraries = Number(data?.libraries) || 0
    /* 四数任一非正都不是真统计（全 0 是后端故障兜底），保持快照 */
    if (works > 0 && episodes > 0 && totalItems > 0 && libraries > 0) {
      libraryStats.works = works
      libraryStats.episodes = episodes
      libraryStats.total_items = totalItems
      libraryStats.libraries = libraries
    }
  } catch {
    /* 保持快照值 */
  }
}
