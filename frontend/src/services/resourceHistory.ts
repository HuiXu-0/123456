/**
 * 我的资源库·生成历史数据获取层（接口文档第 19 章）。
 *
 * - 联调（USE_REAL_API）：GET /resource/history 取当前用户的真实生成历史；
 *   讲义/视频/图解由后端各生成接口旁路自动埋点，思维导图/代码由前端调 logResourceGeneration 补埋。
 * - mock：本地合成一组示例记录（跨知识点/形态/难度/时间），供无后端时展示与筛选。
 *
 * 与无用户归属的 ResourceCache 全局缓存不同：这是「当前用户的生成历史」。
 */
import { apiGet, apiPost, apiRequest, USE_REAL_API } from './api'
import { KNOWLEDGE_POINTS, kpById } from '../data/knowledgePoints'

/**
 * 可入库资产形态（归一化基类）。
 * 内置课程：lecture/video/diagram/mindmap/code；
 * 文档学习平行链路额外产出 quiz（练习题）/ flashcard（记忆闪卡）两类。
 */
export type ResourceKind = 'lecture' | 'video' | 'diagram' | 'mindmap' | 'code' | 'quiz' | 'flashcard'

/** 单条生成历史记录（契约 19.1 items[]）。 */
export interface ResourceHistoryItem {
  id: number
  kpId: string
  kpName: string
  kind: ResourceKind
  difficulty: string
  title: string
  resourceRef: string
  createdAt: string
  /** 来源：内置课程 builtin / 文档学习 document（后端 19.1 返回；mock 缺省视为 builtin）。 */
  source?: 'builtin' | 'document'
  /** 文档学习资源的来源文档 id / 标题（source==='document' 时有值，用于预览按文档重建内容）。 */
  docId?: string | null
  docTitle?: string | null
}

export interface ResourceHistoryPage {
  items: ResourceHistoryItem[]
  total: number
  page: number
  pageSize: number
}

export interface ResourceHistoryQuery {
  page?: number
  pageSize?: number
  kind?: ResourceKind | ''
  kpId?: string
  /** ISO8601 或 YYYY-MM-DD */
  startTime?: string
  endTime?: string
}

/** 形态 → 中文形态名（页面标签/图标复用）。 */
export const KIND_LABEL: Record<ResourceKind, string> = {
  lecture: '定制讲义',
  video: '讲解视频',
  diagram: '知识图解',
  mindmap: '思维导图',
  code: '代码实操',
  quiz: '练习题',
  flashcard: '记忆闪卡',
}

/* ---------------- mock：本地合成示例记录（无后端时展示 + 客户端筛选） ---------------- */

/** 用 KP_RESOURCES 覆盖到的知识点合成样例（保证预览有内容）；nn 用后端/内置兜底，样例不含。 */
const MOCK_SEED: { kpId: string; kind: ResourceKind; difficulty: string; hoursAgo: number }[] = [
  { kpId: 'cnn', kind: 'lecture', difficulty: '初级', hoursAgo: 1 },
  { kpId: 'cnn', kind: 'video', difficulty: '初级', hoursAgo: 2 },
  { kpId: 'cnn', kind: 'diagram', difficulty: '', hoursAgo: 3 },
  { kpId: 'transformer', kind: 'lecture', difficulty: '高级', hoursAgo: 20 },
  { kpId: 'transformer', kind: 'mindmap', difficulty: '', hoursAgo: 22 },
  { kpId: 'dl', kind: 'lecture', difficulty: '初级', hoursAgo: 26 },
  { kpId: 'dl', kind: 'code', difficulty: '', hoursAgo: 27 },
  { kpId: 'ml', kind: 'diagram', difficulty: '', hoursAgo: 50 },
  { kpId: 'ml', kind: 'lecture', difficulty: '入门', hoursAgo: 52 },
]

function buildMockRecords(): ResourceHistoryItem[] {
  const now = Date.now()
  return MOCK_SEED.map((s, i) => {
    const name = kpById(s.kpId)?.name ?? s.kpId
    const label = KIND_LABEL[s.kind]
    const title = s.difficulty ? `${name} · ${label}（${s.difficulty}）` : `${name} · ${label}`
    return {
      id: 1000 + i,
      kpId: s.kpId,
      kpName: name,
      kind: s.kind,
      difficulty: s.difficulty,
      title,
      resourceRef: `${s.kind}:${s.kpId}:${s.difficulty}`,
      createdAt: new Date(now - s.hoursAgo * 3600_000).toISOString(),
    }
  })
}

function filterMock(all: ResourceHistoryItem[], q: ResourceHistoryQuery): ResourceHistoryItem[] {
  let items = all
  if (q.kind) items = items.filter((r) => r.kind === q.kind)
  if (q.kpId) items = items.filter((r) => r.kpId === q.kpId)
  if (q.startTime) {
    const t = Date.parse(q.startTime)
    if (!Number.isNaN(t)) items = items.filter((r) => Date.parse(r.createdAt) >= t)
  }
  if (q.endTime) {
    const t = Date.parse(q.endTime)
    if (!Number.isNaN(t)) items = items.filter((r) => Date.parse(r.createdAt) <= t)
  }
  return items
}

/** 19.1 获取我的资源库（按用户分页、可按 kind/知识点/时间过滤）。 */
export async function getResourceHistory(q: ResourceHistoryQuery = {}): Promise<ResourceHistoryPage> {
  const page = q.page ?? 1
  const pageSize = q.pageSize ?? 20
  if (!USE_REAL_API) {
    const all = filterMock(buildMockRecords(), q).sort(
      (a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt)
    )
    const start = (page - 1) * pageSize
    return { items: all.slice(start, start + pageSize), total: all.length, page, pageSize }
  }
  const params = new URLSearchParams()
  params.set('page', String(page))
  params.set('pageSize', String(pageSize))
  if (q.kind) params.set('kind', q.kind)
  if (q.kpId) params.set('kpId', q.kpId)
  if (q.startTime) params.set('startTime', q.startTime)
  if (q.endTime) params.set('endTime', q.endTime)
  return apiGet<ResourceHistoryPage>(`/resource/history?${params.toString()}`)
}

/** 全部可选知识点（供页面筛选下拉）。 */
export const HISTORY_KPS = KNOWLEDGE_POINTS

/**
 * 19.2 追加生成埋点（仅联调有效）：为「无独立后端生成接口」的形态（思维导图/代码）补埋点。
 * mock 模式 no-op；失败仅告警，绝不打断调用方主链路。upsert 幂等，重复调用安全。
 */
export async function logResourceGeneration(input: {
  kpId: string
  kind: ResourceKind
  difficulty?: string
  title?: string
}): Promise<void> {
  if (!USE_REAL_API) return
  try {
    await apiPost('/resource/history/log', {
      kpId: input.kpId,
      kind: input.kind,
      difficulty: input.difficulty ?? '',
      title: input.title ?? null,
    })
  } catch (e) {
    console.warn('[resourceHistory] 生成埋点失败（已忽略）', e)
  }
}

/**
 * 19.3 重命名资产标题（CRUD·改，仅联调有效）。按 user 校验归属；非本人/不存在 → 抛 ApiError(1004)。
 * mock 模式 no-op（本地样例不落库），返回传入标题占位。
 */
export async function renameResource(id: number, title: string): Promise<ResourceHistoryItem | null> {
  if (!USE_REAL_API) return null
  return apiPost<ResourceHistoryItem>('/resource/history/rename', { id, title })
}

/**
 * 19.4 删除资产（CRUD·删，连带已落库产物，仅联调有效）。按 user 校验归属；非本人/不存在 → 抛 ApiError(1004)。
 * mock 模式 no-op。
 */
export async function deleteResource(id: number): Promise<void> {
  if (!USE_REAL_API) return
  await apiRequest(`/resource/history/${id}`, { method: 'DELETE' })
}
