<script setup lang="ts">
/**
 * 根路径 '/' 分发组件（2026-11-13 落点分场景定版）：
 * 无条件渲染官网 LandingView（URL 保持 '/'，不跳 /welcome），
 * 未登录与已登录进站都停在这里——已登录停宣传页是友站 UHDNow 的
 * 既定设计（用户 21:22 亲定），不许加跳转把人拽走；页内自带进入
 * 应用的入口（Hero「进入后台」钮与菜单面板大钮，已登录进概览、
 * 未登录去登录页）。「登录动作落概览」只发生在 LoginView 提交
 * 成功与已登录访问 /login 两处，与本组件无关。
 * 旧发现页 DiscoveryView 已连根删除，本组件不再渲染任何旧页。
 */
import { defineAsyncComponent } from 'vue'
import LandingSkeleton from '@/components/ui/LandingSkeleton.vue'
import { handleChunkLoadError } from '@/utils/chunkRecovery'

defineOptions({ name: 'RootView' })

// 懒加载口径与原路由分片一致：官网仍自成 chunk，不进首包。
// 加载中先出纸面骨架不许白屏；加载失败先自重试一次，仍失败交给
// chunk 统一恢复（部署换包时 chunk 被删是最常见形态，见 utils/chunkRecovery）。
const LandingView = defineAsyncComponent({
  loader: () => import('@/views/marketing/LandingView.vue'),
  loadingComponent: LandingSkeleton,
  delay: 150,
  onError(error, retry, fail, attempts) {
    if (attempts <= 1) {
      retry()
      return
    }
    handleChunkLoadError(error)
    fail()
  },
})
</script>

<template>
  <LandingView />
</template>
