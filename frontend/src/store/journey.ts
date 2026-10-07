import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { USE_REAL_API } from '../services/api'
import { completeDiagnosisApi } from '../services/profile'
import { generatePath as generatePathApi } from '../services/learning'
import { getJourney } from '../services/mastery'

/**
 * 学习闭环进度（画像→学习→评估→画像更新）的前端状态。
 * 仅两个主标志即可推导"用户当前处于哪一步"，持久化到 localStorage。
 *
 * 联调（USE_REAL_API）下，后端 GET /journey 为权威源，localStorage 退化为缓存：
 * 登录后 loadJourney() 从后端拉取覆盖；completeDiagnosis / generatePath 在本地乐观
 * 更新的同时把写操作同步到后端（POST /profile/diagnosis-complete、
 * POST /learning-path/generate 并轮询 taskId）。
 */
export type JourneyStep = 'diagnose' | 'generate-path' | 'learn' | 'review'

interface JourneyState {
  /** 已完成 ① 画像诊断 */
  hasDiagnosed: boolean
  /** 已生成 ② 学习路径 */
  hasGeneratedPath: boolean
  /** 画像诊断选定的目标岗位（用于首页轻量摘要）*/
  targetJobName?: string
  /** 与目标岗位的对标进度 %（达标维度占比，用于首页轻量摘要）*/
  matchPct?: number
  completeDiagnosis: (result?: { targetJobName?: string; matchPct?: number }) => void
  generatePath: () => void
  /** 联调：从后端拉取旅程状态覆盖本地缓存（登录后调用）*/
  loadJourney: () => Promise<void>
  resetJourney: () => void
}

export const useJourney = create<JourneyState>()(
  persist(
    (set) => ({
      hasDiagnosed: false,
      hasGeneratedPath: false,
      targetJobName: undefined,
      matchPct: undefined,
      completeDiagnosis: (result) => {
        set({
          hasDiagnosed: true,
          ...(result?.targetJobName !== undefined ? { targetJobName: result.targetJobName } : {}),
          ...(result?.matchPct !== undefined ? { matchPct: result.matchPct } : {}),
        })
        if (USE_REAL_API && result?.targetJobName !== undefined && result?.matchPct !== undefined) {
          completeDiagnosisApi(result.targetJobName, result.matchPct).catch((e) =>
            console.error('[journey] diagnosis-complete 同步失败', e)
          )
        }
      },
      generatePath: () => {
        set({ hasGeneratedPath: true })
        if (USE_REAL_API) {
          generatePathApi().catch((e) => console.error('[journey] generate-path 同步失败', e))
        }
      },
      loadJourney: async () => {
        if (!USE_REAL_API) return
        try {
          const j = await getJourney()
          set({
            hasDiagnosed: j.hasDiagnosed,
            hasGeneratedPath: j.hasGeneratedPath,
            targetJobName: j.targetJobName ?? undefined,
            matchPct: j.matchPct ?? undefined,
          })
        } catch (e) {
          console.error('[journey] 加载旅程状态失败', e)
        }
      },
      resetJourney: () =>
        set({ hasDiagnosed: false, hasGeneratedPath: false, targetJobName: undefined, matchPct: undefined }),
    }),
    { name: 'zx-progress' }
  )
)

/** 由两个主标志（+ 路径是否学完）派生当前所处步骤 */
export function getJourneyStep(
  s: Pick<JourneyState, 'hasDiagnosed' | 'hasGeneratedPath'>,
  allLessonsDone = false
): JourneyStep {
  if (!s.hasDiagnosed) return 'diagnose'
  if (!s.hasGeneratedPath) return 'generate-path'
  if (allLessonsDone) return 'review'
  return 'learn'
}
