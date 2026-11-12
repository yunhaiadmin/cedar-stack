<script setup lang="ts">
/**
 * UHD 组件展示页（批 0 验收用，隐藏路由 /dev/uhd-showcase）
 * 同页并排摆深色营销面与浅色办事面两组组件样品，数据全为静态样品。
 * 不进任何导航、不被现有页面引用。
 */
import { ref } from 'vue'
import {
  Bell,
  BookOpen,
  Clapperboard,
  CreditCard,
  Download,
  History,
  LayoutGrid,
  Monitor,
  Music2,
  Server,
  ShieldCheck,
  Smartphone,
  Tv,
  UserPlus,
  Users,
  Wallet,
} from 'lucide-vue-next'
import UhdButton from '@/components/uhd/UhdButton.vue'
import UhdChip from '@/components/uhd/UhdChip.vue'
import UhdEmptyState from '@/components/uhd/UhdEmptyState.vue'
import UhdMemberCard from '@/components/uhd/UhdMemberCard.vue'
import UhdModal from '@/components/uhd/UhdModal.vue'
import UhdNavPill from '@/components/uhd/UhdNavPill.vue'
import UhdSegmented from '@/components/uhd/UhdSegmented.vue'
import UhdSidebar from '@/components/uhd/UhdSidebar.vue'
import UhdStatusRow from '@/components/uhd/UhdStatusRow.vue'
import UhdTable from '@/components/uhd/UhdTable.vue'

const navActive = ref('home')
const navItems = [
  { key: 'home', label: '首页' },
  { key: 'pricing', label: '定价' },
  {
    key: 'community',
    label: '社区',
    children: [
      { label: '官方频道', desc: '公告与更新' },
      { label: '官方群组', desc: '用户交流' },
      { label: '互助群', desc: '问题互助' },
    ],
  },
  { key: 'docs', label: '文档' },
]

const segValue = ref('cn')
const segOptions = [
  { label: '国内优化线路', value: 'cn' },
  { label: '国际线路', value: 'global' },
]

const filterValue = ref('all')
const filterOptions = [
  { label: '全部', value: 'all' },
  { label: '我的请求', value: 'mine' },
  { label: '请求广场', value: 'plaza' },
]

const sideActive = ref('overview')
const sideGroups = [
  {
    items: [{ key: 'overview', label: '概览', icon: LayoutGrid }],
  },
  {
    title: '订阅与财务',
    items: [
      { key: 'plans', label: '订阅服务', icon: CreditCard },
      { key: 'records', label: '订阅记录', icon: History },
      { key: 'wallet', label: '钱包', icon: Wallet },
    ],
  },
  {
    title: '账户',
    items: [
      { key: 'security', label: '账号安全', icon: ShieldCheck },
      { key: 'invite', label: '邀请好友', icon: UserPlus },
      { key: 'team', label: '团队管理', icon: Users },
    ],
  },
  {
    title: '帮助',
    items: [
      { key: 'requests', label: '求片反馈', icon: Clapperboard },
      { key: 'install', label: '安装应用', icon: Download },
      { key: 'docs', label: '使用说明', icon: BookOpen },
    ],
  },
]

const tableColumns = [
  { key: 'plan', label: '套餐' },
  { key: 'detail', label: '详情' },
  { key: 'time', label: '时间' },
  { key: 'expiry', label: '到期' },
]
const tableRows = [
  { id: 1, plan: '环游', detail: '年付 · 积分支付', time: '2026-11-01', expiry: '2027-11-01' },
  { id: 2, plan: '远行', detail: '季付 · 余额支付', time: '2026-08-03', expiry: '2026-11-03' },
]

const modalOpen = ref(false)
</script>

<template>
  <div class="showcase">
    <!-- ================= 深色营销面 ================= -->
    <section class="showcase__dark">
      <UhdNavPill :items="navItems" :active-key="navActive" cta-text="立即加入" @select="navActive = $event" />

      <div class="showcase__hero">
        <p class="showcase__eyebrow">AETRIX SERVICE</p>
        <h1 class="showcase__h1">为热爱观影的人<br />打造私人影音世界</h1>
        <p class="showcase__sub">无广告干扰，尽享纯净观影体验。</p>
        <div class="showcase__hero-btns">
          <UhdButton variant="primary" dark>免费注册</UhdButton>
          <UhdButton variant="secondary" dark>查看定价</UhdButton>
          <UhdButton variant="play" dark>立即播放</UhdButton>
        </div>
        <div class="showcase__specs">
          <UhdChip variant="spec" sub="超高清"><template #icon><Tv /></template>4K</UhdChip>
          <UhdChip variant="spec" sub="高动态范围"><template #icon><Monitor /></template>HDR10+</UhdChip>
          <UhdChip variant="spec" sub="杜比视界"><template #icon><Smartphone /></template>Dolby Vision</UhdChip>
          <UhdChip variant="spec" sub="沉浸体验"><template #icon><Music2 /></template>无广告干扰</UhdChip>
        </div>
      </div>

      <div class="showcase__dark-card">
        <p class="showcase__eyebrow">MULTI-DEVICE SYNC</p>
        <h3 class="showcase__h3">一个账户，跨越每一块屏幕</h3>
        <p class="showcase__dark-desc">深色服务卡样品：#0d0d0d 底 + 1px 边框 + 极淡网格纹。</p>
        <div class="showcase__chips">
          <UhdChip variant="mini">TV</UhdChip>
          <UhdChip variant="mini">Computer</UhdChip>
          <UhdChip variant="mini">Tablet</UhdChip>
          <UhdChip variant="mini">手机</UhdChip>
          <UhdChip variant="badge">推荐</UhdChip>
        </div>
      </div>
    </section>

    <!-- ================= 浅色办事面 ================= -->
    <section class="showcase__light">
      <div class="showcase__frame">
        <UhdSidebar :groups="sideGroups" :active-key="sideActive" class="showcase__side" @select="sideActive = $event">
          <template #account>
            <div class="showcase__account">
              <span class="showcase__avatar">X</span>
              <span class="showcase__account-main">
                <span class="showcase__account-name">xiaye</span>
                <span class="showcase__account-status">● 已订阅</span>
              </span>
              <span class="showcase__account-caret">▾</span>
            </div>
          </template>
        </UhdSidebar>

        <div class="showcase__content">
          <h1 class="showcase__h1-app">概览</h1>

          <div class="showcase__notice">
            <Bell class="showcase__notice-icon" />
            <span>系统公告样品</span>
            <span class="showcase__notice-more">查看 ›</span>
          </div>

          <div class="showcase__warn">
            <span class="showcase__warn-text">会员状态需要关注时显示琥珀提示条</span>
            <UhdButton variant="primary" size="sm">立即续费</UhdButton>
          </div>

          <UhdMemberCard
            plan-name="环游 · 年付"
            user-line="xiaye · ID 01JXAMPLE0000000000000000"
            status-text="有效"
            note="有效期至 2027-11-01"
          />

          <h2 class="showcase__h2">服务器</h2>
          <div class="showcase__rows">
            <UhdStatusRow title="服务器地址" desc="cdn.aetrix.love · 已分配线路" status-text="在线" tone="ok" chevron clickable>
              <template #icon><Server /></template>
            </UhdStatusRow>
            <UhdStatusRow title="弹幕 API" desc="生成 Token，填入播放器的弹幕设置" chevron clickable>
              <template #icon><Music2 /></template>
            </UhdStatusRow>
            <UhdStatusRow title="恢复邮箱" desc="x***@gmail.com" status-text="已设置" tone="ok">
              <template #action><UhdButton variant="secondary" size="sm">更换</UhdButton></template>
            </UhdStatusRow>
            <UhdStatusRow title="Passkey" desc="使用设备生物识别登录" status-text="尚未绑定" tone="idle" :divider="false">
              <template #action><UhdButton variant="secondary" size="sm">添加</UhdButton></template>
            </UhdStatusRow>
          </div>
        </div>
      </div>

      <div class="showcase__panel">
        <h2 class="showcase__h2">分段切换与状态筛选</h2>
        <div class="showcase__line">
          <UhdSegmented v-model="segValue" :options="segOptions" />
          <UhdSegmented v-model="filterValue" :options="filterOptions" />
        </div>
        <div class="showcase__line">
          <UhdChip variant="status" dot="idle">待审核</UhdChip>
          <UhdChip variant="status" dot="doing">处理中</UhdChip>
          <UhdChip variant="status" dot="ok">已完成</UhdChip>
          <UhdChip variant="status" dot="bad">已拒绝</UhdChip>
        </div>

        <h2 class="showcase__h2">按钮全态</h2>
        <div class="showcase__line">
          <UhdButton variant="primary">主按钮</UhdButton>
          <UhdButton variant="secondary">次按钮</UhdButton>
          <UhdButton variant="primary" size="sm">小胶囊</UhdButton>
          <UhdButton variant="danger">退出登录</UhdButton>
          <UhdButton variant="primary" disabled>禁用态</UhdButton>
          <UhdButton variant="primary" @click="modalOpen = true">打开弹窗</UhdButton>
        </div>

        <div class="showcase__login-card">
          <h3 class="showcase__login-title">登录卡样品</h3>
          <label class="showcase__field">
            <span>用户名</span>
            <input type="text" placeholder="3–16 个字符" />
          </label>
          <label class="showcase__field">
            <span>密码</span>
            <input type="password" placeholder="至少 8 位" />
          </label>
          <UhdButton variant="primary" block>登录</UhdButton>
          <UhdButton variant="secondary" block>使用 Passkey 登录</UhdButton>
        </div>

        <h2 class="showcase__h2">表格</h2>
        <UhdTable :columns="tableColumns" :rows="tableRows">
          <template #toolbar>
            <select class="uhd-tool-pill"><option>全部明细</option></select>
            <input class="uhd-tool-pill" type="text" placeholder="搜索" />
            <button type="button" class="uhd-tool-pill">刷新</button>
          </template>
        </UhdTable>

        <h2 class="showcase__h2">空态</h2>
        <UhdEmptyState title="还没有订阅记录" />
      </div>
    </section>

    <UhdModal v-model="modalOpen" title="弹幕 API">
      <p>Token 区样品：尚未生成。生成后填入播放器的弹幕设置。</p>
      <template #footer>
        <UhdButton variant="secondary" @click="modalOpen = false">取消</UhdButton>
        <UhdButton variant="primary" @click="modalOpen = false">生成 Token</UhdButton>
      </template>
    </UhdModal>
  </div>
</template>

<style scoped>
.showcase {
  font-family: var(--uhd-font-sans);
}
/* ---------- 深色营销面 ---------- */
.showcase__dark {
  background: var(--uhd-d-bg);
  color: var(--uhd-d-text);
  padding-bottom: 84px;
}
.showcase__hero {
  max-width: var(--uhd-container-marketing);
  margin: 0 auto;
  padding: 104px 28px 0;
}
.showcase__eyebrow {
  margin: 0;
  font-size: var(--uhd-fs-aux);
  letter-spacing: var(--uhd-ls-eyebrow);
  color: var(--uhd-d-text-2);
  text-transform: uppercase;
}
.showcase__h1 {
  margin: 18px 0 0;
  font-family: var(--uhd-font-display);
  font-size: var(--uhd-fs-h1);
  font-weight: var(--uhd-fw-black);
  line-height: var(--uhd-lh-tight);
  letter-spacing: -0.01em;
}
.showcase__sub {
  margin: 20px 0 0;
  font-size: var(--uhd-fs-lead);
  color: var(--uhd-d-text-2);
}
.showcase__hero-btns {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 32px;
}
.showcase__specs {
  display: flex;
  gap: 36px;
  flex-wrap: wrap;
  margin-top: 68px;
}
.showcase__specs :deep(svg) {
  width: 20px;
  height: 20px;
}
.showcase__dark-card {
  max-width: var(--uhd-container-marketing);
  margin: 76px auto 0;
  width: calc(100% - 56px);
  padding: 42px;
  background-color: var(--uhd-d-surface);
  background-image: var(--uhd-grid-tex);
  background-size: var(--uhd-grid-tex-size);
  border: var(--uhd-border-w) solid var(--uhd-d-border);
  border-radius: var(--uhd-radius-card);
}
.showcase__h3 {
  margin: 12px 0 0;
  font-size: 36px;
  font-weight: var(--uhd-fw-extrabold);
}
.showcase__dark-desc {
  margin: 12px 0 0;
  font-size: var(--uhd-fs-body);
  color: var(--uhd-d-text-2);
}
.showcase__chips {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: center;
  margin-top: 26px;
}
</style>

<style scoped>
/* ---------- 浅色办事面 ---------- */
.showcase__light {
  background: var(--uhd-l-bg);
  padding: 56px 28px 90px;
}
.showcase__frame {
  display: flex;
  max-width: 1290px;
  margin: 0 auto;
  background: var(--uhd-l-surface);
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-card);
  box-shadow: var(--uhd-shadow-card-l);
  overflow: hidden;
}
.showcase__side { min-height: 660px; }
.showcase__content {
  flex: 1;
  min-width: 0;
  max-width: var(--uhd-container-app);
  padding: 32px 38px 40px;
}
.showcase__h1-app {
  margin: 0 0 18px;
  font-size: var(--uhd-fs-h1-app);
  font-weight: var(--uhd-fw-extrabold);
  color: var(--uhd-l-text);
}
.showcase__h2 {
  margin: 30px 0 8px;
  font-size: var(--uhd-fs-h3);
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-l-text);
}
.showcase__notice {
  display: flex;
  align-items: center;
  gap: 9px;
  height: 44px;
  padding: 0 16px;
  border-radius: 22px;
  background: var(--uhd-l-input-bg);
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text);
}
.showcase__notice-icon { width: 17px; height: 17px; }
.showcase__notice-more { margin-left: auto; color: var(--uhd-l-text-2); }
.showcase__warn {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 16px;
  padding: 16px 20px;
  border-radius: var(--uhd-radius-card-sm);
  background: var(--uhd-warn-bg-2);
}
.showcase__warn-text {
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-warn-text);
}
.showcase__content :deep(.uhd-member) { margin-top: 22px; }
.showcase__rows :deep(.uhd-row__icon svg) { width: 22px; height: 22px; }
.showcase__account {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 9px;
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-pill);
  background: var(--uhd-l-surface);
}
.showcase__avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex: none;
  border-radius: 50%;
  background: var(--uhd-l-input-bg);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-bold);
  color: var(--uhd-l-text-2);
}
.showcase__account-main { display: flex; flex-direction: column; flex: 1; min-width: 0; }
.showcase__account-name { font-size: var(--uhd-fs-body); font-weight: var(--uhd-fw-semibold); color: var(--uhd-l-text); }
.showcase__account-status { font-size: var(--uhd-fs-aux-sm); color: var(--uhd-success); }
.showcase__account-caret { color: var(--uhd-l-text-3); font-size: 11px; }
.showcase__panel {
  max-width: 1290px;
  margin: 26px auto 0;
  padding: 8px 38px 36px;
  background: var(--uhd-l-surface);
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-card);
  box-shadow: var(--uhd-shadow-card-l);
}
.showcase__line {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin: 12px 0 20px;
}
.showcase__login-card {
  width: min(445px, 100%);
  margin: 6px 0 28px;
  padding: 30px;
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: 20px;
  box-shadow: var(--uhd-shadow-card-l);
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.showcase__login-title {
  margin: 0 0 6px;
  font-size: var(--uhd-fs-h3-lg);
  font-weight: var(--uhd-fw-extrabold);
  color: var(--uhd-l-text);
}
.showcase__field {
  display: flex;
  flex-direction: column;
  gap: 7px;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text);
}
.showcase__field input {
  height: 44px;
  padding: 0 14px;
  border: var(--uhd-border-w) solid var(--uhd-l-border);
  border-radius: var(--uhd-radius-input);
  background: var(--uhd-l-input-bg);
  font-size: var(--uhd-fs-body);
  font-family: var(--uhd-font-sans);
  color: var(--uhd-l-text);
}
@media (max-width: 760px) {
  .showcase__h1 { font-size: 42px; }
  .showcase__hero { padding-top: 84px; }
  .showcase__frame { flex-direction: column; }
  .showcase__side { width: 100%; min-height: 0; }
  .showcase__content, .showcase__panel { padding-left: 20px; padding-right: 20px; }
  .showcase__light { padding: 36px 14px 60px; }
}
</style>
