/**
 * 文档学习全局状态（跨页面切换持久化 + 后台轮询）。
 *
 * 解决的核心问题：
 * 1. App 用 key={currentPage} 切页面会 unmount 组件，useState 全部销毁；
 * 2. 上传/生成的轮询绑在组件上，组件卸载就停了。
 *
 * 方案：docs/bags/genKinds 等状态 + 轮询定时器均放 Zustand store，
 * 组件卸载不影响，回来时从 store 恢复当前状态。
 */
import { create } from 'zustand'
import type { DocumentItem } from '../services/documentLearning'
import { getDocument } from '../services/documentLearning'
import type { OverviewResult } from '../services/documentLearning'

type Kind = 'lecture' | 'video' | 'diagram' | 'mindmap' | 'quiz' | 'flashcards'

interface DocLearningState {
  /** 文档列表 */
  docs: DocumentItem[]
  setDocs: (docs: DocumentItem[] | ((prev: DocumentItem[]) => DocumentItem[])) => void
  /** 按 ID 更新单个文档（轮询回调用） */
  updateDoc: (docId: string, patch: Partial<DocumentItem>) => void

  /** 聚焦文档 ID */
  focusedId: string | null
  setFocusedId: (idOrFn: string | null | ((prev: string | null) => string | null)) => void

  /** 已勾选文档 ID */
  checkedIds: Set<string>
  setCheckedIds: (ids: Set<string> | ((prev: Set<string>) => Set<string>)) => void

  /** 已生成产物（scopeKey → ArtifactBag） */
  bags: Record<string, Record<string, any>>
  setBags: (bags: Record<string, Record<string, any>> | ((prev: Record<string, Record<string, any>>) => Record<string, Record<string, any>>)) => void

  /** 正在生成中的形态 */
  genKinds: Set<Kind>
  setGenKinds: (kinds: Set<Kind> | ((prev: Set<Kind>) => Set<Kind>)) => void

  /** 生成错误 */
  genErr: string | null
  setGenErr: (err: string | null) => void

  /** 难度 */
  difficulty: string
  setDifficulty: (d: string) => void

  /** 文档概览（docId → OverviewResult），跨页面切换持久化 */
  overviews: Record<string, OverviewResult>
  setOverviews: (v: Record<string, OverviewResult> | ((prev: Record<string, OverviewResult>) => Record<string, OverviewResult>)) => void

  /** 正在生成概览中的文档 ID 集合，防重复调用 */
  overviewBusy: Set<string>
  setOverviewBusy: (v: Set<string> | ((prev: Set<string>) => Set<string>)) => void

  /** 文档列表是否已从后端加载过（防止 remount 时闪 loading） */
  docsLoaded: boolean
  setDocsLoaded: (v: boolean) => void

  /** 活跃异步任务（scopeKey+kind → { taskId, status, kind, scopeKey }） */
  activeTasks: Record<string, { taskId: string; status: string; kind: Kind; scopeKey: string }>
  setActiveTasks: (v: Record<string, { taskId: string; status: string; kind: Kind; scopeKey: string }> | ((prev: Record<string, { taskId: string; status: string; kind: Kind; scopeKey: string }>) => Record<string, { taskId: string; status: string; kind: Kind; scopeKey: string }>)) => void

  // ---- 后台轮询 ----
  /** 启动文档状态轮询（每 1.5s 查一次，直到 indexed/failed） */
  startDocPolling: (docId: string) => void
  /** 停止单个文档轮询 */
  stopDocPolling: (docId: string) => void
  /** 停止全部轮询（组件完全卸载时调用，可选） */
  stopAllPolling: () => void
}

export const useDocLearningStore = create<DocLearningState>()((set, get) => {
  /** 活跃轮询定时器：docId → intervalId */
  const _timers: Record<string, ReturnType<typeof setInterval>> = {}

  return {
    docs: [],
    setDocs: (v) => set((s) => ({ docs: typeof v === 'function' ? v(s.docs) : v })),

    updateDoc: (docId, patch) =>
      set((s) => ({
        docs: s.docs.map((d) => (d.id === docId ? { ...d, ...patch } : d)),
      })),

    focusedId: null,
    setFocusedId: (idOrFn) =>
      set((s) => ({
        focusedId:
          typeof idOrFn === 'function' ? idOrFn(s.focusedId) : idOrFn,
      })),

    checkedIds: new Set<string>(),
    setCheckedIds: (v) => set((s) => ({ checkedIds: typeof v === 'function' ? v(s.checkedIds) : v })),

    bags: {},
    setBags: (v) => set((s) => ({ bags: typeof v === 'function' ? v(s.bags) : v })),

    genKinds: new Set<Kind>(),
    setGenKinds: (v) => set((s) => ({ genKinds: typeof v === 'function' ? v(s.genKinds) : v })),

    genErr: null,
    setGenErr: (err) => set({ genErr: err }),

    difficulty: '初级',
    setDifficulty: (d) => set({ difficulty: d }),

    overviews: {},
    setOverviews: (v) => set((s) => ({ overviews: typeof v === 'function' ? v(s.overviews) : v })),

    overviewBusy: new Set<string>(),
    setOverviewBusy: (v) => set((s) => ({ overviewBusy: typeof v === 'function' ? v(s.overviewBusy) : v })),

    docsLoaded: false,
    setDocsLoaded: (v) => set({ docsLoaded: v }),

    activeTasks: {},
    setActiveTasks: (v) => set((s) => ({ activeTasks: typeof v === 'function' ? v(s.activeTasks) : v })),

    // ---- 后台轮询（不依赖组件生命周期） ----
    startDocPolling: (docId) => {
      // 已在轮询则跳过
      if (_timers[docId]) return
      _timers[docId] = setInterval(async () => {
        try {
          const doc = await getDocument(docId)
          get().updateDoc(docId, doc)
          // 终态则停止轮询
          if (doc.status === 'indexed' || doc.status === 'failed') {
            get().stopDocPolling(docId)
          }
        } catch {
          // 网络错误不停止，下次继续试
        }
      }, 1500)
    },

    stopDocPolling: (docId) => {
      if (_timers[docId]) {
        clearInterval(_timers[docId])
        delete _timers[docId]
      }
    },

    stopAllPolling: () => {
      for (const id of Object.keys(_timers)) {
        clearInterval(_timers[id])
        delete _timers[id]
      }
    },
  }
})
