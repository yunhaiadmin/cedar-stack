<script setup lang="ts">
/**
 * 统一码管理（M1 · Batch F 码体系正名）
 *
 * 三个分区并一页：注册码（2026-11-10 自会员卡码中拆出，只用于注册页）+
 * 会员卡码（续期/白名单/折扣，登录后兑换）+ 旧版兑换码（旧系统，只查旧码）
 * 通过标签页切换，统一入口管理所有码。
 * 四种码的分工说明收敛在本页顶部（唯一权威出处；Coupons.vue 与 ExchangeCodes.vue
 * 的两处旧说明已删，改为指向这里）。
 */
import { computed, defineAsyncComponent } from 'vue'
import { useRoute, useRouter } from 'vue-router'

// 两个标签页各自是一整页：按需加载，只看会员卡码时不用把兑换码页也打进首屏
const RegistrationCodes = defineAsyncComponent(() => import('./RegistrationCodes.vue'))
const ExchangeCodes = defineAsyncComponent(() => import('./ExchangeCodes.vue'))

type CodesTab = 'reg' | 'regcode' | 'exchange'

const route = useRoute()
const router = useRouter()

/**
 * 当前标签与 ?tab= 同步：旧地址 /exchange-codes、/registration-codes 跳过来能落到对应标签，刷新也不丢。
 * tab= 取值（reg / exchange）是兼容硬约束：Dashboard 等页的 ?tab=exchange 深链必须继续有效，
 * 所以只改标签的显示名，不改 query 取值。注册码分区（2026-11-10 分离新增）用新值 regcode。
 */
const activeTab = computed<CodesTab>({
  get: () => {
    if (route.query.tab === 'exchange') return 'exchange'
    if (route.query.tab === 'regcode') return 'regcode'
    return 'reg'
  },
  set: (tab) => {
    if (tab === activeTab.value) return
    router.replace({ query: { ...route.query, tab } })
  },
})
</script>

<template>
  <!-- 两个子页各自带 PageHeader（眉题「经营」），这里只放标签条，不再叠第二个页头 -->
  <div class="admin-page codes-page">
    <!-- 四种码分工说明：唯一权威出处。验收口径=新服主不看说明书能分清四种码 -->
    <el-alert type="info" :closable="false" show-icon class="division-alert">
      <template #title>四种码，一眼看懂</template>
      <ul class="division-list">
        <li><strong>注册码</strong>（本页「注册码」分区）：只用于注册页——凭码注册时用户先验码、建号时核销，并带上码附带的权益；已登录用户在兑换入口用它会被明确拒绝（提示去注册页）。代理出品的码也会在这里出现，按出品人区分。</li>
        <li><strong>会员卡码</strong>（本页「会员卡码」分区）：登录后兑换的奖励码——「续期会员」「送折扣」「资格 / 白名单」。分两种用途：续期码（在到期时间上叠加天数）、白名单码（长期有效）。</li>
        <li><strong>旧版兑换码</strong>（本页右侧）：老系统遗留的码，只查旧码；用户兑换后直接生成奖励（订阅时长 / 抵扣额度）。新发放统一走「会员卡码」。</li>
        <li><strong>优惠券</strong>（<router-link to="/coupons">优惠券</router-link>页）：下单付款那一刻直接打折，钱照旧走支付网关。和兑换码的区别：兑换码是「不花钱直接拿东西」，优惠券是「付费时打折」。</li>
        <li><strong>邀请码</strong>（<router-link to="/invitations">邀请</router-link>页）：邀请好友注册用的码。</li>
      </ul>
    </el-alert>

    <div class="tab-bar" role="tablist" aria-label="码类型">
      <button
        id="codes-tab-regcode"
        type="button"
        role="tab"
        class="tab-btn"
        :class="{ active: activeTab === 'regcode' }"
        :aria-selected="activeTab === 'regcode'"
        aria-controls="codes-panel"
        @click="activeTab = 'regcode'"
      >
        注册码
        <span class="tab-hint">只用于注册页 · 验码后建号核销</span>
      </button>
      <button
        id="codes-tab-reg"
        type="button"
        role="tab"
        class="tab-btn"
        :class="{ active: activeTab === 'reg' }"
        :aria-selected="activeTab === 'reg'"
        aria-controls="codes-panel"
        @click="activeTab = 'reg'"
      >
        会员卡码
        <span class="tab-hint">续期 / 折扣 / 白名单</span>
      </button>
      <button
        id="codes-tab-exchange"
        type="button"
        role="tab"
        class="tab-btn"
        :class="{ active: activeTab === 'exchange' }"
        :aria-selected="activeTab === 'exchange'"
        aria-controls="codes-panel"
        @click="activeTab = 'exchange'"
      >
        旧版兑换码
        <span class="tab-hint">只查旧码 · 新发放走会员卡码</span>
      </button>
    </div>

    <div
      id="codes-panel"
      class="tab-panel"
      role="tabpanel"
      :aria-labelledby="activeTab === 'regcode' ? 'codes-tab-regcode' : activeTab === 'reg' ? 'codes-tab-reg' : 'codes-tab-exchange'"
    >
      <!-- KeepAlive：来回切标签不重拉数据、不丢筛选 -->
      <KeepAlive>
        <RegistrationCodes v-if="activeTab === 'regcode'" variant="register" />
        <RegistrationCodes v-else-if="activeTab === 'reg'" variant="redeem" />
        <ExchangeCodes v-else />
      </KeepAlive>
    </div>
  </div>
</template>

<style scoped>
/* 外层间距交给 .admin-content（不再自带 padding / max-width，否则手机上左右各多出 16px） */
.division-alert { margin-bottom: 14px; }
.division-list { margin: 4px 0 0; padding-left: 18px; display: flex; flex-direction: column; gap: 4px; font-size: 12.5px; line-height: 1.6; }
.division-list a { color: var(--au-primary); }
.tab-bar {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--au-border);
  overflow-x: auto;
  scrollbar-width: none;
}
.tab-btn {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  flex-shrink: 0;
  padding: 10px 18px;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--au-text-2);
  font: inherit;
  font-size: 15px;
  font-weight: 500;
  cursor: pointer;
  margin-bottom: -1px;
  transition: color var(--au-fast) var(--au-ease), border-color var(--au-fast) var(--au-ease);
}
.tab-btn:hover { color: var(--au-text); }
.tab-btn:focus-visible { outline: 2px solid var(--au-border-focus); outline-offset: -2px; }
.tab-btn.active {
  color: var(--au-primary);
  border-bottom-color: var(--au-primary);
}
.tab-hint {
  font-size: 12px;
  color: var(--au-text-3);
  font-weight: 400;
  white-space: nowrap;
}
.tab-btn.active .tab-hint { color: var(--au-primary); }
.tab-panel { min-width: 0; }

@media (max-width: 640px) {
  /* Batch F：手机端不再藏 hint（以前这里 display:none，只剩两个秃词）——hint 缩小保留 */
  .tab-btn { padding: 8px 12px; font-size: 14px; }
  .tab-hint { font-size: 11px; }
}
</style>
