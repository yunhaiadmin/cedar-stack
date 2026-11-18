<template>
  <div class="wts">
    <!-- 折叠行（G-192）：右向三角＝折叠态，展开后旋转向下 -->
    <button
      type="button"
      class="wts-head"
      :aria-expanded="open"
      @click="open = !open"
    >
      <ChevronRight
        :size="15"
        class="wts-arrow"
        :class="{ 'wts-arrow--open': open }"
        aria-hidden="true"
      />
      同步设置
    </button>
    <div v-if="open" class="wts-body">
      <!-- 说明行（G-193）：文案照友站 -->
      <p class="wts-note">开始播放后可查看同步状态</p>
      <div v-for="f in FIELDS" :key="f.key" class="wts-row">
        <label :for="`wts-${f.key}`">{{ f.label }}</label>
        <input
          :id="`wts-${f.key}`"
          type="number"
          inputmode="numeric"
          :value="draft[f.key]"
          @input="setDraft(f.key, $event)"
        />
      </div>
      <!-- 两钮（G-199）：恢复默认＝回友站默认值；应用设置＝保存即时生效 -->
      <div class="wts-actions">
        <button type="button" class="wts-btn" @click="resetDraft">
          恢复默认
        </button>
        <button type="button" class="wts-btn" @click="applyDraft">
          应用设置
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { ChevronRight } from 'lucide-vue-next'
import {
  WT_SYNC_DEFAULTS,
  sanitizeWtSyncParams,
  type WtSyncParams,
} from '@/utils/wtSyncParams'

const props = defineProps<{
  /** 当前已生效的同步参数（父级持久化后回传） */
  params: WtSyncParams
}>()

const emit = defineEmits<{
  apply: [params: WtSyncParams]
}>()

const FIELDS: { key: keyof WtSyncParams; label: string }[] = [
  { key: 'startMs', label: '开始纠偏（ms）' },
  { key: 'endMs', label: '结束纠偏（ms）' },
  { key: 'directMs', label: '直接对齐（ms）' },
  { key: 'ratePct', label: '倍速调整幅度（%）' },
  { key: 'intervalMs', label: '自动对齐间隔（ms）' },
]

const open = ref(false)
// 草稿与已生效值分开：改输入框不立即生效，点「应用设置」才提交
const draft = ref<WtSyncParams>({ ...props.params })

watch(
  () => props.params,
  (p) => {
    draft.value = { ...p }
  },
)

function setDraft(key: keyof WtSyncParams, e: Event): void {
  const n = Number((e.target as HTMLInputElement).value)
  draft.value = { ...draft.value, [key]: Number.isFinite(n) ? n : 0 }
}

function resetDraft(): void {
  draft.value = { ...WT_SYNC_DEFAULTS }
}

function applyDraft(): void {
  emit('apply', sanitizeWtSyncParams(draft.value))
}
</script>

<style scoped>
.wts {
  margin-top: 18px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  padding-top: 6px;
}
.wts-head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  border: none;
  background: transparent;
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  text-align: left;
  padding: 10px 2px;
  cursor: pointer;
}
.wts-arrow {
  flex: none;
  color: rgba(255, 255, 255, 0.55);
  transition: transform 0.18s ease;
}
.wts-arrow--open {
  transform: rotate(90deg);
}
.wts-note {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.5);
  margin: 2px 0 10px;
}
.wts-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 5px 0;
}
.wts-row label {
  font-size: 14.5px;
  color: rgba(255, 255, 255, 0.85);
}
.wts-row input {
  width: 118px;
  height: 40px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.07);
  color: #fff;
  font-size: 15px;
  text-align: center;
  outline: none;
}
.wts-row input:focus {
  border-color: rgba(255, 255, 255, 0.35);
}
.wts-actions {
  display: flex;
  gap: 10px;
  margin-top: 14px;
}
.wts-btn {
  flex: 1;
  height: 46px;
  border: 1px solid rgba(255, 255, 255, 0.28);
  border-radius: 999px;
  background: transparent;
  color: #fff;
  font-size: 14.5px;
  font-weight: 600;
  cursor: pointer;
}
</style>
