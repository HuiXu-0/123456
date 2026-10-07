/**
 * 掌握度与学习旅程数据获取层（接口文档第 7 章，接口 12/13/14/15）。
 * 后端为掌握度 / 旅程的权威数据源，前端 store 退化为缓存。
 *
 * 注意：本文件是 services 数据层；前端状态仍在 store/mastery.ts、store/journey.ts。
 */
import { apiGet, apiPost } from './api'

export type KPStatus = 'learning' | 'pending-check' | 'passed'

/** 7.1 掌握度全集。未出现的知识点视为未开始。 */
export function getMastery(): Promise<{ status: Record<string, KPStatus> }> {
  return apiGet<{ status: Record<string, KPStatus> }>('/mastery')
}

/** 7.2 去检验：learning → pending-check（passed 保持不变）。 */
export function checkKp(kpId: string): Promise<{ id: string; status: KPStatus }> {
  return apiPost<{ id: string; status: KPStatus }>(`/mastery/${kpId}/check`)
}

/** 7.3 标记通过（幂等）。 */
export function passKp(kpId: string): Promise<{ id: string; status: KPStatus }> {
  return apiPost<{ id: string; status: KPStatus }>(`/mastery/${kpId}/pass`)
}

export interface JourneyData {
  hasDiagnosed: boolean
  hasGeneratedPath: boolean
  targetJobName: string | null
  matchPct: number | null
  currentStep: 'diagnose' | 'generate-path' | 'learn' | 'review'
}

/** 7.4 旅程状态，currentStep 由后端推导。 */
export function getJourney(): Promise<JourneyData> {
  return apiGet<JourneyData>('/journey')
}
