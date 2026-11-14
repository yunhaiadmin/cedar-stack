<script setup lang="ts">
/**
 * 壳外放顶栏和底坞，用来证明模态会把它们标成 inert。
 * 不是生产 App.vue，也不读取登录会话。
 */
import { KeepAlive } from 'vue'
import { RouterView } from 'vue-router'
</script>

<template>
  <div class="app-shell">
    <p class="fixture-banner">本地隔离夹具 · 不是登录会话 · 不连接生产账户</p>
    <header class="fake-top">
      <button id="fake-top" type="button">顶栏</button>
    </header>
    <button id="already-inert" type="button" inert>原本不可用</button>
    <RouterView v-slot="{ Component }">
      <KeepAlive>
        <component :is="Component" />
      </KeepAlive>
    </RouterView>
    <nav class="fake-dock">
      <button id="fake-dock" type="button">底坞</button>
    </nav>
  </div>
</template>

<style>
.fixture-banner {
  margin: 0;
  padding: 8px 12px;
  background: #f5d90a;
  color: #1a1a1a;
  font: 600 14px/1.4 sans-serif;
}
.fake-top,
.fake-dock {
  display: flex;
  gap: 8px;
  padding: 8px 12px;
}
#already-inert {
  position: absolute;
  left: -999px;
}
</style>
