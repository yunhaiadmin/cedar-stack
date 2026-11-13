<template>
  <PlayerSheet :open="open" :title="sheetTitle" @update:open="onOpenChange">
    <!-- 主菜单 -->
    <template v-if="view === 'main'">
      <p class="wp-sec">片源</p>
      <div class="wp-list">
        <div class="wp-row wp-row-static">
          <span>开始播放</span>
          <span class="wp-row-val">{{ currentSourceLabel || '—' }}</span>
        </div>
        <button type="button" class="wp-row" @click="view = 'versions'">
          <span>切换版本</span>
          <ChevronRight :size="17" class="wp-dim" />
        </button>
        <button v-if="episodes.length > 1" type="button" class="wp-row" @click="view = 'episodes'">
          <span>选集</span>
          <ChevronRight :size="17" class="wp-dim" />
        </button>
      </div>

      <p class="wp-sec">播放</p>
      <div class="wp-list">
        <button type="button" class="wp-row" @click="view = 'speed'">
          <span>播放速度</span>
          <span class="wp-row-val">{{ rate }}x <ChevronRight :size="15" class="wp-dim-i" /></span>
        </button>
      </div>

      <p class="wp-sec">工具</p>
      <div class="wp-list">
        <button type="button" class="wp-row" @click="emit('screenshot')">
          <span>截图保存</span>
          <Camera :size="17" class="wp-dim" />
        </button>
        <button v-if="pipSupported" type="button" class="wp-row" @click="emit('pip')">
          <span>小窗播放</span>
          <PictureInPicture2 :size="17" class="wp-dim" />
        </button>
        <button type="button" class="wp-row" @click="view = 'info'">
          <span>播放信息</span>
          <ChevronRight :size="17" class="wp-dim" />
        </button>
      </div>
    </template>

    <!-- 切换版本 -->
    <template v-else-if="view === 'versions'">
      <div class="wp-list">
        <button
          v-for="s in sources"
          :key="s.id"
          type="button"
          class="wp-row"
          @click="emit('select-source', s.id)"
        >
          <span class="wp-row-main">
            <Check v-if="s.id === currentSourceId" :size="17" class="wp-check" />
            <span v-else class="wp-check-ph" />
            {{ s.label }}
          </span>
          <span v-if="s.meta" class="wp-row-val">{{ s.meta }}</span>
        </button>
        <p v-if="sources.length === 0" class="wp-empty">没有其他版本。</p>
      </div>
    </template>

    <!-- 选集 -->
    <template v-else-if="view === 'episodes'">
      <div class="wp-list">
        <button
          v-for="ep in episodes"
          :key="ep.id"
          type="button"
          class="wp-row"
          @click="emit('select-episode', ep.id)"
        >
          <span class="wp-row-main">
            <Check v-if="ep.id === currentEpisodeId" :size="17" class="wp-check" />
            <span v-else class="wp-check-ph" />
            {{ ep.label }}
          </span>
        </button>
      </div>
    </template>

    <!-- 播放速度 -->
    <template v-else-if="view === 'speed'">
      <div class="wp-speed-grid">
        <button
          v-for="r in RATES"
          :key="r"
          type="button"
          :class="{ on: rate === r }"
          @click="emit('update:rate', r)"
        >
          {{ r }}x
        </button>
      </div>
      <p class="wp-label wp-label-row">
        <span>自定义速度</span>
        <span class="wp-val">{{ rate.toFixed(2) }}x</span>
      </p>
      <input
        class="wp-range"
        type="range"
        min="0.5"
        max="2"
        step="0.05"
        :value="rate"
        @input="emit('update:rate', Number(($event.target as HTMLInputElement).value))"
      />
    </template>

    <!-- 播放信息 -->
    <template v-else-if="view === 'info'">
      <dl class="wp-info">
        <template v-for="line in infoLines" :key="line.label">
          <dt>{{ line.label }}</dt>
          <dd>{{ line.value }}</dd>
        </template>
      </dl>
    </template>
  </PlayerSheet>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Camera, Check, ChevronRight, PictureInPicture2 } from 'lucide-vue-next'
import PlayerSheet from './PlayerSheet.vue'

export interface SourceOption {
  id: string
  label: string
  meta?: string
}
export interface EpisodeOption {
  id: string
  label: string
}
export interface InfoLine {
  label: string
  value: string
}

const props = defineProps<{
  open: boolean
  sources: SourceOption[]
  currentSourceId: string
  currentSourceLabel: string
  episodes: EpisodeOption[]
  currentEpisodeId: string
  rate: number
  infoLines: InfoLine[]
  /** 浏览器支持标准 PiP（requestPictureInPicture）时才渲染小窗行，不支持不许留假钮 */
  pipSupported: boolean
}>()
const emit = defineEmits<{
  'update:open': [boolean]
  'update:rate': [number]
  'select-source': [string]
  'select-episode': [string]
  'screenshot': []
  'pip': []
}>()

const view = ref<'main' | 'versions' | 'episodes' | 'speed' | 'info'>('main')
const RATES = [0.5, 0.75, 1, 1.25, 1.5, 2]

const sheetTitle = computed(() => {
  switch (view.value) {
    case 'versions':
      return '切换版本'
    case 'episodes':
      return '选集'
    case 'speed':
      return '播放速度'
    case 'info':
      return '播放信息'
    default:
      return '播放设置'
  }
})

watch(
  () => props.open,
  (o) => {
    if (o) view.value = 'main'
  },
)

function onOpenChange(v: boolean): void {
  if (!v && view.value !== 'main') {
    view.value = 'main'
    return
  }
  emit('update:open', v)
}
</script>

<style scoped>
.wp-sec {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.5);
  margin: 18px 0 6px;
}
.wp-sec:first-child {
  margin-top: 6px;
}
.wp-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.wp-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  border: none;
  background: transparent;
  color: #fff;
  font-size: 15px;
  text-align: left;
  padding: 12px 10px;
  border-radius: 12px;
  cursor: pointer;
}
button.wp-row:hover {
  background: rgba(255, 255, 255, 0.06);
}
.wp-row-static {
  cursor: default;
}
.wp-row-main {
  display: flex;
  align-items: center;
  gap: 8px;
}
.wp-row-val {
  color: rgba(255, 255, 255, 0.55);
  font-size: 13.5px;
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
.wp-dim {
  color: rgba(255, 255, 255, 0.4);
}
.wp-dim-i {
  color: rgba(255, 255, 255, 0.4);
  vertical-align: -2px;
}
.wp-check {
  flex: none;
}
.wp-check-ph {
  width: 17px;
  flex: none;
}
.wp-empty {
  color: rgba(255, 255, 255, 0.45);
  font-size: 13.5px;
  padding: 8px 10px;
  margin: 0;
}
.wp-speed-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-top: 10px;
}
.wp-speed-grid button {
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(255, 255, 255, 0.05);
  color: #fff;
  font-size: 15px;
  padding: 12px 0;
  border-radius: 14px;
  cursor: pointer;
}
.wp-speed-grid button.on {
  background: rgba(255, 255, 255, 0.92);
  color: #111;
  font-weight: 700;
}
.wp-label {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.55);
  margin: 18px 0 8px;
}
.wp-label-row {
  display: flex;
  justify-content: space-between;
}
.wp-val {
  color: #fff;
  font-weight: 600;
}
.wp-range {
  width: 100%;
  accent-color: #fff;
}
.wp-info {
  margin: 8px 0 0;
  display: grid;
  grid-template-columns: 86px 1fr;
  row-gap: 10px;
  font-size: 14px;
}
.wp-info dt {
  color: rgba(255, 255, 255, 0.5);
}
.wp-info dd {
  margin: 0;
  color: #fff;
  word-break: break-all;
}
</style>
