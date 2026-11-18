<script setup lang="ts">
/**
 * 统一码管理（M1 · Batch F 码体系正名）
 *
 * 本页只剩会员卡码（续期/白名单/折扣，登录后兑换）一个分区。
 * （原「注册码」分区已随注册码下线于 2026-11-15 取消；「旧版兑换码」
 * 分区已随旧兑换码体系于 2026-11-17 删除，旧地址 /exchange-codes
 * 跳到本页。）
 * 三种码的分工说明收敛在本页顶部（唯一权威出处；Coupons.vue 的旧说明
 * 已删，改为指向这里）。
 */
import { defineAsyncComponent } from 'vue'

// 子页是一整页：按需加载
const MemberCodes = defineAsyncComponent(() => import('./MemberCodes.vue'))
</script>

<template>
  <!-- 子页自带 PageHeader（眉题「经营」），这里不再叠第二个页头 -->
  <div class="admin-page codes-page">
    <!-- 三种码分工说明：唯一权威出处。验收口径=新服主不看说明书能分清三种码 -->
    <el-alert type="info" :closable="false" show-icon class="division-alert">
      <template #title>三种码，一眼看懂</template>
      <ul class="division-list">
        <li><strong>会员卡码</strong>（本页）：登录后兑换的奖励码——「续期会员」「送折扣」「资格 / 白名单」。分两种用途：续期码（在到期时间上叠加天数）、白名单码（长期有效）。</li>
        <li><strong>优惠券</strong>（<router-link to="/coupons">优惠券</router-link>页）：下单付款那一刻直接打折，钱照旧走支付网关。和卡码的区别：卡码是「不花钱直接拿东西」，优惠券是「付费时打折」。</li>
        <li><strong>邀请码</strong>（<router-link to="/invitations">邀请</router-link>页）：邀请好友注册用的码。</li>
      </ul>
    </el-alert>

    <MemberCodes />
  </div>
</template>

<style scoped>
/* 外层间距交给 .admin-content（不再自带 padding / max-width，否则手机上左右各多出 16px） */
.division-alert { margin-bottom: 14px; }
.division-list { margin: 4px 0 0; padding-left: 18px; display: flex; flex-direction: column; gap: 4px; font-size: var(--uhd-fs-aux-sm); line-height: 1.6; }
.division-list a { color: var(--au-primary); }
</style>
