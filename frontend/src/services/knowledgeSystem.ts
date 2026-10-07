/**
 * AI 知识体系数据获取层（接口文档 10.2，端点索引 26b）。
 * GET /api/v1/knowledge-system —— 78 点 / 7 板块目录 + 当前用户板块级能力覆盖。
 *
 * 供「知识图谱分层可视化」（会话二）消费：真实模式以后端为权威源；
 * mock 模式 / 请求失败时上层回退 KNOWLEDGE_SYSTEM_SEED（见 data/knowledgeSystem.ts）。
 * 既有 10.1 GET /knowledge-graph（接口 26，12 节点掌握度着色）签名不变、继续保留。
 */
import { apiGet } from './api'
import type { KnowledgeSystemData } from '../data/knowledgeSystem'

export type { KnowledgeSystemData } from '../data/knowledgeSystem'

/** 10.2 获取 78 点 / 7 板块体系目录 + 板块级覆盖。 */
export function getKnowledgeSystem(): Promise<KnowledgeSystemData> {
  return apiGet<KnowledgeSystemData>('/knowledge-system')
}
