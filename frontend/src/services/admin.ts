/**
 * 管理端数据获取层（接口文档第 14 章，B4-a 知识库 14.1–14.4 + B4-b Prompt/指标 14.5–14.6）。
 * 全部接口要求 role=admin（后端 require_admin，非管理员 → code 1003 / 403）。
 */
import { apiGet, apiPost, apiPostForm, apiRequest } from './api'

/** 文档入库状态机（接口文档 14.1） */
export type KbDocStatus = 'pending' | 'indexing' | 'indexed' | 'failed'

/** 知识库文档（KnowledgeDocument，接口文档 14.2） */
export interface KbDocument {
  id: string
  title: string
  filename: string
  size: number
  category?: string | null
  status: KbDocStatus
  chunks: number
  uploadedAt: string | null
}

export interface KbDocumentPage {
  items: KbDocument[]
  total: number
  page: number
  pageSize: number
}

export interface KbUploadResult {
  taskId: string
  documents: KbDocument[]
}

export interface KbDeleteResult {
  id: string
  deleted: boolean
  removedChunks: number
}

/** 检索测试命中切片（接口文档 14.4） */
export interface KbSearchHit {
  chunkId: string
  documentId: string
  documentTitle: string
  content: string
  /** rerank 重排分 0-1（降级时为 RRF 归一化分） */
  score: number
  /** 向量余弦相似度 0-1 */
  vectorScore: number
  /** BM25 原始分（可选） */
  bm25Score?: number | null
  sourceLocation: string
}

export interface KbSearchResult {
  rerankerUsed: boolean
  results: KbSearchHit[]
}

/** 14.2 文档列表（分页 + keyword/status 过滤）。 */
export function listKbDocuments(
  params: { page?: number; pageSize?: number; keyword?: string; status?: KbDocStatus } = {}
): Promise<KbDocumentPage> {
  const qs = new URLSearchParams()
  if (params.page) qs.set('page', String(params.page))
  if (params.pageSize) qs.set('pageSize', String(params.pageSize))
  if (params.keyword) qs.set('keyword', params.keyword)
  if (params.status) qs.set('status', params.status)
  const suffix = qs.toString()
  return apiGet<KbDocumentPage>(`/admin/kb/documents${suffix ? `?${suffix}` : ''}`)
}

/** 14.1 multipart 上传，返回 taskId（入库异步，列表轮询刷新状态）+ pending 文档。 */
export function uploadKbDocuments(files: File[], category?: string, tags?: string): Promise<KbUploadResult> {
  const form = new FormData()
  files.forEach((f) => form.append('files', f))
  if (category) form.append('category', category)
  if (tags) form.append('tags', tags)
  return apiPostForm<KbUploadResult>('/admin/kb/upload', form)
}

/** 14.3 删除文档 + 同步清理向量切片，返回 removedChunks。 */
export function deleteKbDocument(id: string): Promise<KbDeleteResult> {
  return apiRequest<KbDeleteResult>(`/admin/kb/documents/${id}`, { method: 'DELETE' })
}

/** 14.4 检索测试：混合检索 → 重排，返回 chunk + rerank/vector/bm25 分数 + 来源定位。 */
export function kbSearchTest(query: string, topK = 5): Promise<KbSearchResult> {
  return apiPost<KbSearchResult>('/admin/kb/search-test', { query, topK })
}

/* ===== B4-b：Prompt 模板（14.5）+ 系统指标（14.6） ===== */

/** Agent 标识（与接口文档 11.2 agents[].id 对齐，固定 3 项） */
export type AgentId = 'diagnosis' | 'generation' | 'critic'

/** Agent Prompt 模板（接口文档 14.5） */
export interface PromptTemplate {
  agentId: AgentId
  name: string
  template: string
  /** 必需占位符清单（模板中须保留 {var} 形式） */
  variables: string[]
  /** 每次保存自增 */
  version: number
  updatedAt: string | null
}

/** 14.5 PUT 响应：保存即热更新 */
export interface PromptUpdateResult {
  agentId: AgentId
  version: number
  updatedAt: string
  hotReloaded: boolean
}

/** 系统指标（接口文档 14.6）：三比率 0-1 + 三计数 */
export interface AdminMetrics {
  hallucinationRate: number
  adaptationRate: number
  coverageRate: number
  kbDocuments: number
  kbChunks: number
  generatedResources: number
  updatedAt: string
}

/** 14.5 获取 Agent Prompt 模板。 */
export function getPromptTemplate(agentId: AgentId): Promise<PromptTemplate> {
  return apiGet<PromptTemplate>(`/admin/prompts/${agentId}`)
}

/** 14.5 更新模板（保存即热更新，version 自增）。缺失必需占位符 → code 1001。 */
export function updatePromptTemplate(agentId: AgentId, template: string): Promise<PromptUpdateResult> {
  return apiRequest<PromptUpdateResult>(`/admin/prompts/${agentId}`, { method: 'PUT', body: { template } })
}

/** 14.6 系统指标看板（三比率为 B8 前占位值，结构契约定死）。 */
export function getAdminMetrics(): Promise<AdminMetrics> {
  return apiGet<AdminMetrics>('/admin/metrics')
}

/* ===== B9：管理端用户管理（接口文档 16.2–16.4） ===== */

/** 用户列表行（接口文档 16.2） */
export interface AdminUserRow {
  userId: string
  username: string
  role: 'learner' | 'admin'
  /** false 表示已禁用，登录受阻 */
  isActive: boolean
  hasDiagnosed: boolean
  /** 已通过（passed）知识点数 */
  passedKpCount: number
  createdAt: string | null
  /** 最近登录时间；从未登录为 null */
  lastActiveAt: string | null
}

export interface AdminUserPage {
  items: AdminUserRow[]
  total: number
  page: number
  pageSize: number
}

/** 16.3 重置进度结果 */
export interface ResetProgressResult {
  userId: string
  hasDiagnosed: boolean
  masteryCleared: number
}

/** 16.4 启停结果 */
export interface ToggleActiveResult {
  userId: string
  isActive: boolean
}

/** 16.2 用户列表（分页）。 */
export function listAdminUsers(params: { page?: number; pageSize?: number } = {}): Promise<AdminUserPage> {
  const qs = new URLSearchParams()
  if (params.page) qs.set('page', String(params.page))
  if (params.pageSize) qs.set('pageSize', String(params.pageSize))
  const suffix = qs.toString()
  return apiGet<AdminUserPage>(`/admin/users${suffix ? `?${suffix}` : ''}`)
}

/** 16.3 重置用户学习进度（清空掌握度 + 复位旅程，幂等）。对 admin 目标 → code 1003。 */
export function resetUserProgress(userId: string): Promise<ResetProgressResult> {
  return apiPost<ResetProgressResult>(`/admin/users/${userId}/reset-progress`)
}

/** 16.4 启用/禁用账号（翻转 isActive）。对 admin 目标 → code 1003。 */
export function toggleUserActive(userId: string): Promise<ToggleActiveResult> {
  return apiPost<ToggleActiveResult>(`/admin/users/${userId}/toggle-active`)
}
