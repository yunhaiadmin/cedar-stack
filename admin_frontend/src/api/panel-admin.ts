/**
 * 面板 v4 · 馆灵阶段管理（r48 遗留 ⑨）
 *
 * 后端：`/api/admin/panel/pet-stages`（system_configs 键 panel_pet_stages）。
 * 管理 axios 实例 baseURL 是 `/api/admin`。
 * （成就定义管理已于 2026-11-14 随成就系统删除一并摘除。）
 */
import { get, put } from '@/utils/request'

export interface PetStage {
  name: string
  icon: string
  hours: number
}

export const fetchPetStages = async (): Promise<PetStage[]> =>
  (await get<{ stages: PetStage[] }>('/panel/pet-stages')).stages

export const savePetStages = async (stages: PetStage[]): Promise<PetStage[]> =>
  (await put<{ stages: PetStage[] }>('/panel/pet-stages', { stages })).stages
