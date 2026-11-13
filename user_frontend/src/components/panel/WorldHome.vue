<template>
  <WorldMap variant="home" />
</template>

<script setup lang="ts">
/**
 * 首页探险世界：地图本体在 WorldMap/ 目录（探险日志 /game 已删，现仅首页用），
 * 这里只剩一层壳，保证 DiscoveryView 的引用与行为不变。
 */
import WorldMap from './WorldMap/index.vue'
</script>
