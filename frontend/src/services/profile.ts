/**
 * 画像诊断数据获取层（接口文档第 4 章，接口 4/5/6/7）。
 * 响应结构与 ProfileBuilder 内部 ParsedProfile / profileNarrative 的 Narrative 严格对齐。
 */
import { apiGet, apiPost, apiPostForm } from './api'
import type { Narrative, SourceRef } from './profileNarrative'

/** 4.1 ParsedProfile（source 为 resume|ocr|text|manual） */
export interface ParsedField {
  value: string
  source: string
}
export interface ParsedSkill {
  name: string
  level: number
  source: string
}
export interface ParsedMaterial {
  id: string
  label: string
  kind: 'doc' | 'image' | 'text'
}
export interface ParsedProfileApi {
  education: ParsedField
  major: ParsedField
  goal: ParsedField
  skills: ParsedSkill[]
  materials: ParsedMaterial[]
}

/** 4.1 多模态材料解析（multipart）。files / description 均可空。 */
export function parseProfile(files: File[], description: string): Promise<ParsedProfileApi> {
  const form = new FormData()
  files.forEach((f) => form.append('files', f))
  if (description.trim()) form.append('description', description)
  return apiPostForm<ParsedProfileApi>('/profile/parse', form)
}

interface NarrativeDraft {
  education: { value: string; source: string }
  major: { value: string; source: string }
  goal: { value: string; source: string }
  skills: { name: string; level: number; source: string }[]
}

/** 4.2 画像叙述生成。无材料后端返回 null（与前端 generateNarrative 一致）。 */
export function generateNarrativeApi(
  draft: NarrativeDraft,
  materials: SourceRef[],
  targetJob: { name: string; radar: Record<string, number> } | null
): Promise<Narrative | null> {
  return apiPost<Narrative | null>('/profile/narrative', { draft, materials, targetJob })
}

/** 4.3 完成诊断，写入旅程状态。 */
export function completeDiagnosisApi(
  targetJobName: string,
  matchPct: number
): Promise<{ hasDiagnosed: boolean }> {
  return apiPost<{ hasDiagnosed: boolean }>('/profile/diagnosis-complete', { targetJobName, matchPct })
}

/** 4.4 能力雷达 6 维。 */
export interface AbilityPortrait {
  dimensions: string[]
  values: number[]
}
export function getAbilityPortrait(): Promise<AbilityPortrait> {
  return apiGet<AbilityPortrait>('/profile/ability-portrait')
}
