<template>
  <PlayerSheet :open="open" title="弹幕设置" @update:open="emit('update:open', $event)">
    <div class="wp-group">
      <p class="wp-label">显示开关</p>
      <div class="wp-seg">
        <button type="button" :class="{ on: settings.enabled }" @click="set('enabled', true)">开启</button>
        <button type="button" :class="{ on: !settings.enabled }" @click="set('enabled', false)">关闭</button>
      </div>
    </div>

    <div class="wp-group">
      <p class="wp-label">显示区域</p>
      <div class="wp-seg">
        <button
          v-for="a in AREAS"
          :key="a.value"
          type="button"
          :class="{ on: settings.area === a.value }"
          @click="set('area', a.value)"
        >
          {{ a.label }}
        </button>
      </div>
    </div>

    <div class="wp-group">
      <p class="wp-label wp-label-row">
        <span>不透明度</span>
        <span class="wp-val">{{ settings.opacity }}%</span>
      </p>
      <input
        class="wp-range"
        type="range"
        min="0"
        max="100"
        step="5"
        :value="settings.opacity"
        @input="set('opacity', Number(($event.target as HTMLInputElement).value))"
      />
    </div>

    <div v-for="g in SEG_GROUPS" :key="g.key" class="wp-group">
      <p class="wp-label">{{ g.label }}</p>
      <div class="wp-seg">
        <button
          v-for="o in g.options"
          :key="o.value"
          type="button"
          :class="{ on: settings[g.key] === o.value }"
          @click="setSeg(g.key, o.value)"
        >
          {{ o.label }}
        </button>
      </div>
    </div>

    <div class="wp-group">
      <p class="wp-label">时间偏移</p>
      <div class="wp-stepper">
        <button type="button" aria-label="减少偏移" @click="bumpOffset(-0.5)">−</button>
        <span>{{ settings.offsetSeconds > 0 ? '+' : '' }}{{ settings.offsetSeconds.toFixed(1) }}s</span>
        <button type="button" aria-label="增加偏移" @click="bumpOffset(0.5)">+</button>
      </div>
      <p class="wp-hint">弹幕出现时间相对画面的偏移，负数提前、正数延后。</p>
    </div>

    <!-- 诚实缺口：本站没有弹幕数据源（服务端只有空弹幕兼容端点），
         这里只保存设置，不假装有弹幕可显示。 -->
    <p class="wp-gap">本站暂时没有弹幕数据，设置会先替你存好，有弹幕时按这套生效。</p>
  </PlayerSheet>
</template>

<script setup lang="ts">
import PlayerSheet from './PlayerSheet.vue'

export interface DanmakuSettings {
  enabled: boolean
  area: 'half' | 'full'
  opacity: number
  fontSize: 'small' | 'medium' | 'large'
  density: 'low' | 'standard' | 'high'
  speed: 'slow' | 'standard' | 'fast'
  offsetSeconds: number
}

const props = defineProps<{ open: boolean; settings: DanmakuSettings }>()
const emit = defineEmits<{
  'update:open': [boolean]
  'update:settings': [DanmakuSettings]
}>()

const AREAS = [
  { value: 'half' as const, label: '上半屏' },
  { value: 'full' as const, label: '全屏' },
]
type SegKey = 'fontSize' | 'density' | 'speed'
const SEG_GROUPS: Array<{
  key: SegKey
  label: string
  options: Array<{ value: string; label: string }>
}> = [
  {
    key: 'fontSize',
    label: '字号',
    options: [
      { value: 'small', label: '小' },
      { value: 'medium', label: '中' },
      { value: 'large', label: '大' },
    ],
  },
  {
    key: 'density',
    label: '弹幕密度',
    options: [
      { value: 'low', label: '少' },
      { value: 'standard', label: '标准' },
      { value: 'high', label: '多' },
    ],
  },
  {
    key: 'speed',
    label: '滚动速度',
    options: [
      { value: 'slow', label: '慢' },
      { value: 'standard', label: '标准' },
      { value: 'fast', label: '快' },
    ],
  },
]

function set<K extends keyof DanmakuSettings>(key: K, value: DanmakuSettings[K]): void {
  emit('update:settings', { ...props.settings, [key]: value })
}
function setSeg(key: SegKey, value: string): void {
  emit('update:settings', {
    ...props.settings,
    [key]: value as DanmakuSettings[SegKey],
  })
}
function bumpOffset(delta: number): void {
  const next = Math.round((props.settings.offsetSeconds + delta) * 10) / 10
  set('offsetSeconds', Math.max(-10, Math.min(10, next)))
}
</script>

<style scoped>
.wp-group {
  margin-top: 16px;
}
.wp-label {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.55);
  margin: 0 0 8px;
}
.wp-label-row {
  display: flex;
  justify-content: space-between;
}
.wp-val {
  color: #fff;
  font-weight: 600;
}
.wp-seg {
  display: flex;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 999px;
  padding: 3px;
  gap: 3px;
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
.wp-range {
  width: 100%;
  accent-color: #fff;
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
.wp-hint {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.4);
  margin: 8px 0 0;
}
.wp-gap {
  margin: 20px 0 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: #f2c14e;
  background: rgba(242, 193, 78, 0.1);
  border: 1px solid rgba(242, 193, 78, 0.25);
  border-radius: 12px;
  padding: 10px 12px;
}
</style>
