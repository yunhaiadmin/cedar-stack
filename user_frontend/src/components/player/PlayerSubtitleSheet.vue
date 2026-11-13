<template>
  <PlayerSheet :open="open" title="字幕" @update:open="emit('update:open', $event)">
    <div class="wp-seg" role="tablist" aria-label="字幕槽位">
      <button type="button" :class="{ on: slot === 'primary' }" @click="slot = 'primary'">主字幕</button>
      <button type="button" :class="{ on: slot === 'secondary' }" @click="slot = 'secondary'">副字幕</button>
    </div>

    <div class="wp-list">
      <button type="button" class="wp-row" @click="pick(null)">
        <span>关闭字幕</span>
        <Check v-if="current === null" :size="17" class="wp-check" />
      </button>
      <button
        v-for="t in tracks"
        :key="t.index"
        type="button"
        class="wp-row"
        @click="pick(t.index)"
      >
        <span class="wp-row-main">
          <Check v-if="current === t.index" :size="17" class="wp-check" />
          <span v-else class="wp-check-ph" />
          {{ t.label }}
        </span>
        <span v-if="t.language" class="wp-code">{{ t.language.toUpperCase() }}</span>
      </button>
      <p v-if="tracks.length === 0" class="wp-empty">这个版本没有可用的字幕轨。</p>
    </div>

    <div class="wp-group">
      <p class="wp-label">字幕延迟</p>
      <div class="wp-stepper">
        <button type="button" aria-label="减少延迟" @click="bumpDelay(-100)">−</button>
        <span>{{ delayLabel }}</span>
        <button type="button" aria-label="增加延迟" @click="bumpDelay(100)">+</button>
      </div>
    </div>

    <div class="wp-group">
      <p class="wp-label">字号</p>
      <div class="wp-seg">
        <button
          v-for="s in SIZES"
          :key="s.value"
          type="button"
          :class="{ on: fontSize === s.value }"
          @click="emit('update:fontSize', s.value)"
        >
          {{ s.label }}
        </button>
      </div>
    </div>
  </PlayerSheet>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Check } from 'lucide-vue-next'
import PlayerSheet from './PlayerSheet.vue'

export interface SubtitleTrackOption {
  index: number
  label: string
  language?: string
}
export type SubtitleFontSize = 'default' | 'small' | 'medium' | 'large'

const props = defineProps<{
  open: boolean
  tracks: SubtitleTrackOption[]
  primary: number | null
  secondary: number | null
  delayMs: number
  fontSize: SubtitleFontSize
}>()
const emit = defineEmits<{
  'update:open': [boolean]
  'update:primary': [number | null]
  'update:secondary': [number | null]
  'update:delayMs': [number]
  'update:fontSize': [SubtitleFontSize]
}>()

const slot = ref<'primary' | 'secondary'>('primary')
const delayLabel = computed(() => {
  const s = props.delayMs / 1000
  if (s === 0) return '0s'
  return `${s > 0 ? '+' : ''}${s.toFixed(1)}s`
})
const current = computed(() => (slot.value === 'primary' ? props.primary : props.secondary))
const SIZES: Array<{ value: SubtitleFontSize; label: string }> = [
  { value: 'default', label: '默认' },
  { value: 'small', label: '小' },
  { value: 'medium', label: '中' },
  { value: 'large', label: '大' },
]

function pick(index: number | null): void {
  if (slot.value === 'primary') emit('update:primary', index)
  else emit('update:secondary', index)
}
function bumpDelay(delta: number): void {
  emit('update:delayMs', Math.max(-5000, Math.min(5000, props.delayMs + delta)))
}
</script>

<style scoped>
.wp-seg {
  display: flex;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 999px;
  padding: 3px;
  gap: 3px;
  margin-top: 6px;
}
.wp-seg button {
  flex: 1;
  border: none;
  background: transparent;
  color: rgba(255, 255, 255, 0.65);
  font-size: 14px;
  padding: 8px 0;
  border-radius: 999px;
  cursor: pointer;
}
.wp-seg button.on {
  background: rgba(255, 255, 255, 0.92);
  color: #111;
  font-weight: 600;
}
.wp-list {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
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
  padding: 11px 10px;
  border-radius: 12px;
  cursor: pointer;
}
.wp-row:hover {
  background: rgba(255, 255, 255, 0.06);
}
.wp-row-main {
  display: flex;
  align-items: center;
  gap: 8px;
}
.wp-check {
  color: #fff;
  flex: none;
}
.wp-check-ph {
  width: 17px;
  flex: none;
}
.wp-code {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.22);
  border-radius: 6px;
  padding: 2px 6px;
}
.wp-empty {
  color: rgba(255, 255, 255, 0.45);
  font-size: 13.5px;
  padding: 8px 10px;
  margin: 0;
}
.wp-group {
  margin-top: 18px;
}
.wp-label {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.55);
  margin: 0 0 8px;
}
.wp-stepper {
  display: flex;
  align-items: center;
  gap: 12px;
}
.wp-stepper button {
  width: 44px;
  height: 40px;
  border-radius: 999px;
  border: none;
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
  font-size: 18px;
  cursor: pointer;
}
.wp-stepper span {
  flex: 1;
  text-align: center;
  font-size: 15px;
  font-weight: 600;
  background: rgba(255, 255, 255, 0.06);
  border-radius: 999px;
  padding: 10px 0;
}
</style>
