import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { USE_REAL_API } from '../services/api'
import { getStudentPortrait, type PortraitDimension } from '../services/profileDialogue'

/**
 * 已确认的「学习画像快照」。学情概览（首页）据此合成真实数据——
 * 未诊断（dims 为空）时首页视为「无画像」，呈引导态、不展示任何概览数据。
 *
 * 写入时机：对话诊断「确认画像」、或传统表单完成时落快照（见 ProfileDialogue / ProfileBuilder）。
 * 联调（USE_REAL_API）下 GET /profile/student-portrait 为权威源，loadPortrait() 拉取覆盖；
 * 持久化到 localStorage（zx-portrait），与 journey/mastery 一并由「重置引导进度」清空。
 */
interface PortraitState {
  dims: PortraitDimension[]
  updatedAt?: string
  /** 确认画像时写入快照（学情概览据此合成） */
  setPortrait: (dims: PortraitDimension[], updatedAt?: string) => void
  /** 联调：登录后从后端 student-portrait 拉取覆盖本地快照 */
  loadPortrait: () => Promise<void>
  reset: () => void
}

export const usePortrait = create<PortraitState>()(
  persist(
    (set) => ({
      dims: [],
      updatedAt: undefined,
      setPortrait: (dims, updatedAt) =>
        set({ dims, updatedAt: updatedAt ?? new Date().toISOString() }),
      loadPortrait: async () => {
        if (!USE_REAL_API) return
        try {
          const p = await getStudentPortrait()
          if (p.dimensions.length) set({ dims: p.dimensions, updatedAt: p.updatedAt })
        } catch (e) {
          console.error('[portrait] 加载学生画像失败', e)
        }
      },
      reset: () => set({ dims: [], updatedAt: undefined }),
    }),
    { name: 'zx-portrait' }
  )
)
