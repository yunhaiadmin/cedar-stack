<template>
  <!-- 桌面并壳（2026-11-14 统一壳单）：≥769px 经闸门进用户中心壳；手机原样 -->
  <UhdAccountShellGate>
  <div class="tm-page">
    <div class="tm-inner">
      <header class="tm-head">
        <h1 class="tm-title">团队管理</h1>
      </header>

      <div v-if="loadError" class="tm-err">
        {{ loadError }}<button type="button" class="tm-retry" @click="loadDetail">重试</button>
      </div>
      <div v-else-if="loading" class="tm-loading" role="status" aria-live="polite">
        <span class="tm-spin" aria-hidden="true"></span>
        <p class="tm-loading__text">正在加载团队…</p>
      </div>

      <template v-else-if="detail">
        <!-- 家庭版已购态：套餐名＋剩余名额＋更改为团队＋统计四格＋下载权限行（照友站家庭版页 f1） -->
        <section v-if="familyMode && family" class="tm-family">
          <h2 class="tm-family__title">{{ family.planName }}</h2>
          <p class="tm-family__sub">{{ family.subLine }}</p>
          <div class="tm-family__actions">
            <button type="button" class="tm-family__refresh" aria-label="刷新" @click="loadDetail">
              <RefreshCw :size="18" aria-hidden="true" />
            </button>
            <UhdButton variant="primary" size="sm" :dark="themeResolved === 'dark'" @click="openBuy">
              更改为团队 <ArrowRight :size="16" aria-hidden="true" />
            </UhdButton>
          </div>

          <div class="tm-family__stats">
            <div class="tm-fam-stat">
              <p class="tm-fam-stat__label">成员</p>
              <p class="tm-fam-stat__value">{{ family.memberUsed }} <span class="tm-fam-stat__unit">/ {{ family.memberLimit }}</span></p>
            </div>
            <div class="tm-fam-stat">
              <p class="tm-fam-stat__label">团队总流量</p>
              <p class="tm-fam-stat__value">{{ quotaGbNumber(family.trafficQuotaBytes) }} <span class="tm-fam-stat__unit">GB</span></p>
            </div>
            <div class="tm-fam-stat">
              <p class="tm-fam-stat__label">同时播放</p>
              <p class="tm-fam-stat__value">{{ family.streams ?? '—' }} <span v-if="family.streams != null" class="tm-fam-stat__unit">路</span></p>
            </div>
            <div class="tm-fam-stat">
              <p class="tm-fam-stat__label">套餐到期</p>
              <p class="tm-fam-stat__value">{{ family.expiresDate }} <span v-if="family.expiresTime" class="tm-fam-stat__unit">{{ family.expiresTime }}</span></p>
            </div>
          </div>

          <div v-if="family.downloadAllowed != null" class="tm-family__dl">
            <div class="tm-family__dl-text">
              <p class="tm-family__dl-title">套餐默认下载权限</p>
              <p class="tm-family__dl-desc">仅用于新家庭成员的初始权限。</p>
            </div>
            <span class="tm-family__dl-value" :class="{ 'tm-family__dl-value--off': !family.downloadAllowed }">
              {{ family.downloadAllowed ? '已允许' : '未允许' }}
            </span>
          </div>
        </section>

        <!-- 介绍卡：标题＋副文＋三列数据＋客服审核提示＋彩虹创建钮（照友站 §8.2） -->
        <section v-else class="tm-card tm-intro">
          <div class="tm-intro__head">
            <span class="tm-intro__icon" aria-hidden="true">
              <ShieldCheck :size="26" />
            </span>
            <div class="tm-intro__text">
              <h2 class="tm-intro__title">为团队分配独立账号</h2>
              <p class="tm-intro__desc">选择线路和总流量，成员配额会按套餐规则自动计算。</p>
            </div>
          </div>

          <div class="tm-stats">
            <template v-if="team">
              <div class="tm-stat">
                <p class="tm-stat__label">线路</p>
                <p class="tm-stat__value">{{ teamLineName }}</p>
              </div>
              <div class="tm-stat">
                <p class="tm-stat__label">套餐总流量</p>
                <p class="tm-stat__value">{{ formatGb(team.plan_traffic_gb) }}</p>
              </div>
              <div class="tm-stat">
                <p class="tm-stat__label">成员配额</p>
                <p class="tm-stat__value">{{ team.member_count }} / {{ team.plan_member_limit ?? '—' }}</p>
              </div>
            </template>
            <template v-else>
              <div class="tm-stat">
                <p class="tm-stat__label">Global 最低起订</p>
                <p class="tm-stat__value">{{ formatGb(config.team_global_min_traffic_gb) }}</p>
              </div>
              <div class="tm-stat">
                <p class="tm-stat__label">Premium 最低起订</p>
                <p v-if="premiumPaused" class="tm-stat__value tm-stat__value--off">暂停服务</p>
                <p v-else class="tm-stat__value">{{ formatGb(config.team_premium_min_traffic_gb) }}</p>
              </div>
              <div class="tm-stat">
                <p class="tm-stat__label">成员配额</p>
                <p class="tm-stat__value">自动计算</p>
              </div>
            </template>
          </div>

          <p v-if="team && team.status === 'pending_activation'" class="tm-pending" role="alert">
            套餐已购买，播放权限待开通——请联系客服开通播放权限后，成员才能正常使用。
          </p>
          <p v-else-if="team && team.status === 'active'" class="tm-active-line">
            团队套餐已开通，有效期至 {{ formatTeamDate(team.plan_expires_at) }}。
          </p>

          <div class="tm-review">
            <div class="tm-review__text">
              <p class="tm-review__title">支付前请先联系客服审核</p>
              <p class="tm-review__desc">
                结算金额可随时预览；为保障服务安全，请先联系客服审核、审核通过后再支付。
                购买成功后还需联系客服开通播放权限，否则无法使用。
              </p>
            </div>
            <UhdButton
              v-if="ctaMode === 'buy'"
              variant="primary"
              :dark="themeResolved === 'dark'"
              @click="openBuy"
            >
              <Plus :size="16" aria-hidden="true" />{{ team ? '升级 / 续订团队套餐' : '创建团队套餐' }}
            </UhdButton>
            <p v-else-if="ctaMode === 'sub'" class="tm-cta-note">
              子账号不能创建团队套餐，请用主账号操作。
            </p>
            <p v-else-if="ctaMode === 'closed'" class="tm-cta-note">
              团队购买暂未开放。
            </p>
          </div>
        </section>

        <!-- 团队信息条（有团队才显示）：名称/状态/团长＋改名（团长） -->
        <section v-if="team" class="tm-card tm-teambar">
          <div class="tm-teambar__main">
            <p class="tm-teambar__name">{{ team.name || '未命名团队' }}</p>
            <p class="tm-teambar__sub">
              团长 @{{ team.owner_username || '—' }} · {{ teamLineName }} · 总流量 {{ formatGb(team.plan_traffic_gb) }}
            </p>
          </div>
          <span class="tm-chip" :class="{ 'tm-chip--ok': team.status === 'active' }">
            {{ team.status === 'active' ? '已开通' : '待开通播放权限' }}
          </span>
          <button
            v-if="canManage"
            type="button"
            class="tm-mini-btn"
            @click="openRename"
          >改名</button>
        </section>

        <!-- 成员账号区（常驻）：搜索/筛选/成员表；无团队时空态＋控件禁用（照友站 §8.2） -->
        <section class="tm-members">
          <div class="tm-members__head">
            <h2 class="tm-sec-title">成员账号<span v-if="familyMode" class="tm-members__count">{{ membersTotal }} 个</span></h2>
            <UhdButton
              v-if="familyMode && canManage"
              variant="primary"
              size="sm"
              :dark="themeResolved === 'dark'"
              @click="openMember"
            >
              <UserPlus :size="16" aria-hidden="true" />添加成员
            </UhdButton>
            <button
              v-else-if="canManage"
              type="button"
              class="tm-add-btn"
              :disabled="!team"
              @click="openMember"
            >
              <UserPlus :size="16" aria-hidden="true" />添加成员
            </button>
          </div>
          <p v-if="team && canManage && atLimit" class="tm-limit-note">
            成员已达上限（{{ team.plan_member_limit }} 个子账号），移除一个后可以再添加。
          </p>
          <p v-else-if="familyMode && canManage && familyAtLimit" class="tm-limit-note">
            成员已达上限（{{ family?.memberLimit }} 个子账号），移除一个后可以再添加。
          </p>
          <p v-if="myRole === 'member'" class="tm-limit-note">
            你由团长 @{{ team?.owner_username || '—' }} 创建，订阅与流量按团队套餐计算；如需退出请联系团长。
          </p>

          <div class="tm-toolbar">
            <label class="tm-tool">
              <select v-model="searchField" class="tm-select" :disabled="!membersEnabled" aria-label="搜索方式">
                <option value="name">按名称</option>
              </select>
            </label>
            <label class="tm-tool tm-tool--grow">
              <input
                v-model="qDraft"
                type="text"
                class="tm-input"
                placeholder="输入成员名称"
                aria-label="输入成员名称"
                :disabled="!membersEnabled"
                @keyup.enter="applySearch"
              />
            </label>
            <label class="tm-tool">
              <select v-model="statusFilter" class="tm-select" :disabled="!membersEnabled" aria-label="全部状态" @change="applySearch">
                <option value="all">全部状态</option>
                <option value="active">正常</option>
                <option value="disabled">已禁用</option>
              </select>
            </label>
            <button type="button" class="tm-search-btn" :disabled="!membersEnabled" @click="applySearch">
              <Search :size="15" aria-hidden="true" />搜索
            </button>
          </div>

          <div class="tm-card tm-table-card">
            <div v-if="membersEnabled && membersLoading" class="tm-table-loading">
              <span class="tm-spin tm-spin--sm" aria-hidden="true"></span>正在加载…
            </div>
            <UhdEmptyState
              v-else-if="!membersEnabled"
              title="请先开通团队套餐"
            />
            <UhdEmptyState
              v-else-if="familyMode && !members.length"
              title="还没有成员账号"
            >
              <template #action>
                <button type="button" class="tm-fam-empty-btn" @click="openMember">添加成员</button>
              </template>
            </UhdEmptyState>
            <UhdEmptyState
              v-else-if="!members.length"
              title="还没有成员"
              desc="点右上角「添加成员」，用用户名和密码创建第一个成员账号。"
            />
            <div v-else class="tm-table-wrap">
              <table class="tm-table">
                <thead>
                  <tr>
                    <th>成员</th>
                    <th>有效期</th>
                    <th>流量</th>
                    <th>使用情况</th>
                    <th>重置日</th>
                    <th v-if="canManage">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in members" :key="row.id">
                    <td>
                      <span class="tm-member-name">{{ row.username || '—' }}</span>
                      <span v-if="!row.is_active" class="tm-chip tm-chip--off">已禁用</span>
                      <span v-if="row.public_id" class="tm-member-id">{{ row.public_id }}</span>
                    </td>
                    <td>{{ formatTeamDate(row.expires_at) }}</td>
                    <td>{{ formatBytes(row.traffic_quota_bytes) }}</td>
                    <td>
                      <span v-if="familyMode" class="tm-usage-text">与主账号共享</span>
                      <template v-else>
                        <span class="tm-usage-text">
                          {{ formatBytes(row.traffic_used_bytes) }} / {{ formatBytes(row.traffic_quota_bytes) }}
                          <template v-if="row.traffic_used_pct != null">（{{ row.traffic_used_pct }}%）</template>
                        </span>
                        <span class="tm-usage-bar" aria-hidden="true">
                          <span class="tm-usage-bar__fill" :style="{ width: `${Math.min(100, row.traffic_used_pct ?? 0)}%` }"></span>
                        </span>
                      </template>
                    </td>
                    <td>{{ formatTeamDate(row.reset_date) }}</td>
                    <td v-if="canManage">
                      <button type="button" class="tm-remove-btn" @click="askRemove(row)">移除</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-if="membersEnabled && membersTotal > pageSize" class="tm-pager">
              <button type="button" class="tm-mini-btn" :disabled="page <= 1 || membersLoading" @click="goPage(page - 1)">上一页</button>
              <span class="tm-pager__info">第 {{ page }} 页 · 共 {{ membersTotal }} 人</span>
              <button type="button" class="tm-mini-btn" :disabled="page * pageSize >= membersTotal || membersLoading" @click="goPage(page + 1)">下一页</button>
            </div>
          </div>
        </section>
      </template>
    </div>

    <!-- 创建 / 升级弹窗（两步：配置 → 结算预览，照友站 §8.3；手机端紧凑卡
         版式照友站录屏实测：浮动卡＋头部＋五块卡＋底部栏常驻，弹层封顶
         82dvh、只在内部滚动。专职 sheet 而非 UhdModal：UhdModal 无高度
         封顶，长内容会溢出屏幕、底部按钮被挤出（用户真机实证）。 -->
    <Teleport to="body">
      <div v-if="buyOpen" class="tm-sheet__overlay" @click.self="closeBuy">
        <div class="tm-sheet" role="dialog" aria-modal="true" aria-label="开通 / 升级团队方案">
          <div class="tm-sheet__head">
            <template v-if="buyStep === 2">
              <button type="button" class="tm-sheet__nav-btn" aria-label="返回配置" @click="buyStep = 1">
                <ArrowLeft :size="20" aria-hidden="true" />
              </button>
              <div class="tm-sheet__titles">
                <h3 class="tm-sheet__title">结算预览</h3>
                <p class="tm-sheet__sub">请确认以下结算信息无误</p>
              </div>
            </template>
            <template v-else>
              <span class="tm-sheet__zap"><Zap :size="20" aria-hidden="true" /></span>
              <div class="tm-sheet__titles">
                <h3 class="tm-sheet__title">开通 / 升级团队方案</h3>
              </div>
            </template>
            <button type="button" class="tm-sheet__nav-btn tm-sheet__close" aria-label="关闭" @click="closeBuy">×</button>
          </div>

          <div class="tm-sheet__body">
            <!-- 预览加载态（友站文案原样） -->
            <div v-if="buyStep === 'loading'" class="tm-buy-loading" role="status" aria-live="polite">
              <span class="tm-spin" aria-hidden="true"></span>
              <p>结算金额计算中…</p>
            </div>

            <!-- 第 1 步：配置 -->
            <div v-else-if="buyStep === 1" class="tm-buy">
              <p v-if="buyError" class="tm-buy-error" role="alert">{{ buyError }}</p>

              <p class="tm-buy__label">方案类型</p>
              <div class="tm-plan-cards" role="group" aria-label="方案类型">
                <button
                  type="button"
                  class="tm-plan-card"
                  :class="{ 'tm-plan-card--on': form.line === 'global' }"
                  :aria-pressed="form.line === 'global'"
                  @click="setLine('global')"
                >
                  <span class="tm-plan-card__name">Global</span>
                  <span class="tm-plan-card__sub">常规线路</span>
                </button>
                <button
                  type="button"
                  class="tm-plan-card"
                  :class="{ 'tm-plan-card--on': form.line === 'premium', 'tm-plan-card--off': premiumPaused }"
                  :aria-pressed="form.line === 'premium'"
                  :disabled="premiumPaused"
                  @click="setLine('premium')"
                >
                  <span class="tm-plan-card__name">Premium</span>
                  <span class="tm-plan-card__sub">{{ premiumPaused ? '暂停服务' : '高级线路' }}</span>
                </button>
              </div>

              <section class="tm-buy-card">
                <div class="tm-buy-card__row">
                  <div class="tm-buy-card__main">
                    <p class="tm-buy-card__label">同时播放</p>
                    <p class="tm-buy-card__desc">团队通用值，修改时同步现有成员；新成员默认使用，0 表示不限</p>
                  </div>
                  <label class="tm-capsule tm-capsule--sm">
                    <input
                      v-model.number="form.streams"
                      type="number"
                      min="0"
                      step="1"
                      aria-label="同时播放路数"
                    />
                    <span class="tm-capsule__unit">路</span>
                  </label>
                </div>
              </section>

              <section class="tm-buy-card">
                <div class="tm-buy-card__row">
                  <div class="tm-buy-card__main">
                    <p class="tm-buy-card__label">团队总流量</p>
                    <p class="tm-buy-card__conv">{{ formatGb(form.traffic_gb || 0) }}</p>
                  </div>
                  <label class="tm-capsule tm-capsule--lg">
                    <input
                      v-model.number="form.traffic_gb"
                      type="number"
                      :min="lineMin"
                      :max="config.team_max_traffic_gb"
                      step="100"
                      aria-label="订购流量（GB）"
                    />
                    <span class="tm-capsule__unit">GB</span>
                  </label>
                </div>
                <input
                  v-model.number="form.traffic_gb"
                  type="range"
                  class="tm-range"
                  :min="lineMin"
                  :max="config.team_max_traffic_gb"
                  step="100"
                  aria-label="订购流量滑块"
                />
                <div class="tm-slider-caps">
                  <span>最低 {{ lineMin.toLocaleString() }} GB</span>
                  <span>最高 {{ config.team_max_traffic_gb.toLocaleString() }} GB</span>
                </div>
              </section>

              <section class="tm-buy-card">
                <div class="tm-buy-card__row">
                  <p class="tm-buy-card__label">订购时长</p>
                  <p class="tm-buy-card__value">{{ form.months }}个月</p>
                </div>
                <input
                  v-model.number="form.months"
                  type="range"
                  class="tm-range"
                  min="1"
                  max="12"
                  step="1"
                  aria-label="订购时长（月）"
                />
              </section>

              <div class="tm-mini-summary" aria-label="配置摘要">
                <div class="tm-mini-card">
                  <Zap :size="14" aria-hidden="true" />
                  <span class="tm-mini-card__label">类型</span>
                  <b class="tm-mini-card__value">{{ form.line === 'global' ? 'Global' : 'Premium' }}</b>
                </div>
                <div class="tm-mini-card">
                  <Gauge :size="14" aria-hidden="true" />
                  <span class="tm-mini-card__label">流量</span>
                  <b class="tm-mini-card__value">{{ (form.traffic_gb || 0).toLocaleString() }} GB</b>
                </div>
                <div class="tm-mini-card">
                  <CalendarDays :size="14" aria-hidden="true" />
                  <span class="tm-mini-card__label">时长</span>
                  <b class="tm-mini-card__value">{{ form.months }}个月</b>
                </div>
              </div>
            </div>

            <!-- 第 2 步：结算预览 -->
            <div v-else-if="buyStep === 2 && preview" class="tm-settle">
              <p class="tm-field-label">团队名称</p>
              <input
                v-model="teamName"
                type="text"
                class="tm-input"
                maxlength="100"
                placeholder="请输入团队名称（可选）"
                aria-label="团队名称（可选）"
              />

              <section class="tm-buy-card tm-settle__card" aria-label="套餐信息">
                <div class="tm-sum-row"><span>类型</span><b>{{ TEAM_LINE_NAMES[preview.line] }}</b></div>
                <div class="tm-sum-row"><span>流量</span><b>{{ preview.traffic_gb.toLocaleString() }} GB</b></div>
                <div class="tm-sum-row"><span>时长</span><b>{{ preview.months }} 个月</b></div>
                <div class="tm-sum-row"><span>子账户数量</span><b>{{ preview.sub_account_limit }}</b></div>
              </section>

              <section class="tm-buy-card tm-settle__card" aria-label="费用明细">
                <div class="tm-sum-row"><span>套餐费用</span><b>{{ formatFen(preview.list_price_fen) }}</b></div>
                <div class="tm-sum-row tm-sum-row--cut">
                  <span>旧套餐抵扣<small class="tm-sum-note">当前套餐剩余价值抵扣</small></span>
                  <b>−{{ formatFen(preview.switch_credit_fen) }}</b>
                </div>
                <div class="tm-sum-row tm-sum-row--total"><span>总计</span><b>{{ formatFen(preview.total_fen) }}</b></div>
                <div class="tm-sum-row"><span>钱包余额</span><b>{{ formatFen(preview.balance_fen) }}</b></div>
              </section>

              <p v-if="!preview.balance_sufficient" class="tm-buy-error" role="alert">
                钱包余额不足，还差 {{ formatFen(preview.total_fen - preview.balance_fen) }}，充值后即可购买。
              </p>
              <p v-if="buyError" class="tm-buy-error" role="alert">{{ buyError }}</p>
            </div>
          </div>

          <div v-if="buyStep !== 'loading'" class="tm-sheet__foot">
            <template v-if="buyStep === 1">
              <button type="button" class="tm-btn tm-btn--ghost" @click="closeBuy">取消</button>
              <UhdButton variant="primary" :dark="themeResolved === 'dark'" @click="doPreview">预览结算金额</UhdButton>
            </template>
            <template v-else-if="buyStep === 2">
              <button type="button" class="tm-btn tm-btn--ghost" :disabled="buying" @click="buyStep = 1">取消</button>
              <UhdButton variant="primary" :dark="themeResolved === 'dark'" :disabled="buying" @click="doPurchase">
                {{ buying ? '处理中…' : '确认并支付' }}
              </UhdButton>
            </template>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 添加成员弹窗 -->
    <UhdModal v-model="memberOpen" title="添加成员" :width="420">
      <form class="tm-member-form" @submit.prevent="doCreateMember">
        <p class="tm-field-label">成员用户名</p>
        <input
          v-model="memberForm.username"
          type="text"
          class="tm-input"
          autocomplete="off"
          placeholder="3–32 位字母、数字或下划线"
          aria-label="成员用户名"
        />
        <p class="tm-field-label">成员密码</p>
        <input
          v-model="memberForm.password"
          type="password"
          class="tm-input"
          autocomplete="new-password"
          placeholder="至少 6 位"
          aria-label="成员密码"
        />
        <p class="tm-member-note">
          {{ familyMode
            ? '成员账号由你创建、用上面的用户名密码登录，订阅与流量按主账号家庭套餐计算；移除成员会停用该账号。'
            : '成员账号由你创建、用上面的用户名密码登录，订阅与流量按团队套餐计算；移除成员会停用该账号。' }}
        </p>
        <p v-if="memberError" class="tm-buy-error" role="alert">{{ memberError }}</p>
      </form>
      <template #footer>
        <button type="button" class="tm-btn tm-btn--ghost" :disabled="memberBusy" @click="memberOpen = false">取消</button>
        <UhdButton variant="primary" :dark="themeResolved === 'dark'" :disabled="memberBusy" @click="doCreateMember">
          {{ memberBusy ? '创建中…' : '创建成员' }}
        </UhdButton>
      </template>
    </UhdModal>

    <!-- 移除成员确认 -->
    <UhdModal v-model="removeOpen" title="移除成员" :width="400">
      <p class="tm-confirm-text">
        确认移除成员 @{{ removeTarget?.username }}？移除后该账号将被停用、无法再登录。
      </p>
      <p v-if="removeError" class="tm-buy-error" role="alert">{{ removeError }}</p>
      <template #footer>
        <button type="button" class="tm-btn tm-btn--ghost" :disabled="removeBusy" @click="removeOpen = false">取消</button>
        <button type="button" class="tm-btn tm-btn--danger" :disabled="removeBusy" @click="doRemoveMember">
          {{ removeBusy ? '处理中…' : '确认移除' }}
        </button>
      </template>
    </UhdModal>

    <!-- 改名弹窗 -->
    <UhdModal v-model="renameOpen" title="修改团队名" :width="400">
      <div class="tm-rename">
        <input
          v-model="renameValue"
          type="text"
          class="tm-input"
          maxlength="100"
          placeholder="请输入团队名称"
          aria-label="团队名称"
          @keyup.enter="doRename"
        />
      </div>
      <p v-if="renameError" class="tm-buy-error" role="alert">{{ renameError }}</p>
      <template #footer>
        <button type="button" class="tm-btn tm-btn--ghost" :disabled="renameBusy" @click="renameOpen = false">取消</button>
        <UhdButton variant="primary" :dark="themeResolved === 'dark'" :disabled="renameBusy" @click="doRename">
          {{ renameBusy ? '保存中…' : '保存' }}
        </UhdButton>
      </template>
    </UhdModal>
  </div>
  </UhdAccountShellGate>
</template>

<script setup lang="ts">
/**
 * 团队管理（/game/account/team，2026-11-18 重建批 2）。
 *
 * 形态照友站团队页走查（说明书 §8）：介绍卡（起订量/成员配额自动
 * 计算）＋彩虹创建钮 → 成员账号区（搜索/状态筛选/六列表；无团队
 * 时空态「请先开通团队套餐」且控件禁用）→ 创建两步弹窗（选线路/
 * 流量/时长 → 结算预览：套餐费/旧套餐抵扣/总计 → 确认并支付）。
 * 数据走批 1 后端 /api/user/teams：价格全由 preview 返回，前端不
 * 自算价；子账户数由 preview 返回（流量/除数），第 1 步摘要不展示。
 *
 * 友站走查未到达的有团后管理动作（添加成员/改名/移除）按接口
 * 形状＋最合理行为推导：成员＝团长创建的子账号（用户名+密码，
 * 与家庭子账号同校验口径）；移除＝二次确认，文案明示账号停用；
 * 成员本人无自助退出接口，页面只给「联系团长」说明。
 *
 * 家庭版已购态（2026-11-18 团队页家庭版状态对齐）：无 Team 行时，
 * 若主账号当前套餐声明了子账号权益（sub_account_limit > 0），
 * 本页即以家庭版为当前团队展示（友站同款：套餐名/剩余名额/统计
 * 四格/下载权限行/成员区），「更改为团队 →」进同一购买弹窗。
 * 数据全部来自既有读口，不新增后端字段——套餐名与到期走
 * /api/user/subscriptions，成员上限/在用/清单走
 * /api/user/sub-accounts，团队总流量与重置日走 /api/user/traffic，
 * 同时播放路数与下载开关走 /api/user/economy/payment/plans
 * （逐套餐 entitlements.concurrent_streams；下载开关为该接口
 * 顶层 download_allowed，即全局＋服策略的既有判定）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import './team-view.css'
import '../../styles/modal-glass.css'
import { ArrowLeft, ArrowRight, CalendarDays, Gauge, Plus, RefreshCw, Search, ShieldCheck, UserPlus, Zap } from 'lucide-vue-next'
import UhdAccountShellGate from '@/components/uhd/UhdAccountShellGate.vue'
import UhdButton from '@/components/uhd/UhdButton.vue'
import UhdEmptyState from '@/components/uhd/UhdEmptyState.vue'
import UhdModal from '@/components/uhd/UhdModal.vue'
import { useTheme } from '@/composables/useTheme'
import { useToast } from '@/composables/useToast'
import { useUserStore } from '@/stores/user'
import { formatBytes, getTraffic } from '@/api/traffic'
import { paymentApi, type SubscriptionPlan } from '@/api/economy'
import { subscriptionApi, type MySubscription } from '@/api'
import { subAccountsApi, type SubAccountItem } from '@/api/subAccounts'
import { newWalletKey } from '@/api/wallet'
import {
  teamsApi,
  TEAM_LINE_NAMES,
  formatFen,
  formatGb,
  formatTeamDate,
  teamErrorMessage,
  type TeamDetail,
  type TeamLine,
  type TeamMemberRow,
  type TeamPreview,
} from '@/api/team'

const toast = useToast()
const userStore = useUserStore()
const { resolved: themeResolved } = useTheme()

/* ---------------- 详情与成员列表 ---------------- */

const loading = ref(true)
const loadError = ref('')
const detail = ref<TeamDetail | null>(null)

const config = computed(() => detail.value!.config)
/** Premium 线暂停服务（后台 team_premium_enabled 关）：卡片置灰不可选 */
const premiumPaused = computed(() => !config.value.team_premium_enabled)
const team = computed(() => detail.value?.team ?? null)
const myRole = computed(() => detail.value?.my_role ?? null)
const isSubAccount = computed(() => !!userStore.user?.is_sub_account)
const atLimit = computed(() => {
  const t = team.value
  return !!t && t.plan_member_limit != null && t.member_count >= t.plan_member_limit
})
const teamLineName = computed(() => {
  const line = team.value?.plan_line
  return line === 'global' || line === 'premium' ? TEAM_LINE_NAMES[line] : '—'
})

/** 介绍卡主钮形态：买（团长/普通用户）/ 子账号提示 / 未开放 / 成员不显示 */
const ctaMode = computed<'buy' | 'sub' | 'closed' | 'none'>(() => {
  if (myRole.value === 'member') return 'none'
  if (!team.value && isSubAccount.value) return 'sub'
  if (!config.value.team_purchase_enabled) return 'closed'
  return 'buy'
})

/* ---------------- 家庭版已购态 ---------------- */

/** 家庭版当前团队状态：字段全部来自既有读口的实值（见文件头注释）。
 * streams 为 null＝套餐未声明 concurrent_streams，页面显示 '—'，
 * 不编造数字；downloadAllowed 为 null＝套餐表读口不可用，下载权限
 * 行整行隐藏，不猜默认值。流量按 1024³ 换算成 GB 整数展示
 * （生产家庭版实值 1,649,267,441,664 字节＝1,536 GB；友站帧图的
 * 1,500 GB 是对方十进制口径，不照抄）。 */
interface FamilyState {
  planName: string
  subLine: string
  memberLimit: number
  memberUsed: number
  remaining: number
  trafficQuotaBytes: number | null
  streams: number | null
  expiresAt: string | null
  expiresDate: string
  expiresTime: string | null
  downloadAllowed: boolean | null
  resetDate: string | null
  periodStart: string | null
  periodEnd: string | null
  items: SubAccountItem[]
}

const family = ref<FamilyState | null>(null)
/** 家庭版形态：无 Team 行且主账号当前套餐有子账号权益 */
const familyMode = computed(() => !team.value && !!family.value)
/** 成员区可用：有团队，或家庭版已购态 */
const membersEnabled = computed(() => !!team.value || familyMode.value)
/** 成员区可管理：团队团长，或家庭版主账号（family 只在主账号加载） */
const canManageFamily = computed(() => familyMode.value)
const canManage = computed(() => myRole.value === 'owner' || canManageFamily.value)
const familyAtLimit = computed(() =>
  !!family.value && family.value.memberUsed >= family.value.memberLimit)

/** 「家庭版 · Global · 剩余 2 个成员名额」：套餐名按空白切分，
 * 首段为线路、余段为短名（"Global 家庭版" → "家庭版 · Global"）；
 * 无空白的套餐名不切分，直接用全名＋剩余名额。 */
function familySubLine(planName: string, remaining: number): string {
  const parts = planName.trim().split(/\s+/)
  if (parts.length >= 2) {
    const [line, ...rest] = parts
    return `${rest.join(' ')} · ${line} · 剩余 ${remaining} 个成员名额`
  }
  return `${planName} · 剩余 ${remaining} 个成员名额`
}

/** ISO 时间 → "2026.12.18 20:09"（友站到期格形）：日期走
 * formatTeamDate，时间直接取 ISO 的 HH:mm 原值，不经 Date 解析，
 * 避免时区转换改写后端存的墙上时间。 */
function formatFamilyDateTime(iso: string | null | undefined): { date: string; time: string | null } {
  if (!iso) return { date: '—', time: null }
  const m = iso.match(/T(\d{2}):(\d{2})/)
  return { date: formatTeamDate(iso), time: m ? `${m[1]}:${m[2]}` : null }
}

/** 字节 → GB 整数（1,649,267,441,664 → "1,536"）；空值回 '—' */
function quotaGbNumber(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return '—'
  return Math.round(bytes / 1024 ** 3).toLocaleString('en-US')
}

async function loadFamily() {
  family.value = null
  const [subsRes, mgmtRes, trafficRes, plansRes] = await Promise.allSettled([
    subscriptionApi.getMine(),
    subAccountsApi.get(),
    getTraffic(),
    paymentApi.plans(),
  ])
  // 两项核心读口任一失败，或当前套餐无子账号权益 → 不是家庭版已购态，
  // 保持现行未开通态（不报错吓人，团队详情本身已加载成功）。
  if (subsRes.status !== 'fulfilled' || mgmtRes.status !== 'fulfilled') return
  const mgmt = mgmtRes.value
  if (mgmt.is_sub_account || mgmt.limit <= 0) return
  const plans: SubscriptionPlan[] = plansRes.status === 'fulfilled'
    ? plansRes.value.plans ?? []
    : []
  // 当前生效的家庭订阅：active 且未过期；套餐表可读时优先取名字能对上
  // 声明了子账号权益的那一份（多服多订阅时不拿错档）；套餐表不可读时
  // 取第一份 active 订阅（limit > 0 本身已证明当前套餐有该权益）。
  const activeSubs = subsRes.value.filter(
    (s: MySubscription) => s.status === 'active' && s.days_left > 0)
  const sub = activeSubs.find((s: MySubscription) =>
    plans.some((p) => p.name === s.plan_name
      && (typeof p.entitlements?.sub_account_limit === 'number'
        ? p.entitlements.sub_account_limit
        : 0) > 0))
    ?? activeSubs[0]
  if (!sub) return
  const matchedPlan = plans.find((p) => p.name === sub.plan_name)
  const traffic = trafficRes.status === 'fulfilled' ? trafficRes.value : null
  const expiry = formatFamilyDateTime(sub.end_date)
  family.value = {
    planName: sub.plan_name,
    subLine: familySubLine(sub.plan_name, Math.max(0, mgmt.limit - mgmt.used)),
    memberLimit: mgmt.limit,
    memberUsed: mgmt.used,
    remaining: Math.max(0, mgmt.limit - mgmt.used),
    trafficQuotaBytes: traffic?.quota_bytes ?? null,
    streams: typeof matchedPlan?.entitlements?.concurrent_streams === 'number'
      ? matchedPlan.entitlements.concurrent_streams
      : null,
    expiresAt: sub.end_date,
    expiresDate: expiry.date,
    expiresTime: expiry.time,
    downloadAllowed: plansRes.status === 'fulfilled'
      ? (plansRes.value.download_allowed ?? null)
      : null,
    resetDate: traffic?.reset_date ?? null,
    periodStart: traffic?.period_start ?? null,
    periodEnd: traffic?.period_end ?? null,
    items: mgmt.items ?? [],
  }
}

/** 家庭成员行 → 团队成员表行：有效期＝主账号订阅到期（全家共享）、
 * 流量＝团队总流量按子账户上限均分（与团队 member_payload 同款
 * 「配额自动计算」展示口径，实际用量走主账号共享池）；每人用量
 * 后端不分账，traffic_used_bytes 恒为 null，页面显示 '—'，不编数。 */
function familyMemberRows(): TeamMemberRow[] {
  const f = family.value
  if (!f) return []
  const quota = f.trafficQuotaBytes != null && f.memberLimit > 0
    ? Math.floor(f.trafficQuotaBytes / f.memberLimit)
    : 0
  return f.items.map((it): TeamMemberRow => ({
    id: it.id,
    user_id: it.id,
    username: it.username,
    public_id: null,
    role: 'member',
    is_active: it.is_active,
    joined_at: it.created_at,
    expires_at: f.expiresAt,
    traffic_quota_bytes: quota,
    traffic_used_bytes: null,
    traffic_used_pct: null,
    period_start: f.periodStart ?? '',
    period_end: f.periodEnd ?? '',
    reset_date: f.resetDate ?? '',
  }))
}

/** 家庭成员列表：客户端过滤＋分页（子账号管理口一次性下发全量，
 * limit 上限 20，无需服务端分页）。 */
function applyFamilyMembers() {
  const f = family.value
  if (!f) {
    members.value = []
    membersTotal.value = 0
    return
  }
  const q = qApplied.value.trim().toLowerCase()
  let list = familyMemberRows()
  if (q) {
    list = list.filter((row) => (row.username ?? '').toLowerCase().includes(q))
  }
  if (statusFilter.value === 'active') list = list.filter((row) => row.is_active)
  else if (statusFilter.value === 'disabled') list = list.filter((row) => !row.is_active)
  membersTotal.value = list.length
  // 删到整页空时回退到最后一页有数据的页（家庭成员增删后刷新会走到这里）
  const lastPage = Math.max(1, Math.ceil(list.length / pageSize))
  if (page.value > lastPage) page.value = lastPage
  const start = (page.value - 1) * pageSize
  members.value = list.slice(start, start + pageSize)
}

/** 家庭数据重载（成员增删后）：只刷家庭读口，不动团队详情与整页加载态 */
async function refreshFamily() {
  membersLoading.value = true
  try {
    await loadFamily()
    applyFamilyMembers()
  } finally {
    membersLoading.value = false
  }
}

const members = ref<TeamMemberRow[]>([])
const membersTotal = ref(0)
const membersLoading = ref(false)
const page = ref(1)
const pageSize = 20
const qDraft = ref('')
const qApplied = ref('')
const searchField = ref('name')
const statusFilter = ref('all')

async function loadDetail() {
  loading.value = true
  loadError.value = ''
  try {
    detail.value = await teamsApi.detail()
    page.value = 1
    if (detail.value.team) {
      await loadMembers()
    } else if (!isSubAccount.value) {
      // 无团队时补读家庭版已购态（读不到即保持未开通态，见 loadFamily）
      await loadFamily()
      if (family.value) applyFamilyMembers()
    }
  } catch (err) {
    loadError.value = teamErrorMessage(err, '团队信息加载失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

async function loadMembers() {
  if (familyMode.value) {
    applyFamilyMembers()
    return
  }
  if (!team.value) return
  membersLoading.value = true
  try {
    const res = await teamsApi.members({
      q: qApplied.value || undefined,
      status: statusFilter.value,
      page: page.value,
      page_size: pageSize,
    })
    members.value = res.items
    membersTotal.value = res.total
  } catch (err) {
    toast.error(teamErrorMessage(err, '成员列表加载失败'))
  } finally {
    membersLoading.value = false
  }
}

function applySearch() {
  qApplied.value = qDraft.value.trim()
  page.value = 1
  if (familyMode.value) applyFamilyMembers()
  else void loadMembers()
}

function goPage(next: number) {
  page.value = next
  if (familyMode.value) applyFamilyMembers()
  else void loadMembers()
}

/* ---------------- 创建 / 升级（两步弹窗） ---------------- */

const buyOpen = ref(false)
const buyStep = ref<1 | 2 | 'loading'>(1)

/** 购买 sheet 关闭（× / 遮罩 / Esc 三路同口径，与 UhdModal 一致） */
function closeBuy() {
  buyOpen.value = false
}
function onBuyKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') closeBuy()
}
watch(buyOpen, (open) => {
  if (open) document.addEventListener('keydown', onBuyKeydown)
  else document.removeEventListener('keydown', onBuyKeydown)
})
onBeforeUnmount(() => document.removeEventListener('keydown', onBuyKeydown))
const buyError = ref('')
const buying = ref(false)
const form = ref<{ line: TeamLine; traffic_gb: number; months: number; streams: number }>({
  line: 'global',
  traffic_gb: 10000,
  months: 1,
  streams: 0,
})
const preview = ref<TeamPreview | null>(null)
const teamName = ref('')
const purchaseKey = ref('')

const lineMin = computed(() =>
  form.value.line === 'global'
    ? config.value.team_global_min_traffic_gb
    : config.value.team_premium_min_traffic_gb,
)

function clampTraffic() {
  const max = config.value.team_max_traffic_gb
  let v = Math.floor(Number(form.value.traffic_gb) || 0)
  if (v > max) v = max
  if (v < lineMin.value) v = lineMin.value
  form.value.traffic_gb = v
}

function setLine(line: TeamLine) {
  if (line === 'premium' && premiumPaused.value) return
  form.value.line = line
  clampTraffic()
}

function openBuy() {
  const t = team.value
  form.value = {
    // Premium 暂停时即使存量团队预填 premium 也回落 global（卡不可选）
    line: !premiumPaused.value && t?.plan_line === 'premium' ? 'premium' : 'global',
    traffic_gb: t?.plan_traffic_gb ?? config.value.team_global_min_traffic_gb,
    months: 1,
    // 家庭版升级场景：套餐声明了同时播放路数时预填现值（友站升级
    // 弹窗同款），未声明回落 0
    streams: t?.plan_streams ?? family.value?.streams ?? 0,
  }
  clampTraffic()
  preview.value = null
  buyError.value = ''
  buyStep.value = 1
  buyOpen.value = true
}

async function doPreview() {
  buyError.value = ''
  const traffic = Math.floor(Number(form.value.traffic_gb) || 0)
  if (traffic < lineMin.value) {
    buyError.value = `起订流量最低为 ${lineMin.value.toLocaleString()} GB`
    return
  }
  if (traffic > config.value.team_max_traffic_gb) {
    buyError.value = `流量上限为 ${config.value.team_max_traffic_gb.toLocaleString()} GB`
    return
  }
  form.value.traffic_gb = traffic
  form.value.months = Math.min(12, Math.max(1, Math.floor(Number(form.value.months) || 1)))
  form.value.streams = Math.max(0, Math.floor(Number(form.value.streams) || 0))
  buyStep.value = 'loading'
  try {
    preview.value = await teamsApi.preview({
      line: form.value.line,
      traffic_gb: form.value.traffic_gb,
      months: form.value.months,
      streams: form.value.streams,
    })
    teamName.value = team.value?.name ?? ''
    purchaseKey.value = newWalletKey()
    buyStep.value = 2
  } catch (err) {
    buyStep.value = 1
    buyError.value = teamErrorMessage(err, '结算预览失败，请稍后重试')
  }
}

async function doPurchase() {
  if (!preview.value || buying.value) return
  buying.value = true
  buyError.value = ''
  try {
    await teamsApi.purchase({
      line: form.value.line,
      traffic_gb: form.value.traffic_gb,
      months: form.value.months,
      streams: form.value.streams,
      name: teamName.value.trim() || undefined,
      idempotency_key: purchaseKey.value,
    })
    buyOpen.value = false
    toast.success('团队套餐购买成功')
    await loadDetail()
  } catch (err) {
    buyError.value = teamErrorMessage(err, '支付失败，请稍后重试')
  } finally {
    buying.value = false
  }
}

/* ---------------- 添加 / 移除成员、改名 ---------------- */

const memberOpen = ref(false)
const memberBusy = ref(false)
const memberError = ref('')
const memberForm = ref({ username: '', password: '' })

function openMember() {
  if (familyMode.value) {
    if (familyAtLimit.value) {
      toast.error(`成员已达上限（${family.value?.memberLimit} 个子账号）`)
      return
    }
  } else if (atLimit.value) {
    toast.error(`成员已达上限（${team.value?.plan_member_limit} 个子账号）`)
    return
  }
  memberForm.value = { username: '', password: '' }
  memberError.value = ''
  memberOpen.value = true
}

async function doCreateMember() {
  if (memberBusy.value) return
  memberBusy.value = true
  memberError.value = ''
  try {
    if (familyMode.value) {
      // 家庭成员走家庭子账号口（与团队成员口同为用户名+密码创建，
      // 但名额与归属算家庭侧，不占团队 plan_member_limit）
      await subAccountsApi.create({
        username: memberForm.value.username.trim(),
        password: memberForm.value.password,
      })
    } else {
      await teamsApi.createMember({
        username: memberForm.value.username.trim(),
        password: memberForm.value.password,
      })
    }
    memberOpen.value = false
    toast.success('成员已创建')
    page.value = 1
    if (familyMode.value) await refreshFamily()
    else await loadDetail()
  } catch (err) {
    memberError.value = teamErrorMessage(err, '创建成员失败，请稍后重试')
  } finally {
    memberBusy.value = false
  }
}

const removeOpen = ref(false)
const removeBusy = ref(false)
const removeError = ref('')
const removeTarget = ref<TeamMemberRow | null>(null)

function askRemove(row: TeamMemberRow) {
  removeTarget.value = row
  removeError.value = ''
  removeOpen.value = true
}

async function doRemoveMember() {
  if (!removeTarget.value || removeBusy.value) return
  removeBusy.value = true
  removeError.value = ''
  try {
    if (familyMode.value) {
      // 家庭成员移除＝家庭子账号软禁用（与团队成员移除同为停用账号）
      await subAccountsApi.remove(removeTarget.value.user_id)
    } else {
      await teamsApi.removeMember(removeTarget.value.user_id)
    }
    removeOpen.value = false
    toast.success('成员已移除')
    if (familyMode.value) await refreshFamily()
    else await loadDetail()
  } catch (err) {
    removeError.value = teamErrorMessage(err, '移除成员失败，请稍后重试')
  } finally {
    removeBusy.value = false
  }
}

const renameOpen = ref(false)
const renameBusy = ref(false)
const renameError = ref('')
const renameValue = ref('')

function openRename() {
  renameValue.value = team.value?.name ?? ''
  renameError.value = ''
  renameOpen.value = true
}

async function doRename() {
  if (renameBusy.value) return
  renameBusy.value = true
  renameError.value = ''
  try {
    const res = await teamsApi.rename(renameValue.value.trim())
    if (detail.value) detail.value.team = res.team
    renameOpen.value = false
    toast.success('团队名已更新')
  } catch (err) {
    renameError.value = teamErrorMessage(err, '改名失败，请稍后重试')
  } finally {
    renameBusy.value = false
  }
}

onMounted(() => {
  void loadDetail()
})
</script>

<style scoped>
/* 团队页版式：主题变量在 team-view.css（令牌契约要求独立样式表）；
   字阶/圆角/动效走 --uhd-* 令牌。 */
.tm-inner {
  max-width: 760px;
  margin: 0 auto;
  padding: 14px 20px 120px;
  box-sizing: border-box;
}
/* 桌面并壳时内容区放宽到办事面宽度（闸门 ≥769px 才进壳） */
@media (min-width: 769px) {
  .tm-inner { max-width: 1000px; }
}
.tm-head { margin: 10px 0 18px; }
.tm-title {
  margin: 0;
  font-size: var(--uhd-fs-h1-app);
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
  line-height: var(--uhd-lh-tight);
}
.tm-err {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--tm-line);
  border-radius: 16px;
  color: var(--tm-text-2);
  font-size: var(--uhd-fs-body);
}
.tm-retry {
  border: 1px solid var(--tm-line);
  background: transparent;
  color: inherit;
  border-radius: var(--uhd-radius-pill);
  padding: 6px 14px;
  cursor: pointer;
  font-size: var(--uhd-fs-caption);
}
.tm-loading {
  min-height: 40dvh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
}
.tm-loading__text { margin: 0; font-size: var(--uhd-fs-caption); color: var(--tm-text-3); }
.tm-spin {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  border: 3px solid var(--tm-line);
  border-top-color: var(--tm-text-2);
  animation: tm-spin 0.8s linear infinite;
}
.tm-spin--sm { width: 16px; height: 16px; border-width: 2px; }
@keyframes tm-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .tm-spin { animation-duration: 1.6s; }
}

/* ---------- 卡片与介绍卡 ---------- */
.tm-card {
  background: var(--tm-card);
  border: 1px solid var(--tm-line);
  border-radius: 20px;
}
.tm-intro { padding: 22px; }
.tm-intro__head {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}
.tm-intro__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  flex: none;
  border-radius: 14px;
  background: var(--tm-chip);
}
.tm-intro__title {
  margin: 2px 0 6px;
  font-size: var(--uhd-fs-title);
  font-weight: var(--uhd-fw-bold);
}
.tm-intro__desc {
  margin: 0;
  font-size: var(--uhd-fs-caption);
  color: var(--tm-text-2);
  line-height: var(--uhd-lh-body);
}
.tm-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 20px;
}
.tm-stat {
  padding: 14px;
  border-radius: 14px;
  background: var(--tm-chip);
}
.tm-stat__label {
  margin: 0 0 6px;
  font-size: var(--uhd-fs-aux);
  color: var(--tm-text-2);
}
.tm-stat__value {
  margin: 0;
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-bold);
}
.tm-stat__value--off {
  color: var(--tm-text-2);
}
.tm-pending {
  margin: 16px 0 0;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--tm-warn-bg);
  color: var(--tm-warn-text);
  font-size: var(--uhd-fs-caption);
  line-height: var(--uhd-lh-body);
}
.tm-active-line {
  margin: 16px 0 0;
  font-size: var(--uhd-fs-caption);
  color: var(--tm-text-2);
}
.tm-review {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 20px;
  padding-top: 18px;
  border-top: 1px solid var(--tm-line);
}
.tm-review__text { flex: 1; min-width: 0; }
.tm-review__title {
  margin: 0 0 4px;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-semibold);
}
.tm-review__desc {
  margin: 0;
  font-size: var(--uhd-fs-aux);
  color: var(--tm-text-2);
  line-height: var(--uhd-lh-body);
}
.tm-cta-note {
  margin: 0;
  font-size: var(--uhd-fs-caption);
  color: var(--tm-text-2);
  white-space: nowrap;
}
@media (max-width: 640px) {
  .tm-stats { grid-template-columns: minmax(0, 1fr); }
  .tm-review { flex-direction: column; align-items: stretch; }
  .tm-cta-note { white-space: normal; }
}

/* ---------- 家庭版已购态（照友站家庭版页 f1：无卡片底，直接铺在页面底色上） ---------- */
.tm-family { margin-top: 2px; }
.tm-family__title {
  margin: 0;
  font-size: var(--uhd-fs-h1-app);
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
  line-height: var(--uhd-lh-tight);
}
.tm-family__sub {
  margin: 8px 0 0;
  font-size: var(--uhd-fs-caption);
  color: var(--tm-text-2);
}
.tm-family__actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
}
.tm-family__refresh {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border: 1px solid var(--tm-line);
  border-radius: 50%;
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.tm-family__stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-top: 20px;
  border-top: 1px solid var(--tm-line);
}
.tm-fam-stat { padding: 14px 0; }
.tm-fam-stat:nth-child(n+3) { border-top: 1px solid var(--tm-line); }
.tm-fam-stat__label {
  margin: 0 0 6px;
  font-size: var(--uhd-fs-aux);
  color: var(--tm-text-2);
}
.tm-fam-stat__value {
  margin: 0;
  font-size: var(--uhd-fs-h3);
  font-weight: var(--uhd-fw-bold);
}
.tm-fam-stat__unit {
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  color: var(--tm-text-2);
}
.tm-family__dl {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-top: 6px;
  padding: 16px 0 4px;
  border-top: 1px solid var(--tm-line);
}
.tm-family__dl-title {
  margin: 0;
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
}
.tm-family__dl-desc {
  margin: 4px 0 0;
  font-size: var(--uhd-fs-aux);
  color: var(--tm-text-2);
}
.tm-family__dl-value {
  flex: none;
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-success);
  white-space: nowrap;
}
.tm-family__dl-value--off { color: var(--tm-text-2); }
.tm-members__count {
  margin-left: 6px;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  color: var(--tm-text-2);
}
/* 家庭空态卡里的白钮（友站同款）：浅色/深色两态均为实色白底＋深字 */
.tm-fam-empty-btn {
  height: 40px;
  padding: 0 20px;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: var(--uhd-radius-pill);
  background: #ffffff;
  color: #17181c;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-semibold);
  font-family: var(--uhd-font-sans);
  cursor: pointer;
}

/* ---------- 团队信息条 ---------- */
.tm-teambar {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 16px;
  padding: 16px 22px;
}
.tm-teambar__main { flex: 1; min-width: 0; }
.tm-teambar__name {
  margin: 0;
  font-size: var(--uhd-fs-lead);
  font-weight: var(--uhd-fw-bold);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tm-teambar__sub {
  margin: 4px 0 0;
  font-size: var(--uhd-fs-aux);
  color: var(--tm-text-2);
}
.tm-chip {
  flex: none;
  padding: 4px 10px;
  border-radius: var(--uhd-radius-pill);
  background: var(--tm-warn-bg);
  color: var(--tm-warn-text);
  font-size: var(--uhd-fs-aux-sm);
  font-weight: var(--uhd-fw-semibold);
}
.tm-chip--ok {
  background: rgba(22, 163, 74, 0.14);
  color: var(--uhd-success);
}
.tm-chip--off {
  margin-left: 8px;
  background: var(--tm-chip);
  color: var(--tm-text-2);
}
.tm-mini-btn {
  flex: none;
  border: 1px solid var(--tm-line);
  background: transparent;
  color: inherit;
  border-radius: var(--uhd-radius-pill);
  padding: 7px 14px;
  font-size: var(--uhd-fs-caption);
  cursor: pointer;
}
.tm-mini-btn:disabled { opacity: 0.45; cursor: not-allowed; }

/* ---------- 成员区 ---------- */
.tm-members { margin-top: 28px; }
.tm-members__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.tm-sec-title {
  margin: 0;
  font-size: var(--uhd-fs-h3);
  font-weight: var(--uhd-fw-extrabold);
  letter-spacing: -0.01em;
}
.tm-add-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--tm-line);
  background: var(--tm-card);
  color: inherit;
  border-radius: var(--uhd-radius-pill);
  height: 38px;
  padding: 0 16px;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
}
.tm-add-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.tm-limit-note {
  margin: 0 0 12px;
  font-size: var(--uhd-fs-caption);
  color: var(--tm-text-2);
}
.tm-toolbar {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.tm-tool { display: flex; }
.tm-tool--grow { flex: 1; min-width: 160px; }
.tm-select,
.tm-input {
  height: 40px;
  padding: 0 14px;
  border: 1px solid var(--tm-line);
  border-radius: var(--uhd-radius-pill);
  background: var(--tm-input);
  color: inherit;
  font-size: var(--uhd-fs-caption);
  font-family: var(--uhd-font-sans);
  box-sizing: border-box;
}
.tm-tool .tm-input { width: 100%; border-radius: var(--uhd-radius-pill); }
.tm-select:disabled,
.tm-input:disabled { opacity: 0.5; }
.tm-search-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 40px;
  padding: 0 18px;
  border: none;
  border-radius: var(--uhd-radius-pill);
  background: var(--tm-text-2);
  color: var(--tm-card);
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-semibold);
  cursor: pointer;
}
.tm-search-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.tm-table-card { padding: 6px 22px 14px; }
.tm-table-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 48px 0;
  font-size: var(--uhd-fs-caption);
  color: var(--tm-text-2);
}
.tm-table-wrap { overflow-x: auto; }
.tm-table {
  width: 100%;
  min-width: 640px;
  border-collapse: collapse;
}
.tm-table th {
  padding: 12px 16px 10px 2px;
  text-align: left;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-medium);
  color: var(--tm-text-2);
  border-bottom: 1px solid var(--tm-line);
  white-space: nowrap;
}
.tm-table td {
  padding: 12px 16px 12px 2px;
  font-size: var(--uhd-fs-body);
  border-bottom: 1px solid var(--tm-line);
  vertical-align: middle;
}
.tm-table tbody tr:last-child td { border-bottom: none; }
.tm-member-name { font-weight: var(--uhd-fw-semibold); }
.tm-member-id {
  display: block;
  font-size: var(--uhd-fs-aux-sm);
  color: var(--tm-text-3);
}
.tm-usage-text { display: block; white-space: nowrap; }
.tm-usage-bar {
  display: block;
  width: 110px;
  height: 5px;
  margin-top: 6px;
  border-radius: 3px;
  background: var(--tm-chip);
  overflow: hidden;
}
.tm-usage-bar__fill {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: var(--uhd-l-link);
}
.tm-remove-btn {
  border: none;
  background: transparent;
  color: var(--tm-danger);
  font-size: var(--uhd-fs-caption);
  cursor: pointer;
  padding: 4px 6px;
}
.tm-pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding-top: 12px;
}
.tm-pager__info { font-size: var(--uhd-fs-caption); color: var(--tm-text-2); }

/* ---------- 弹窗内通用 ---------- */
.tm-field-label {
  margin: 18px 0 8px;
  font-size: var(--uhd-fs-caption);
  font-weight: var(--uhd-fw-semibold);
  color: var(--uhd-l-text);
}
.tm-field-label:first-child { margin-top: 0; }
.tm-buy .tm-input,
.tm-settle .tm-input,
.tm-member-form .tm-input,
.tm-rename .tm-input {
  width: 100%;
  border-radius: 12px;
  /* 弹窗经 Teleport 出 .tm-page 作用域，--tm-* 不可达：边框/底色用
     中性字面值＋弹窗作用域翻转的 --uhd-l-input-bg（与订阅确认弹窗同法） */
  border-color: rgba(128, 128, 140, 0.35);
  background: var(--uhd-l-input-bg);
  color: var(--uhd-l-text);
}
.tm-btn {
  height: 44px;
  padding: 0 20px;
  border-radius: var(--uhd-radius-pill);
  font-size: var(--uhd-fs-body);
  font-weight: var(--uhd-fw-semibold);
  font-family: var(--uhd-font-sans);
  cursor: pointer;
}
.tm-btn--ghost {
  border: 1px solid rgba(128, 128, 140, 0.35);
  background: transparent;
  color: var(--uhd-l-text);
}
.tm-btn--danger {
  border: none;
  background: var(--uhd-danger);
  color: #fff;
}
.tm-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.tm-buy-error {
  margin: 0 0 14px;
  padding: 11px 14px;
  border-radius: 12px;
  background: rgba(220, 38, 38, 0.12);
  /* 只在弹窗内用（Teleport 出页面作用域）：深色玻璃上用亮红，浅色翻深红 */
  color: #f87171;
  font-size: var(--uhd-fs-caption);
  line-height: var(--uhd-lh-body);
}
html[data-theme='light'] .tm-buy-error { color: var(--uhd-danger); }
.tm-settle .tm-buy-error,
.tm-member-form .tm-buy-error { margin-top: 14px; }
.tm-confirm-text {
  margin: 0;
  font-size: var(--uhd-fs-body);
  line-height: var(--uhd-lh-body);
}

/* ---------- 购买 sheet（专职壳：封顶 82dvh＋内部滚动＋底部栏常驻） ---------- */
.tm-sheet__overlay {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 14px;
  box-sizing: border-box;
  background: var(--modal-scrim-dark);
  animation: uhd-fade-in var(--uhd-dur-fade) var(--uhd-ease-standard);
  font-family: var(--uhd-font-sans);
}
.tm-sheet {
  /* 面板作用域文字令牌随主题翻转（默认深色实面＝浅字）；面板本体实色不透底（2026-11-18 用户令：弹窗不用玻璃） */
  --uhd-l-text: var(--modal-glass-fg);
  --uhd-l-text-2: var(--modal-glass-dim);
  --uhd-l-input-bg: rgba(255, 255, 255, 0.08);
  display: flex;
  flex-direction: column;
  width: min(720px, 100%);
  max-height: 82dvh;
  background: var(--uhd-d-surface);
  border: 1px solid var(--uhd-d-border);
  border-radius: 24px;
  box-shadow: var(--uhd-shadow-card-d);
  animation: uhd-blur-in var(--uhd-dur-blur-in) var(--uhd-ease-pop);
  color: var(--uhd-l-text-2);
  font-size: var(--uhd-fs-body);
}
html[data-theme='light'] .tm-sheet {
  --uhd-l-text: #111111;
  --uhd-l-text-2: #6b7280;
  --uhd-l-input-bg: #f5f5f5;
  background: var(--uhd-l-surface);
  border-color: var(--uhd-l-border);
  box-shadow: var(--uhd-shadow-card-l);
}
.tm-sheet__head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 18px 20px 4px;
}
.tm-sheet__zap {
  display: inline-flex;
  flex: none;
  margin-top: 1px;
  color: #f59e0b;
}
.tm-sheet__titles { flex: 1; min-width: 0; }
.tm-sheet__title {
  margin: 0;
  font-size: 16px;
  font-weight: var(--uhd-fw-extrabold);
  line-height: 1.35;
  color: var(--uhd-l-text);
}
.tm-sheet__sub {
  margin: 3px 0 0;
  font-size: var(--uhd-fs-caption);
  color: var(--uhd-l-text-2);
}
.tm-sheet__nav-btn {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  margin-top: -3px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--uhd-l-text);
  cursor: pointer;
  padding: 0;
}
.tm-sheet__nav-btn:hover { background: var(--uhd-l-input-bg); }
.tm-sheet__close { font-size: 20px; line-height: 1; color: var(--uhd-l-text-2); }
.tm-sheet__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px 20px 14px;
}
.tm-sheet__foot {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 10px 20px 18px;
}

/* ---------- 购买第 1 步：紧凑卡版式（友站录屏实测） ---------- */
.tm-buy-loading {
  min-height: 220px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  color: var(--uhd-l-text-2);
  font-size: var(--uhd-fs-caption);
}
.tm-buy-loading p { margin: 0; }
.tm-buy {
  display: flex;
  flex-direction: column;
  gap: 11px;
}
.tm-buy .tm-buy-error { margin: 0; }
.tm-buy__label {
  margin: 2px 0 -3px;
  font-size: 13px;
  color: var(--uhd-l-text-2);
}
.tm-plan-cards { display: flex; gap: 10px; }
.tm-plan-card {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  padding: 10px 14px;
  border-radius: 14px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(255, 255, 255, 0.05);
  color: var(--uhd-l-text-2);
  font-family: var(--uhd-font-sans);
  text-align: left;
  cursor: pointer;
}
.tm-plan-card__name {
  font-size: 15px;
  font-weight: var(--uhd-fw-bold);
  line-height: 1.3;
}
.tm-plan-card__sub { font-size: 12px; line-height: 1.3; }
.tm-plan-card--off {
  opacity: 0.45;
  cursor: not-allowed;
}
.tm-plan-card--on {
  background: rgba(255, 255, 255, 0.16);
  border-color: rgba(255, 255, 255, 0.9);
  color: var(--uhd-l-text);
}
html[data-theme='light'] .tm-plan-card {
  background: #ffffff;
  border-color: rgba(17, 17, 17, 0.1);
}
html[data-theme='light'] .tm-plan-card--on {
  background: #ececf0;
  border-color: #16161a;
}
.tm-buy-card {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 18px;
  padding: 14px 16px;
}
html[data-theme='light'] .tm-buy-card {
  background: #f4f4f6;
  border-color: rgba(17, 17, 17, 0.04);
}
.tm-buy-card__row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.tm-buy-card__main { flex: 1; min-width: 0; }
.tm-buy-card__label {
  margin: 0;
  font-size: 14px;
  font-weight: var(--uhd-fw-semibold);
  line-height: 1.4;
  color: var(--uhd-l-text);
}
.tm-buy-card__desc {
  margin: 5px 0 0;
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--uhd-l-text-2);
}
.tm-buy-card__conv {
  margin: 5px 0 0;
  font-size: 13px;
  line-height: 1.4;
  color: var(--uhd-l-text-2);
}
.tm-buy-card__value {
  margin: 0;
  font-size: 16px;
  font-weight: var(--uhd-fw-bold);
  line-height: 1.4;
  color: var(--uhd-l-text);
}
.tm-capsule {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border-radius: var(--uhd-radius-pill);
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.16);
  color: var(--uhd-l-text);
  padding: 0 14px;
  box-sizing: border-box;
}
html[data-theme='light'] .tm-capsule {
  background: #ffffff;
  border-color: rgba(17, 17, 17, 0.14);
  color: #16161a;
}
.tm-capsule--sm { width: 108px; height: 36px; margin-top: 2px; }
.tm-capsule--lg { width: 168px; height: 44px; }
.tm-capsule input {
  flex: 1;
  min-width: 0;
  width: 100%;
  border: none;
  outline: none;
  background: transparent;
  color: inherit;
  font-family: var(--uhd-font-sans);
  font-size: 16px;
  font-weight: var(--uhd-fw-bold);
  text-align: right;
  padding: 0;
  -moz-appearance: textfield;
  appearance: textfield;
}
.tm-capsule input::-webkit-outer-spin-button,
.tm-capsule input::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}
.tm-capsule__unit {
  flex: none;
  font-size: 12px;
  color: var(--uhd-l-text-2);
}
html[data-theme='light'] .tm-capsule__unit { color: #6b7280; }
/* 滑块：细轨＋白色圆环钮（友站实测形态，两主题同形） */
.tm-range {
  -webkit-appearance: none;
  appearance: none;
  display: block;
  width: 100%;
  height: 22px;
  margin: 8px 0 2px;
  background: transparent;
  cursor: pointer;
}
.tm-range::-webkit-slider-runnable-track {
  height: 5px;
  border-radius: 3px;
  background: rgba(128, 128, 140, 0.35);
}
.tm-range::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 20px;
  height: 20px;
  margin-top: -7.5px;
  border-radius: 50%;
  background: #ffffff;
  border: 2px solid rgba(22, 22, 26, 0.9);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.25);
}
.tm-range::-moz-range-track {
  height: 5px;
  border-radius: 3px;
  background: rgba(128, 128, 140, 0.35);
}
.tm-range::-moz-range-thumb {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #ffffff;
  border: 2px solid rgba(22, 22, 26, 0.9);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.25);
}
.tm-slider-caps {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--uhd-l-text-2);
}
.tm-mini-summary { display: flex; gap: 10px; }
.tm-mini-card {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 8px 12px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.07);
  color: var(--uhd-l-text-2);
}
html[data-theme='light'] .tm-mini-card {
  background: #f4f4f6;
  border-color: rgba(17, 17, 17, 0.04);
}
.tm-mini-card__label { font-size: 11px; line-height: 1.35; }
.tm-mini-card__value {
  font-size: 14px;
  font-weight: var(--uhd-fw-bold);
  line-height: 1.35;
  color: var(--uhd-l-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ---------- 购买第 2 步：结算预览 ---------- */
.tm-settle {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.tm-settle .tm-field-label { margin: 0 0 -4px; }
.tm-settle__card {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.tm-settle .tm-buy-error { margin: 0; }
.tm-sum-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  font-size: var(--uhd-fs-caption);
}
.tm-sum-row span { color: var(--uhd-l-text-2); }
.tm-sum-row b { color: var(--uhd-l-text); font-weight: var(--uhd-fw-semibold); text-align: right; }
.tm-sum-row--total {
  padding-top: 10px;
  border-top: 1px solid rgba(128, 128, 140, 0.35);
  font-size: var(--uhd-fs-body);
}
.tm-sum-note {
  display: block;
  font-size: var(--uhd-fs-aux-sm);
  color: var(--uhd-l-text-2);
}
.tm-member-note {
  margin: 16px 0 0;
  font-size: var(--uhd-fs-aux);
  color: var(--uhd-l-text-2);
  line-height: var(--uhd-lh-body);
}
</style>
