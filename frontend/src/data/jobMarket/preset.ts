/* 预置岗位库（离线降级用）：联网/快照获取失败时回退到这份内置数据。
   与 public/data/job-market/*.json 镜像；线上由离线管线定期覆盖 public 快照。 */
export interface JobSkill {
  name: string
  freqPct: number
}

export interface JobMarket {
  id: string
  name: string
  salaryRange: string
  salaryMedian: string
  heat: string
  heatPct: number
  openings: number
  source: string
  fetchedAt: string
  skills: JobSkill[]
  /** 岗位要求（按雷达图 6 个维度），用于雷达"岗位要求"线与 Gap 分析 */
  radar: Record<string, number>
}

export interface HotJob {
  id: string
  name: string
}

export const HOT_JOBS: HotJob[] = [
  { id: 'llm-app', name: '大模型应用工程师' },
  { id: 'algo-engineer', name: '算法工程师' },
  { id: 'ml-engineer', name: '机器学习工程师' },
  { id: 'data-analyst', name: '数据分析师' },
]

export const PRESET: Record<string, JobMarket> = {
  'llm-app': {
    id: 'llm-app',
    name: '大模型应用工程师',
    salaryRange: '30K–55K · 15薪',
    salaryMedian: '40K',
    heat: '极高',
    heatPct: 95,
    openings: 1840,
    source: '预置岗位库（离线）',
    fetchedAt: '2026-06-06T06:10:00+08:00',
    skills: [
      { name: 'Prompt 工程', freqPct: 88 },
      { name: 'RAG / 向量检索', freqPct: 82 },
      { name: 'Python', freqPct: 80 },
      { name: 'LangChain / LangGraph', freqPct: 71 },
      { name: '大模型微调(LoRA)', freqPct: 58 },
      { name: 'Agent 编排', freqPct: 49 },
    ],
    radar: { 机器学习基础: 70, 神经网络: 72, 深度学习: 78, 注意力机制: 85, Transformer: 92, 大模型微调: 88 },
  },
  'algo-engineer': {
    id: 'algo-engineer',
    name: '算法工程师',
    salaryRange: '25K–50K · 14薪',
    salaryMedian: '35K',
    heat: '高',
    heatPct: 86,
    openings: 2360,
    source: '预置岗位库（离线）',
    fetchedAt: '2026-06-06T05:40:00+08:00',
    skills: [
      { name: '机器学习', freqPct: 90 },
      { name: 'Python', freqPct: 88 },
      { name: '深度学习', freqPct: 76 },
      { name: 'PyTorch / TensorFlow', freqPct: 70 },
      { name: '数据结构与算法', freqPct: 64 },
      { name: 'CNN / RNN', freqPct: 52 },
    ],
    radar: { 机器学习基础: 92, 神经网络: 85, 深度学习: 88, 注意力机制: 60, Transformer: 62, 大模型微调: 45 },
  },
  'ml-engineer': {
    id: 'ml-engineer',
    name: '机器学习工程师',
    salaryRange: '28K–52K · 15薪',
    salaryMedian: '38K',
    heat: '高',
    heatPct: 88,
    openings: 1560,
    source: '预置岗位库（离线）',
    fetchedAt: '2026-06-06T06:00:00+08:00',
    skills: [
      { name: '机器学习', freqPct: 91 },
      { name: '深度学习', freqPct: 80 },
      { name: 'Python', freqPct: 86 },
      { name: 'MLOps / 工程化', freqPct: 62 },
      { name: 'PyTorch', freqPct: 68 },
      { name: '分布式训练', freqPct: 44 },
    ],
    radar: { 机器学习基础: 90, 神经网络: 88, 深度学习: 90, 注意力机制: 70, Transformer: 72, 大模型微调: 60 },
  },
  'data-analyst': {
    id: 'data-analyst',
    name: '数据分析师',
    salaryRange: '15K–30K · 13薪',
    salaryMedian: '22K',
    heat: '中',
    heatPct: 64,
    openings: 3120,
    source: '预置岗位库（离线）',
    fetchedAt: '2026-06-06T05:20:00+08:00',
    skills: [
      { name: 'SQL', freqPct: 94 },
      { name: 'Excel / BI', freqPct: 78 },
      { name: 'Python', freqPct: 66 },
      { name: '统计分析', freqPct: 60 },
      { name: '数据可视化', freqPct: 55 },
      { name: '机器学习基础', freqPct: 38 },
    ],
    radar: { 机器学习基础: 58, 神经网络: 35, 深度学习: 30, 注意力机制: 18, Transformer: 15, 大模型微调: 10 },
  },
}
