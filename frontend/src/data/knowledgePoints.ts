/* 贯穿 学习路径 / 知识图谱 / 学习资源 / 学情概览 的核心知识点注册表。
   id 复用知识图谱节点 id，并映射到学习路径课程 sequence，作为闭环更新的唯一锚点。*/
export interface KnowledgePoint {
  id: string
  name: string
  lessonSeq: number // 对应 data/learningPath.ts 的 lesson.sequence
  graphNodeId: string // 对应 KnowledgeGraph 的 node.id
}

export const KNOWLEDGE_POINTS: KnowledgePoint[] = [
  { id: 'ml', name: '机器学习基础', lessonSeq: 1, graphNodeId: 'ml' },
  { id: 'nn', name: '神经网络基础', lessonSeq: 2, graphNodeId: 'nn' },
  { id: 'dl', name: '深度学习原理', lessonSeq: 3, graphNodeId: 'dl' },
  { id: 'cnn', name: 'CNN架构', lessonSeq: 4, graphNodeId: 'cnn' },
  { id: 'transformer', name: 'Transformer架构', lessonSeq: 5, graphNodeId: 'transformer' },
  { id: 'finetune', name: '大模型微调技术', lessonSeq: 6, graphNodeId: 'finetune' },
]

/** 资源页当前正在学习的知识点 */
export const CURRENT_KP_ID = 'nn'

export const kpByLessonSeq = (seq: number) => KNOWLEDGE_POINTS.find((k) => k.lessonSeq === seq)
export const kpByGraphNode = (nodeId: string) => KNOWLEDGE_POINTS.find((k) => k.graphNodeId === nodeId)
export const kpById = (id: string) => KNOWLEDGE_POINTS.find((k) => k.id === id)
