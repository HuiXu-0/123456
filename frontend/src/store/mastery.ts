import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { USE_REAL_API } from '../services/api'
import { checkKp, getMastery, passKp } from '../services/mastery'

/**
 * 知识点掌握度状态：完成的唯一判定 = 通过分阶测试（在资源页 quiz 处置位 passed）。
 * 学习路径 / 知识图谱 / 学情概览 都从这里派生，保证"通过后只更新一次、多处同步"。
 *
 * 联调（USE_REAL_API）下后端 GET /mastery 为权威源，localStorage 退化为缓存：
 * 登录 / 进入资源页时 load() 拉取覆盖；goCheck / markPassed 本地乐观更新的同时
 * 把写操作同步到后端（POST /mastery/{id}/check|pass）。
 */
export type KPStatus = 'learning' | 'pending-check' | 'passed'

interface MasteryState {
  status: Record<string, KPStatus>
  /** 点击"去检验"：学习中 → 待检验（已通过不回退）*/
  goCheck: (id: string) => void
  /** 测试通过：→ 已通过（幂等，仅首次置位算新掌握）*/
  markPassed: (id: string) => void
  /** 联调：从后端拉取掌握度全集覆盖本地缓存 */
  load: () => Promise<void>
  reset: () => void
}

/** 初始：空——掌握/学习状态只由真实学习行为（生成讲义→learning、通过测试→passed）写入，
 *  不预置任何 in_progress/completed。保证零基础新用户「路径全节点未开始」（与问题1修复一致）。
 *  资源页对「当前知识点」无状态时回落 'learning'（见 LearningResource `?? 'learning'`），故不依赖此种子。 */
const initial = (): Record<string, KPStatus> => ({})

export const useMastery = create<MasteryState>()(
  persist(
    (set) => ({
      status: initial(),
      goCheck: (id) => {
        set((s) => (s.status[id] === 'passed' ? s : { status: { ...s.status, [id]: 'pending-check' } }))
        if (USE_REAL_API) {
          checkKp(id)
            .then((r) => set((s) => ({ status: { ...s.status, [id]: r.status } })))
            .catch((e) => console.error('[mastery] check 同步失败', e))
        }
      },
      markPassed: (id) => {
        set((s) => (s.status[id] === 'passed' ? s : { status: { ...s.status, [id]: 'passed' } }))
        if (USE_REAL_API) {
          passKp(id).catch((e) => console.error('[mastery] pass 同步失败', e))
        }
      },
      load: async () => {
        if (!USE_REAL_API) return
        try {
          const { status } = await getMastery()
          set({ status })
        } catch (e) {
          console.error('[mastery] 加载掌握度失败', e)
        }
      },
      reset: () => set({ status: initial() }),
    }),
    { name: 'zx-mastery' }
  )
)

export const STATUS_LABEL: Record<KPStatus, string> = {
  learning: '学习中',
  'pending-check': '待检验',
  passed: '已通过',
}
