<template>
  <div class="pc-entry">
    <PcEntryRow
      :icon="LayoutGrid"
      name="发现页卡片展示"
      :meta="modeLabel(cardPrefs.mode.value)"
      expandable
      :expanded="open"
      @click="toggle"
    />
    <div v-if="open" class="pc-expand">
      <UhdSegmented
        class="mode-seg"
        :options="modeOptions"
        :model-value="cardPrefs.mode.value"
        aria-label="发现页卡片展示模式"
        @update:model-value="onModeChange"
      />
      <p class="mode-hint">自动 = 一个服大卡、多个服总览</p>

      <div v-if="serversLoading && !servers.length" class="sx-loading"><span class="sx-spin" aria-hidden="true"></span>正在加载…</div>
      <div v-else-if="serversLoadError" class="sx-err">服务器清单加载失败<button type="button" class="sx-mini-btn" @click="() => loadServers()">重试</button></div>
      <div v-else-if="!servers.length" class="srv-empty">还没有可展示的服务器</div>
      <div v-else>
        <div v-for="s in servers" :key="s.id" class="srv-row">
          <div class="srv-info">
            <div class="srv-name">{{ s.name }}</div>
            <div class="srv-tag">{{ serverTagText(s) }}</div>
          </div>
          <button
            type="button"
            class="set-switch"
            :class="{ 'is-on': cardPrefs.isVisible(s.id) }"
            role="switch"
            :aria-checked="cardPrefs.isVisible(s.id)"
            :aria-label="`${s.name}：在发现页显示`"
            @click="cardPrefs.toggleServer(s.id)"
          ></button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 发现页卡片展示（自旧 AccountSettingsView 原样抽出）：展示模式三段
 * （自动/大卡/总览）＋ 每台服务器显隐开关；偏好走 useServerCardPrefs
 * 单例（与发现页共用同一份状态），服务器清单首次展开才加载。
 */
import { ref } from 'vue'
import { LayoutGrid } from 'lucide-vue-next'
import PcEntryRow from './PcEntryRow.vue'
import UhdSegmented, { type UhdSegmentOption } from '@/components/uhd/UhdSegmented.vue'
import { useServerCardPrefs, type ServerCardMode } from '@/composables/useServerCardPrefs'
import { useServerCards, type ServerCardItem } from '@/composables/useServerCards'

const cardPrefs = useServerCardPrefs()
const { servers, load: loadServers, loading: serversLoading, loadError: serversLoadError } = useServerCards()

const open = ref(false)

const modeOptions: UhdSegmentOption[] = [
  { value: 'auto', label: '自动' },
  { value: 'cards', label: '大卡' },
  { value: 'overview', label: '总览' },
]

function onModeChange(v: string) {
  cardPrefs.setMode(v as ServerCardMode)
}

const ACCESS_LABEL: Record<ServerCardItem['accessMode'], string> = {
  paid: '付费服',
  free: '公益服',
}

function modeLabel(m: ServerCardMode): string {
  return m === 'cards' ? '大卡' : m === 'overview' ? '总览' : '自动'
}
function serverTagText(s: ServerCardItem): string {
  return ACCESS_LABEL[s.accessMode]
}
function toggle() {
  open.value = !open.value
  if (open.value && !serversLoading.value && !servers.value.length) void loadServers()
}
</script>

<style scoped>
/* 展开面板：状态行模具（说明书 2.6）；模式切换=分段模具（2.3，UhdSegmented 全宽）、
   每服一行 + 开关模具（与设置页 .set-switch 同形态） */
.pc-expand {
  border-top: var(--uhd-border-w) solid var(--uhd-l-divider);
  padding: 14px 2px 18px;
  font-family: var(--uhd-font-sans);
}
.mode-seg { display: flex; width: 100%; }
.mode-seg :deep(.uhd-seg__item) { flex: 1; justify-content: center; }
.mode-hint {
  margin: 10px 0 0;
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-l-text-2);
}
.sx-mini-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  min-height: 36px;
  padding: 0 16px;
  background: var(--uhd-l-surface);
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-pill);
  color: var(--uhd-l-text);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  cursor: pointer;
}
.sx-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 0 6px;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
}
.sx-spin {
  width: 18px;
  height: 18px;
  flex: none;
  border-radius: 50%;
  border: 2px solid var(--uhd-l-divider);
  border-top-color: var(--uhd-l-text-2);
  animation: uhd-spin 0.8s linear infinite;
}
.sx-err {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 14px;
  padding: 14px 16px;
  border: 1px dashed #d4d4d8;
  border-radius: 16px;
  font-size: var(--uhd-fs-caption);
  line-height: 1.6;
  color: var(--uhd-l-text-2);
}
.srv-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 56px;
  padding: 8px 0;
}
.srv-row + .srv-row {
  border-top: var(--uhd-border-w) solid var(--uhd-l-divider);
}
.srv-info { min-width: 0; }
.srv-name {
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-l-text);
}
.srv-tag {
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-l-text-2);
}
/* 开关模具：44×24 黑体轨道 + 20px 白钮，按钮本体 52×44 命中区（类内写死高度） */
.set-switch {
  position: relative;
  flex: none;
  width: 52px;
  height: 44px;
  min-height: 44px;
  padding: 0;
  border: 0;
  background: none;
  cursor: pointer;
}
.set-switch::before {
  content: '';
  position: absolute;
  left: 4px;
  top: 10px;
  width: 44px;
  height: 24px;
  border-radius: var(--uhd-radius-pill);
  background: var(--uhd-l-border);
  transition: background var(--uhd-dur-ui) var(--uhd-ease-standard);
}
.set-switch::after {
  content: '';
  position: absolute;
  top: 12px;
  left: 6px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--uhd-l-surface);
  box-shadow: var(--uhd-shadow-seg);
  transition: left var(--uhd-dur-ui) var(--uhd-ease-standard);
}
.set-switch.is-on::before { background: var(--uhd-l-text); }
.set-switch.is-on::after { left: 26px; }
.srv-empty {
  padding: 14px 0 4px;
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-l-text-2);
}
</style>
