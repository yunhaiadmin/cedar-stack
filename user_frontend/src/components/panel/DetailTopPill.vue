<!-- 详情页移动端右上玻璃胶囊（2026-11-14 用户截图亲点的友站形态）：
     一颗毛玻璃胶囊里并排「外观切换（月牙/太阳）」与「关闭（X）」。
     外观走 StreamShell 注入的片库面主题口径（三态循环，与顶栏同键
     aetrix-stream-theme，不新造一套）；关闭 = 回上一页（由详情页传入）。
     仅移动端渲染——桌面详情头部没有这颗胶囊（桌面有顶栏承担外观切换）。 -->
<template>
  <div class="dtp">
    <button
      v-if="theme"
      type="button"
      class="dtp__btn"
      aria-label="切换深浅外观"
      @click="theme.cycleTheme()"
    >
      <Moon v-if="isDark" :size="16" />
      <Sun v-else :size="16" />
    </button>
    <span v-if="theme" class="dtp__sep" aria-hidden="true"></span>
    <button type="button" class="dtp__btn" :aria-label="closeLabel" @click="emit('close')">
      <X :size="16" />
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, type ComputedRef, type Ref } from 'vue'
import { Moon, Sun, X } from 'lucide-vue-next'

const emit = defineEmits<{ close: [] }>()

/** 关闭钮的无障碍文案：详情页是「关闭详情」；片库首页复用本控件时
    语义是「退出媒体库」，由使用方传入。关闭动作本身走 close 事件。 */
withDefaults(defineProps<{ closeLabel?: string }>(), { closeLabel: '关闭详情' })

interface StreamThemeCtx {
  pref: Ref<'system' | 'light' | 'dark'>
  resolved: ComputedRef<'light' | 'dark'>
  cycleTheme: () => void
}

/** StreamShell provide 的片库面外观上下文；不在片库面内使用时为 null，
    此时只保留关闭钮（不假造主题能力） */
const theme = inject<StreamThemeCtx | null>('stream-theme', null)
const isDark = computed(() => theme?.resolved.value === 'dark')
</script>

<style scoped>
.dtp {
  position: absolute;
  top: calc(env(safe-area-inset-top, 0px) + 20px);
  right: 16px;
  z-index: 4;
  display: none;
  align-items: center;
  gap: 2px;
  padding: 3px;
  border-radius: 999px;
  border: 1px solid var(--uhd-s-pill-border);
  background: var(--uhd-s-pill-bg);
  color: var(--uhd-s-text);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.18),
    var(--uhd-s-shadow-pill);
  backdrop-filter: var(--uhd-glass-filter);
  -webkit-backdrop-filter: var(--uhd-glass-filter);
}
/* 全局 mobile.css 的 button{min-height:44px} 会撑高胶囊内小钮，本作用域中和 */
.dtp button {
  min-height: 0;
}
.dtp__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.dtp__btn:active {
  background: rgba(128, 128, 128, 0.18);
}
.dtp__sep {
  width: 1px;
  height: 16px;
  background: currentColor;
  opacity: 0.16;
}
@media (max-width: 767px) {
  .dtp {
    display: inline-flex;
  }
}
</style>
