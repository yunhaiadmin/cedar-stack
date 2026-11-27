<script setup lang="ts">
/**
 * NewbieGuideView.vue —— 新人指南（用户中心 · 帮助组）
 *
 * 静态内容页（2026-11-27 用户亲令）：给第一次来的新用户看的上手
 * 说明，只放站内、登录后可见，不发任何外部渠道。内容为前端写死
 * 的静态文案，不走后端接口、不做管理端可编辑。事实口径（服务器
 * 地址、套餐价格、封禁播放器名单等）以本页注释与文案为准，改价
 * 或改名单时同步改这里。
 */
import AxAccountLayout from '@/components/ax/AxAccountLayout.vue'
</script>

<template>
  <AxAccountLayout title="新人指南" eyebrow="帮助">
    <div class="guide-page">
      <p class="guide-lede">第一次来 Aetrix？照着下面一步步来，几分钟就能看上片。</p>

      <section class="guide-card">
        <h2 class="guide-card-title">1. 注册与登录</h2>
        <ol class="guide-steps">
          <li>在网站注册一个账号，注册是开放的，填好信息就能注册成功。</li>
          <li>本站没有免费试用：注册完先买订阅才能看片（见第 2 步）。</li>
          <li>网站账号就是以后在播放器里登录用的账号，一套账号密码走天下，不用记两套。</li>
        </ol>
      </section>

      <section class="guide-card">
        <h2 class="guide-card-title">2. 先买订阅才能播</h2>
        <ol class="guide-steps">
          <li>没有有效订阅，可以逛片库、看有什么片，但点播放看不了。</li>
          <li>
            在售套餐两档：
            <ul class="guide-list">
              <li>Global 国际版：$1.2/月，每月 500GB 流量。</li>
              <li>Global 家庭版：$3.6/月，每月 1.5TB 流量，能建子账号，还能同时播放 2 路（两台设备一起看）。</li>
            </ul>
          </li>
          <li>在「订阅服务」页选好套餐，用钱包余额付款就开通了（钱包怎么充见第 3 步）。</li>
        </ol>
      </section>

      <section class="guide-card">
        <h2 class="guide-card-title">3. 怎么付钱</h2>
        <ol class="guide-steps">
          <li>先给「我的钱包」充值，再用余额买订阅、买流量。</li>
          <li>充值最低 $1；用人民币付就行，系统按汇率自动折算成美元余额到账。</li>
          <li>流量不够用可以单独买：$0.30/100GB 起，按需加。</li>
        </ol>
      </section>

      <section class="guide-card">
        <h2 class="guide-card-title">4. 怎么看片</h2>
        <ol class="guide-steps">
          <li>装一个播放器 App（推荐见下面），打开后找「添加服务器」。</li>
          <li>
            服务器地址填：
            <span class="guide-addr">https://cdn.aetrix.love</span>
          </li>
          <li>再用你的网站账号密码登录，就能看片了。</li>
          <li>推荐播放器：Rex、SenPlayer、官方 Emby 客户端。</li>
          <li>这几个播放器已被本站封禁，用它们登录会被拦，别用：Filmly（爆米花）、Reflix、ai-player。</li>
        </ol>
      </section>

      <section class="guide-card">
        <h2 class="guide-card-title">5. 子账号</h2>
        <ol class="guide-steps">
          <li>家庭版可以给家人开子账号：去「团队管理」页里新建。</li>
          <li>建好后，对方用子账号自己的用户名和密码登录就能看。</li>
        </ol>
      </section>

      <section class="guide-card">
        <h2 class="guide-card-title">6. 想看的片没有怎么办</h2>
        <ol class="guide-steps">
          <li>去「求片反馈」页，先搜一下片名——说不定站里已经有，只是名字不一样。</li>
          <li>确实没有，再提交求片，把片名写清楚。</li>
          <li>提交后等处理，有结果会在求片反馈里回复你。</li>
        </ol>
      </section>

      <section class="guide-card">
        <h2 class="guide-card-title">7. 常见问题</h2>
        <ul class="guide-list">
          <li>登不上？按顺序查三件事：① 服务器地址是不是 https://cdn.aetrix.love，一个字都别错；② 账号密码对不对；③ 订阅是不是过期了。</li>
          <li>播放卡？先去「线路测速」测一下，换一条快的线路再播。</li>
          <li>4K 片子放不动？用客户端 App（Rex、SenPlayer、官方 Emby）看 4K，别用网页播。</li>
        </ul>
      </section>

      <section class="guide-card">
        <h2 class="guide-card-title">8. 邀请好友</h2>
        <ol class="guide-steps">
          <li>去「邀请好友」页，复制你的邀请链接发给朋友。</li>
          <li>朋友通过你的链接注册并付费，你就能拿到返利。</li>
        </ol>
      </section>
    </div>
  </AxAccountLayout>
</template>

<style scoped>
/* 用户中心框架（AxAccountLayout）内容面令牌与同框架页面同口径：
   实色卡面、无玻璃，手机端可读。 */
.guide-page {
  font-family: var(--ax-font-sans);
  color: var(--ax-l-text);
  max-width: 720px;
}
.guide-lede {
  margin: 0 0 20px;
  font-size: var(--ax-fs-body);
  color: var(--ax-l-text-2);
}
.guide-card {
  background: var(--ax-l-surface);
  border: 1px solid var(--ax-l-border);
  border-radius: var(--ax-radius-card);
  padding: 20px;
  margin-bottom: 20px;
}
.guide-card-title {
  margin: 0 0 12px;
  font-size: var(--ax-fs-title);
  font-weight: var(--ax-fw-bold);
  color: var(--ax-l-text);
}
.guide-steps,
.guide-list {
  margin: 0;
  padding-left: 22px;
  font-size: var(--ax-fs-body);
  line-height: 1.75;
  color: var(--ax-l-text);
}
.guide-steps li,
.guide-list li {
  margin-bottom: 6px;
}
.guide-steps li:last-child,
.guide-list li:last-child {
  margin-bottom: 0;
}
.guide-steps .guide-list {
  margin-top: 6px;
}
.guide-addr {
  display: block;
  margin: 8px 0;
  padding: 10px 14px;
  border: 1px solid var(--ax-l-border);
  border-radius: var(--ax-radius-input);
  background: var(--ax-l-input-bg);
  font-weight: var(--ax-fw-semibold);
  word-break: break-all;
  user-select: all;
}
</style>
