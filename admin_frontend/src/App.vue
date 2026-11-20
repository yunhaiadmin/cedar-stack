<script setup lang="ts">
import { RouterView } from 'vue-router'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import Toast from '@/components/Toast.vue'
</script>

<template>
  <!--
    Element Plus 内置文案统一中文（v2.42.5）：分页的「Go to previous page / page 2」、
    表格与下拉的空态（No Data）、日期选择器等都是组件库自带的英文串。
    按需引入模式下不能 app.use(ElementPlus,{locale})（会全量打包），
    所以用 ConfigProvider 把 locale 注入组件树。
  -->
  <el-config-provider :locale="zhCn">
    <RouterView />
    <Toast />
  </el-config-provider>
</template>

<style>
html,
body {
  margin: 0;
  padding: 0;
  height: 100%;
  /* 批 1：跟随 ax 主题底色（--au-bg 深/浅两档），不再写死 #0a0a0a，
     否则浅色主题下 html 底边仍露深色。 */
  background: var(--au-bg);
}

#app {
  height: 100%;
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
  color: var(--au-text);
}
</style>
