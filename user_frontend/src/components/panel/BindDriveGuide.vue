<!-- 未绑盘点播引导（全站统一一套）：播放门禁拦下点播时展示。
     一句话说清绑上自己的盘就能看这部片，主按钮「去绑盘」直达设置页
     绑定组，并带回跳参数——绑完自动回到来源页重开播放器选择。
     所有点播入口共用本组件，不许各入口再自写一份提示文案。 -->
<template>
  <div class="bdg">
    <p class="bdg-text">
      <template v-if="title">绑上你自己的网盘，就能看《{{ title }}》了。</template>
      <template v-else>绑上你自己的网盘，就能点播了。</template>
    </p>
    <p class="bdg-sub">没绑定只能浏览、不能点播。绑好后自动带你回来，接着选播放器就能看。</p>
    <button type="button" class="bdg-btn" @click="goBind">去绑盘</button>
  </div>
</template>

<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { bindDriveLocation } from '@/composables/useBindReturn'

const props = defineProps<{ title?: string }>()
const emit = defineEmits<{ (e: 'navigate'): void }>()

const route = useRoute()
const router = useRouter()

function goBind() {
  // 回跳来源 = 当前页路径（发现页 / 或探险日志页），绑完原路返回
  void router.push(bindDriveLocation(route.path, props.title))
  emit('navigate')
}
</script>

<style scoped>
.bdg { margin-top: 10px; }
.bdg-text {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  line-height: 1.55;
  color: var(--ink, #2a2620);
}
.bdg-sub {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.55;
  color: var(--ink2, #6b6257);
}
.bdg-btn {
  display: block;
  width: 100%;
  margin-top: 14px;
  min-height: 48px;
  padding: 12px 20px;
  border: 0;
  border-radius: var(--paper-r-lg, 12px);
  /* 主按钮深青（DESIGN.md 唯一彩色），全站激活态同口径 */
  background: var(--paper-primary, #016a71);
  color: #fff;
  font-size: 15px;
  font-weight: 800;
  letter-spacing: 0.04em;
  cursor: pointer;
  box-shadow: 0 2px 0 rgba(0, 0, 0, 0.18);
}
.bdg-btn:active { transform: translateY(2px); box-shadow: none; }
.bdg-btn:focus-visible {
  outline: 2px solid var(--red, #c23b22);
  outline-offset: 2px;
}
@media (prefers-reduced-motion: reduce) {
  .bdg-btn:active { transform: none; }
}
</style>
