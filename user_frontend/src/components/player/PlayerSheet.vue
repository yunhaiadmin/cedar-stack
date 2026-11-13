<template>
  <!-- 播放器弹层外壳：照 UHDNow 录屏——窄屏底部升起的大圆角玻璃卡，
       宽屏/横屏为居中卡。点遮罩关闭。 -->
  <Transition name="wp-sheet">
    <div v-if="open" class="wp-sheet-root" @click.self="emit('update:open', false)">
      <section class="wp-sheet" role="dialog" :aria-label="title">
        <header class="wp-sheet-head">
          <h3>{{ title }}</h3>
          <button type="button" class="wp-sheet-x" aria-label="关闭" @click="emit('update:open', false)">
            <X :size="18" />
          </button>
        </header>
        <div class="wp-sheet-body">
          <slot />
        </div>
      </section>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { X } from 'lucide-vue-next'

defineProps<{ open: boolean; title: string }>()
const emit = defineEmits<{ 'update:open': [boolean] }>()
</script>

<style scoped>
.wp-sheet-root {
  position: absolute;
  inset: 0;
  z-index: 40;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: flex-end;
  justify-content: center;
}
.wp-sheet {
  width: min(430px, 100%);
  max-height: 78%;
  overflow-y: auto;
  background: rgba(24, 24, 27, 0.92);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 24px 24px 0 0;
  color: #fff;
  padding: 18px 20px calc(18px + env(safe-area-inset-bottom));
}
@media (min-width: 768px) {
  .wp-sheet-root {
    align-items: center;
    padding: 24px;
  }
  .wp-sheet {
    border-radius: 24px;
    max-height: 84%;
  }
}
.wp-sheet-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.wp-sheet-head h3 {
  font-size: 17px;
  font-weight: 700;
  margin: 0;
}
.wp-sheet-x {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: none;
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
  display: grid;
  place-items: center;
  cursor: pointer;
}
.wp-sheet-enter-active,
.wp-sheet-leave-active {
  transition: opacity 0.22s ease;
}
.wp-sheet-enter-active .wp-sheet,
.wp-sheet-leave-active .wp-sheet {
  transition: transform 0.22s ease;
}
.wp-sheet-enter-from,
.wp-sheet-leave-to {
  opacity: 0;
}
.wp-sheet-enter-from .wp-sheet,
.wp-sheet-leave-to .wp-sheet {
  transform: translateY(24px);
}
</style>
