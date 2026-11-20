<template>
  <button v-if="!hidden" type="button" class="uir-row" @click="onTap">
    <Download :size="20" class="uir-row__icon" aria-hidden="true" />
    <span class="uir-row__name">安装应用</span>
    <span class="uir-row__value">添加到主屏幕</span>
    <ChevronRight :size="17" class="uir-row__chev" aria-hidden="true" />
  </button>

  <Teleport to="body">
    <div v-if="sheetOpen" class="uir-sheet-wrap" role="dialog" aria-modal="true" aria-label="安装应用说明">
      <div class="uir-sheet__scrim" @click="sheetOpen = false"></div>
      <section class="uir-sheet">
        <header class="uir-sheet__head">
          <h2 class="uir-sheet__title">把 Aetrix 放到桌面</h2>
          <button type="button" class="uir-sheet__close" aria-label="关闭" @click="sheetOpen = false">
            <X :size="18" aria-hidden="true" />
          </button>
        </header>

        <ol v-if="sheetMode === 'ios'" class="uir-steps">
          <li class="uir-step">
            <span class="uir-step__no">1</span>
            <Share :size="18" class="uir-step__icon" aria-hidden="true" />
            <p class="uir-step__text">点浏览器底部的<strong>分享</strong>按钮</p>
          </li>
          <li class="uir-step">
            <span class="uir-step__no">2</span>
            <Plus :size="18" class="uir-step__icon" aria-hidden="true" />
            <p class="uir-step__text">在菜单里选<strong>「添加到主屏幕」</strong></p>
          </li>
          <li class="uir-step">
            <span class="uir-step__no">3</span>
            <Check :size="18" class="uir-step__icon" aria-hidden="true" />
            <p class="uir-step__text">点右上角<strong>「添加」</strong>，桌面就会出现 Aetrix 图标</p>
          </li>
        </ol>
        <p v-else class="uir-hint">
          打开浏览器菜单，选<strong>「添加到主屏幕」</strong>（或「安装应用」），Aetrix 就会以独立 app 的形态出现在桌面。
        </p>

        <button type="button" class="uir-sheet__ok" @click="sheetOpen = false">知道了</button>
      </section>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Check, ChevronRight, Download, Plus, Share, X } from 'lucide-vue-next'
import { isIOS, isStandalone, promptInstall, useInstallPrompt } from '@/composables/useInstallPrompt'

// 已按 app 形态打开时整行不渲染（没有可安装的东西了）
const hidden = ref(false)
const sheetOpen = ref(false)
const sheetMode = ref<'ios' | 'generic'>('generic')
const { canInstall } = useInstallPrompt()

onMounted(() => {
  hidden.value = isStandalone()
})

async function onTap() {
  if (canInstall.value) {
    const outcome = await promptInstall()
    if (outcome === 'accepted') hidden.value = true
    return
  }
  sheetMode.value = isIOS() ? 'ios' : 'generic'
  sheetOpen.value = true
}
</script>

<style scoped>
/* 行式与 AccountMenuView 的 .am-row 对齐：同一套 ax 深色语言 */
.uir-row {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 15px 16px;
  border: none;
  background: transparent;
  color: var(--ax-d-text);
  font-family: var(--ax-font-sans);
  font-size: var(--ax-fs-body);
  text-align: left;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}
.uir-row:active {
  background: var(--ax-d-surface-2);
}
.uir-row__icon {
  flex: 0 0 auto;
  color: var(--ax-d-text-2);
}
.uir-row__name {
  flex: 1 1 auto;
  min-width: 0;
}
.uir-row__value {
  flex: 0 0 auto;
  font-size: var(--ax-fs-caption);
  color: var(--ax-d-text-3);
}
.uir-row__chev {
  flex: 0 0 auto;
  color: var(--ax-d-text-3);
}

.uir-sheet-wrap {
  position: fixed;
  inset: 0;
  z-index: 320;
  display: flex;
  align-items: center;
  justify-content: center;
}
.uir-sheet__scrim {
  position: absolute;
  inset: 0;
  background: rgba(10, 10, 14, 0.55);
}
/* 居中实色浮窗（2026-11-18 用户亲令改实色；2026-11-15 弹层形态
   批 1 曾为玻璃浮窗，与购流量弹窗同一口径）：宽 92%、max 420px、
   四角圆角 26；面板底改实色 surface（深
   var(--ax-d-surface-raised)、浅 var(--ax-l-surface)）、阴影走
   --ax-shadow-modal、遮罩与面板均无 backdrop；浅色变体见
   文末覆盖。 */
.uir-sheet {
  position: relative;
  width: 92%;
  max-width: 420px;
  margin: 0 auto;
  padding: 22px 22px 24px;
  border-radius: 26px;
  background: var(--ax-d-surface-raised);
  border: 1px solid var(--ax-glass-border);
  box-shadow: var(--ax-shadow-modal);
  color: var(--ax-d-text);
  font-family: var(--ax-font-sans);
  animation: uir-pop-in 0.28s cubic-bezier(0.32, 0.72, 0.28, 1) both;
}
@keyframes uir-pop-in {
  from { transform: translateY(12px) scale(0.97); opacity: 0; }
  to { transform: translateY(0) scale(1); opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .uir-sheet { animation: none; }
}
.uir-sheet__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}
.uir-sheet__title {
  margin: 0;
  font-size: 17px;
  font-weight: var(--ax-fw-bold);
}
.uir-sheet__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 50%;
  background: var(--ax-d-surface-raised);
  color: var(--ax-d-text-2);
  cursor: pointer;
}
.uir-steps {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.uir-step {
  display: flex;
  align-items: center;
  gap: 12px;
}
.uir-step__no {
  flex: 0 0 auto;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--ax-d-surface-raised);
  color: var(--ax-d-text);
  font-size: 12px;
  font-weight: var(--ax-fw-bold);
}
.uir-step__icon {
  flex: 0 0 auto;
  color: var(--ax-d-text-2);
}
.uir-step__text {
  margin: 0;
  font-size: var(--ax-fs-body);
  line-height: 1.55;
  color: var(--ax-d-text-2);
}
.uir-step__text strong,
.uir-hint strong {
  color: var(--ax-d-text);
  font-weight: var(--ax-fw-bold);
}
.uir-hint {
  margin: 0;
  font-size: var(--ax-fs-body);
  line-height: 1.6;
  color: var(--ax-d-text-2);
}
.uir-sheet__ok {
  width: 100%;
  margin-top: 20px;
  padding: 13px 16px;
  border: none;
  border-radius: 999px;
  background: var(--ax-d-surface-raised);
  color: var(--ax-d-text);
  font-family: var(--ax-font-sans);
  font-size: var(--ax-fs-body);
  font-weight: var(--ax-fw-bold);
  cursor: pointer;
}

/* 浅色变体（批 1 补齐）：面板换浅色玻璃底/边，文字与抬升面
   换 --ax-l-* 一套，与深色一一对应，不再写死深色。 */
html[data-theme='light'] .uir-sheet {
  background: var(--ax-l-surface);
  border-color: var(--ax-glass-border-l);
  color: var(--ax-l-text);
}
html[data-theme='light'] .uir-sheet__close {
  background: var(--ax-l-seg-bg);
  color: var(--ax-l-text-2);
}
html[data-theme='light'] .uir-step__no {
  background: var(--ax-l-seg-bg);
  color: var(--ax-l-text);
}
html[data-theme='light'] .uir-step__icon {
  color: var(--ax-l-text-2);
}
html[data-theme='light'] .uir-step__text {
  color: var(--ax-l-text-2);
}
html[data-theme='light'] .uir-step__text strong,
html[data-theme='light'] .uir-hint strong {
  color: var(--ax-l-text);
}
html[data-theme='light'] .uir-hint {
  color: var(--ax-l-text-2);
}
html[data-theme='light'] .uir-sheet__ok {
  background: var(--ax-l-seg-bg);
  color: var(--ax-l-text);
}
</style>
