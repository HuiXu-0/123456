/**
 * 知识图谱数据获取层（接口文档第 10 章，接口 26）。
 * 节点 category/value 由后端按掌握度权威推导，前端不再自行推导着色。
 */
import { apiGet } from './api'

export interface GraphNode {
  id: string
  name: string
  /** 0=已掌握 1=学习中 2=待学习 3=知识盲区（后端推导） */
  category: number
  /** 掌握度 %（前端据此算 symbolSize） */
  value: number
}

export interface GraphLink {
  source: string
  target: string
}

export interface KnowledgeGraphData {
  nodes: GraphNode[]
  links: GraphLink[]
  categories: { name: string }[]
}

/** 10.1 获取知识图谱（12 节点 + 14 边 + 4 类别）。 */
export function getKnowledgeGraph(): Promise<KnowledgeGraphData> {
  return apiGet<KnowledgeGraphData>('/knowledge-graph')
}
