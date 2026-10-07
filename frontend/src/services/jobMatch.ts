/**
 * 岗位匹配度 + 能力缺口（接口文档 5.3，C-fix 批2）。
 *
 * 对话诊断采集到「学习目标 / 目标岗位」后，用现有静态岗位库（job-market）与用户能力
 * （6 维基线画像 + 已掌握 KP 抬升）对标，得到匹配度与能力缺口——让学情概览里的目标
 * 岗位信息有真实出处。**仅用现有静态数据，不联网采集。**
 *
 * - 联调（USE_REAL_API）：POST /job-market/match（后端用 ability-portrait + Mastery 对标）；
 * - mock：本地推断目标岗位 + 读静态岗位快照 radar，与基线能力对标，口径与后端一致。
 */
import { USE_REAL_API, apiPost } from './api'
import { HOT_JOBS, getJobMarket } from './jobMarket'

export interface JobGap {
  dimension: string
  /** 用户当前水平 */
  level: number
  /** 岗位需求水平 */
  demand: number
  /** 缺口 = demand - level（> 0） */
  gap: number
}

export interface JobMatchResult {
  jobId: string
  jobName: string
  /** 匹配度 0-100（达标维度占比） */
  matchPct: number
  /** 能力缺口（未达标维度，按缺口降序） */
  gaps: JobGap[]
  radar: { dimensions: string[]; values: number[]; demand: number[] }
}

/* 6 维能力坐标系（与岗位 radar 字段、后端 ABILITY_DIMENSIONS 对齐） */
const ABILITY_DIMS = ['机器学习基础', '神经网络', '深度学习', '注意力机制', 'Transformer', '大模型微调']
/* 基线能力（接口文档 4.1/4.4 示例，与后端 _MOCK_BASELINE 一致） */
const BASELINE = [85, 72, 68, 45, 30, 20]
/* KP → 能力维下标（已掌握 → 抬升），与后端 _KP_TO_ABILITY 同口径 */
const KP_TO_DIM: Record<string, number> = { ml: 0, nn: 1, dl: 2, cnn: 2, transformer: 4, finetune: 5 }
const MASTERED_FLOOR = 82

const JOB_KEYWORDS: Record<string, string[]> = {
  'llm-app': ['大模型', 'llm', '应用', 'prompt', 'rag', 'agent', 'gpt', 'aigc', '生成式'],
  'algo-engineer': ['算法'],
  'ml-engineer': ['机器学习', '建模', '训练'],
  'data-analyst': ['数据分析', '数据', '分析', 'bi', '报表', '可视化'],
}

/** 由学习目标自由文本推断目标岗位 id（命中关键词最多者；无命中 → null）。 */
export function inferJobId(goal: string): string | null {
  const text = (goal || '').toLowerCase()
  if (!text) return null
  let best: string | null = null
  let bestScore = 0
  for (const job of HOT_JOBS) {
    let score = 0
    const name = job.name || ''
    const core = name.replace('工程师', '').replace('分析师', '')
    for (const kw of [name.toLowerCase(), core.toLowerCase()]) {
      if (kw.length >= 2 && text.includes(kw)) score += 2
    }
    for (const kw of JOB_KEYWORDS[job.id] || []) if (text.includes(kw)) score += 1
    if (score > bestScore) {
      bestScore = score
      best = job.id
    }
  }
  return bestScore > 0 ? best : null
}

function computeMatch(
  radar: Record<string, number>,
  levels: number[]
): { matchPct: number; gaps: JobGap[]; demand: number[] } {
  const demand = ABILITY_DIMS.map((d) => Math.round(radar[d] ?? 0))
  let met = 0
  const gaps: JobGap[] = []
  ABILITY_DIMS.forEach((d, i) => {
    const lvl = levels[i]
    const dem = demand[i]
    if (lvl >= dem) met++
    else gaps.push({ dimension: d, level: lvl, demand: dem, gap: dem - lvl })
  })
  gaps.sort((a, b) => b.gap - a.gap)
  return { matchPct: Math.round((met / ABILITY_DIMS.length) * 100), gaps, demand }
}

/**
 * 计算岗位匹配结果。goal=学习目标文本（对话诊断的 learning_goal 值）。
 * masteredKps=已掌握知识点（让"掌握度"参与对标）。无法匹配岗位时返回 null。
 */
export async function getJobMatch(
  goal: string,
  opts?: { masteredKps?: string[] }
): Promise<JobMatchResult | null> {
  if (USE_REAL_API) {
    try {
      return await apiPost<JobMatchResult>('/job-market/match', { goal })
    } catch {
      return null
    }
  }
  // mock：本地推断 + 静态快照对标
  const jobId = inferJobId(goal)
  if (!jobId) return null
  const levels = [...BASELINE]
  for (const kp of opts?.masteredKps || []) {
    const i = KP_TO_DIM[kp]
    if (i !== undefined) levels[i] = Math.max(levels[i], MASTERED_FLOOR)
  }
  try {
    const { data } = await getJobMarket(jobId)
    const { matchPct, gaps, demand } = computeMatch(data.radar, levels)
    return { jobId, jobName: data.name, matchPct, gaps, radar: { dimensions: ABILITY_DIMS, values: levels, demand } }
  } catch {
    return null
  }
}
