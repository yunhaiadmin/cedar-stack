import { computed, ref } from 'vue'
import { checkinApi } from '@/api/economy'

/**
 * 探险任务：今日任务（拆包裹 / 读信）的状态与跳转；签到状态只作数据
 * 回退（能量 / 连签展示），签到页已删除、无跳转。
 * 从 WorldMap.vue 拆出（原内联逻辑，行为不变）。
 *
 * 包裹与信件的数据归属调用方（index.vue 持有 parcel/letter 状态）：
 * - params.parcelCount / params.unsealedCount / params.letterRead / params.hasLetter
 * - params.openParcels / params.openLetter：任务按钮的打开动作
 */
export interface WorldQuest {
  key: string
  label: string
  done: boolean
  rw: string
}

interface CheckinStatus {
  checked_today?: boolean
  base_points?: number
  points?: number
  streak?: number
}

export function useWorldQuests(params: {
  parcelCount: () => number
  unsealedCount: () => number
  hasLetter: () => boolean
  letterRead: () => boolean
  openParcels: () => void
  openLetter: () => void
}) {
  const checkin = ref<CheckinStatus | null>(null)

  async function loadCheckin() {
    try {
      checkin.value = (await checkinApi.status()) as unknown as CheckinStatus
    } catch {
      checkin.value = null
    }
  }

  const checkinDone = computed(() => checkin.value?.checked_today === true)

  const quests = computed<WorldQuest[]>(() => {
    const parcelsDone = params.parcelCount() > 0 && params.unsealedCount() >= 1
    const read = params.letterRead()
    return [
      {
        key: 'parcels',
        label: '拆一个封存包裹',
        done: parcelsDone,
        rw: params.parcelCount() > 0 ? `${params.unsealedCount()}/${params.parcelCount()}` : '今日无新包裹',
      },
      {
        key: 'letter',
        label: '读昨天的信',
        done: read,
        rw: !params.hasLetter() ? '暂无信件' : read ? '已读' : '0/1',
      },
    ]
  })

  const questPercent = computed(() => {
    const done = quests.value.filter((q) => q.done).length
    return quests.value.length ? (done / quests.value.length) * 100 : 0
  })
  const questDoneCount = computed(() => quests.value.filter((q) => q.done).length)
  const questNextLabel = computed(() => quests.value.find((q) => !q.done)?.label ?? '')

  function onQuest(key: string) {
    if (key === 'parcels') {
      params.openParcels()
      return
    }
    if (key === 'letter') params.openLetter()
  }

  return {
    checkin,
    checkinDone,
    quests,
    questPercent,
    questDoneCount,
    questNextLabel,
    onQuest,
    loadCheckin,
  }
}
