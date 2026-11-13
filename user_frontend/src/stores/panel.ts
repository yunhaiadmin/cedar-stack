import { defineStore } from 'pinia'
import { ref } from 'vue'
import { panelApi, humanizeError } from '@/composables/usePanelApi'

/**
 * v4 面板数据 store（PR-2 地基）
 * - 内存缓存 TTL 60s（方案 §2.4）
 * - 接口失败时各区块显示纸色占位，不许白屏/转圈（由视图层处理 error 状态）
 * - 发现页退役后只剩收藏家 / 服务器两个数据面
 */

const TTL_MS = 60_000

interface CacheEntry<T = unknown> {
  data: T | null
  error: string | null
  loading: boolean
  at: number
}

function freshEntry<T>(): CacheEntry<T> {
  return { data: null, error: null, loading: false, at: 0 }
}

export const usePanelStore = defineStore('panel', () => {
  const collector = ref<CacheEntry>(freshEntry())
  const server = ref<CacheEntry>(freshEntry())

  async function load<T>(entry: CacheEntry<T>, fetcher: () => Promise<T>, force = false) {
    const now = Date.now()
    if (!force && entry.data !== null && now - entry.at < TTL_MS) return
    if (entry.loading && !force) return
    entry.loading = true
    entry.error = null
    try {
      entry.data = await fetcher()
      entry.at = Date.now()
    } catch (e) {
      entry.error = humanizeError(e)
    } finally {
      entry.loading = false
    }
  }

  const loadCollector = (force = false) => load(collector.value, panelApi.collector, force)
  const loadServer = (force = false) => load(server.value, panelApi.server, force)

  /** 账号切换时清空全部面板缓存：防止新账号看到上一个账号的数据（TTL 内的串号问题） */
  function reset() {
    collector.value = freshEntry()
    server.value = freshEntry()
  }

  return {
    collector, server,
    loadCollector, loadServer,
    reset,
  }
})
