<template>
  <div class="iv-page">
    <header class="iv-topbar">
      <RouterLink to="/game/account/menu" class="iv-back" aria-label="返回我的">
        <ChevronLeft :size="20" aria-hidden="true" />
        <span>邀请好友</span>
      </RouterLink>
    </header>

    <main class="iv-body">
      <div v-if="loadError" class="iv-err">
        {{ loadError }}<button type="button" class="iv-retry" @click="load">重试</button>
      </div>
      <div v-else-if="loading" class="iv-skeleton" aria-hidden="true"></div>

      <template v-else>
        <p v-if="!inviteEnabled" class="iv-empty">邀请功能暂时关闭，过段时间再来看看。</p>

        <template v-else>
          <!-- 我的邀请：码＋链接，都是现行注册机制真能用的（链接带 ?invite= 预填） -->
          <section class="iv-card">
            <h2 class="iv-card__title">我的邀请码</h2>
            <div class="iv-code-row">
              <span class="iv-code">{{ myCode }}</span>
              <button type="button" class="iv-copy" @click="copyText(myCode, '邀请码已复制')">复制</button>
            </div>
            <h2 class="iv-card__title iv-card__title--sub">邀请链接</h2>
            <div class="iv-link-row">
              <span class="iv-link">{{ inviteLink }}</span>
              <button type="button" class="iv-copy" @click="copyText(inviteLink, '邀请链接已复制')">复制</button>
            </div>
            <p class="iv-stats">已邀请 {{ records.length }} 人 · 累计返利 {{ totalRebate }}</p>
          </section>

          <!-- 规则说明：比例读后端配置（invitation_rebate_percent），不写死数字；
               返利去向按已定版口径——进余额钱包；钱包未上线前只记账不发放 -->
          <section class="iv-card">
            <h2 class="iv-card__title">邀请规则</h2>
            <p class="iv-rule">
              好友通过你的邀请码或邀请链接注册成功，就是你的邀请好友。好友充值时，
              <template v-if="rebatePercent > 0">系统按其充值金额的 {{ rebatePercent }}% 记一笔返利到你的返利台账。</template>
              <template v-else>系统会把返利记入你的返利台账。</template>
              余额钱包上线后，返利会直接发放到钱包；在此之前先记账，一笔都不会丢。
            </p>
          </section>

          <!-- 邀请记录 -->
          <section class="iv-card">
            <h2 class="iv-card__title">邀请记录</h2>
            <p v-if="!records.length" class="iv-none">
              还没有好友通过你的邀请注册。把邀请链接发给朋友，对方注册成功后就会出现在这里。
            </p>
            <template v-else>
              <div class="iv-thead" aria-hidden="true">
                <span class="iv-col iv-col--name">被邀请人</span>
                <span class="iv-col">注册时间</span>
                <span class="iv-col iv-col--num">累计返利</span>
              </div>
              <ul class="iv-rows">
                <li v-for="r in records" :key="r.id" class="iv-row">
                  <span class="iv-col iv-col--name">{{ r.invitee_username }}</span>
                  <span class="iv-col">{{ formatDate(r.created_at) }}</span>
                  <span class="iv-col iv-col--num">{{ r.rebate_total }}</span>
                </li>
              </ul>
            </template>
          </section>
        </template>
      </template>
    </main>
  </div>
</template>

<script setup lang="ts">
/**
 * 邀请好友（/game/account/invite）——2026-11-13 对齐友站「我的 · 账户」新增。
 * 数据走现行邀请机制（backend/api/invitation.py）：邀请码自动生成、注册页
 * ?invite= 预填并在注册时落邀请关系；累计返利是台账口径（后端按充值订单
 * 归属从返利流水拆算），页面只展示记账数，不承诺已发放到钱包。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ChevronLeft } from 'lucide-vue-next'
import { inviteApi, type InvitationRecordRow, type MyInviteInfo } from '@/api/economy'
import { useToast } from '@/composables/useToast'
import './invite-friends.css'

const toast = useToast()

const loading = ref(true)
const loadError = ref('')
const info = ref<MyInviteInfo | null>(null)
const records = ref<InvitationRecordRow[]>([])

const inviteEnabled = computed(() => info.value?.config?.enabled !== false)
const myCode = computed(() => info.value?.code || '')
const rebatePercent = computed(() => info.value?.config?.rebate_percent ?? 0)
const inviteLink = computed(() =>
  myCode.value ? `${window.location.origin}/login?invite=${myCode.value}` : '',
)
const totalRebate = computed(() =>
  records.value.reduce((sum, r) => sum + (r.rebate_total || 0), 0),
)

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

async function copyText(text: string, okMsg: string) {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    toast.success(okMsg)
  } catch {
    toast.error('复制失败，请手动复制')
  }
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const [codeRes, recRes] = await Promise.all([
      inviteApi.myCode(),
      inviteApi.records({ limit: 100 }),
    ])
    info.value = codeRes
    records.value = recRes.records || []
  } catch {
    loadError.value = '邀请信息加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
