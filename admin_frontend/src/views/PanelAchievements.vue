<script setup lang="ts">
/**
 * 馆灵阶段：面板 v4 玩法配置（r48 遗留 ⑨ 补的管理入口）
 *
 * - 馆灵阶段：第一阶段 hours 必须为 0，且阈值不能倒挂（后端同口径校验）。
 * （成就定义管理已于 2026-11-14 随成就系统删除一并摘除，本页只剩馆灵阶段。）
 */
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { toAdminError } from '@/utils/errorMessage'
import { Medal, Plus, RefreshCw, Trash2 } from 'lucide-vue-next'
import { PageHeader, SectionCard } from '@/components/ui'
import {
  fetchPetStages, savePetStages,
  type PetStage,
} from '@/api/panel-admin'

const loading = ref(false)
const loadError = ref('')

// ===== 馆灵阶段 =====
const stages = ref<PetStage[]>([])
const stagesLoaded = ref(false)
const savingStages = ref(false)

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const sts = await fetchPetStages()
    stages.value = sts.map((s) => ({ ...s }))
    stagesLoaded.value = true
  } catch (e) {
    loadError.value = toAdminError(e, '加载失败')
  } finally {
    loading.value = false
  }
}

function addStage() {
  const last = stages.value[stages.value.length - 1]
  stages.value.push({ name: '', icon: '', hours: last ? last.hours + 100 : 0 })
}

function removeStage(i: number) {
  stages.value.splice(i, 1)
}

async function saveStages() {
  if (!stagesLoaded.value) return
  // 前端先拦一遍 obvious 的错：后端同口径再拦一次
  if (!stages.value.length) {
    ElMessage.error('至少保留一个阶段')
    return
  }
  for (let i = 0; i < stages.value.length; i++) {
    const s = stages.value[i]
    if (!s.name.trim()) {
      ElMessage.error(`第 ${i + 1} 个阶段名称不能为空`)
      return
    }
    if (i === 0 && s.hours !== 0) {
      ElMessage.error('第一阶段 hours 必须为 0（从 0 小时开始）')
      return
    }
    if (i > 0 && s.hours < stages.value[i - 1].hours) {
      ElMessage.error(`第 ${i + 1} 个阶段 hours 不能小于前一阶段`)
      return
    }
  }
  try {
    await ElMessageBox.confirm('保存后立即对全站生效，确认吗？', '保存馆灵阶段', { type: 'warning' })
  } catch {
    return
  }
  savingStages.value = true
  try {
    stages.value = await savePetStages(stages.value.map((s) => ({ ...s, name: s.name.trim() })))
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error(toAdminError(e, '保存失败'))
  } finally {
    savingStages.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader
      title="馆灵阶段"
      description="面板 v4 玩法配置：馆灵阶段改累计观影小时阈值。"
    >
      <template #actions>
        <el-button :icon="RefreshCw" :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <SectionCard title="馆灵阶段" :icon="Medal" flush>
      <template #actions>
        <el-button :icon="Plus" @click="addStage">新增阶段</el-button>
        <el-button type="primary" :loading="savingStages" :disabled="!stagesLoaded" @click="saveStages">
          保存
        </el-button>
      </template>
      <p v-if="loadError" class="field-help">{{ loadError }}</p>
      <div v-for="(s, i) in stages" :key="i" class="stage-row">
        <span class="mono stage-idx">{{ i + 1 }}</span>
        <el-input v-model="s.icon" placeholder="图标" class="stage-icon" maxlength="20" />
        <el-input v-model="s.name" placeholder="阶段名称" class="stage-name" maxlength="30" />
        <el-input-number
          v-model="s.hours"
          :min="0"
          :disabled="i === 0"
          placeholder="累计小时"
          class="stage-hours"
        />
        <span class="field-help">小时</span>
        <el-button
          size="small" type="danger" :icon="Trash2" circle
          :disabled="stages.length <= 1 || i === 0"
          @click="removeStage(i)"
          aria-label="删除该阶段"
        />
      </div>
      <p class="field-help">
        第一阶段 hours 固定为 0（从 0 小时开始，不可改）；后续阶段阈值不能倒挂。保存后立即对全站生效。
      </p>
    </SectionCard>
  </div>
</template>

<style scoped>
.stage-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.stage-idx { width: 24px; color: var(--el-text-color-secondary); }
.stage-icon { width: 90px; }
.stage-name { flex: 1; min-width: 120px; }
.stage-hours { width: 150px; }
</style>
