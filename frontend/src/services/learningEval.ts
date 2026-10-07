/**
 * 学习过程评估（接口文档 12.2，C-fix 批3）。
 *
 * 联调走后端「学习评估 Agent」（GET /dashboard/evaluation）——基于真实做题/进度/步骤
 * 数据产出多维评估 + 方法建议 + 动态调整。mock 模式由前端按掌握度确定性合成（同结构、
 * 同口径骨架），保证无后端也能渲染评估面板。
 */
import { apiGet } from './api'

export interface EvalDimension {
  key: string
  label: string
  score: number
  detail: string
}
export interface EvalWeakPoint {
  kpId: string
  name: string
  reason: string
}
export interface EvalAdjustment {
  nextKpId: string | null
  nextKpName: string | null
  difficultyAdvice: string
  action: string
}
export type EvalTrend = 'improving' | 'declining' | 'stable'

export interface LearningEvaluation {
  overallScore: number
  level: string
  trend: EvalTrend
  dimensions: EvalDimension[]
  weakPoints: EvalWeakPoint[]
  summary: string
  suggestions: string[]
  adjustment: EvalAdjustment
  generatedBy: string
  signals: {
    masteredCount: number
    totalCore: number
    attemptCount: number
    avgBestScore: number
    stepsDone: number
    notesFilled: number
    retries: number
  }
}

/** 12.2 学习过程评估（联调）。 */
export function getLearningEvaluation(): Promise<LearningEvaluation> {
  return apiGet<LearningEvaluation>('/dashboard/evaluation')
}

const clamp = (x: number) => Math.max(0, Math.min(100, Math.round(x)))

/**
 * mock 兜底：由掌握度本地合成评估（前端无做题历史，按已掌握知识点确定性派生）。
 * 与后端 learning_eval 同结构、同维度，保证面板在 mock 模式可渲染、口径一致。
 */
export function synthesizeEvaluation(
  kps: { id: string; name: string }[],
  masteredIds: string[]
): LearningEvaluation {
  const total = kps.length || 1
  const mastered = masteredIds.length
  const masteredSet = new Set(masteredIds)
  const masteryProgress = clamp((mastered / total) * 100)
  // mock 无做题历史：已通过即视为测验达标（≥60），未通过则 0；投入按已掌握数派生
  const quizPerformance = mastered > 0 ? 72 : 0
  const efficiency = mastered > 0 ? 65 : 50
  const engagement = clamp(mastered * 16)
  const overall = clamp(
    masteryProgress * 0.35 + quizPerformance * 0.3 + efficiency * 0.2 + engagement * 0.15
  )
  const level =
    overall >= 80 ? '表现优异' : overall >= 60 ? '稳步推进' : overall >= 40 ? '起步阶段' : '刚刚起步'

  const dimensions: EvalDimension[] = [
    { key: 'mastery_progress', label: '掌握进度', score: masteryProgress, detail: `已通过 ${mastered}/${total} 个核心知识点` },
    { key: 'quiz_performance', label: '测验表现', score: quizPerformance, detail: mastered > 0 ? '已通过知识点测验达标' : '暂无测验记录' },
    { key: 'learning_efficiency', label: '学习效率', score: efficiency, detail: '趋势平稳' },
    { key: 'engagement', label: '学习投入', score: engagement, detail: `已掌握 ${mastered} 个知识点` },
  ]

  const weakPoints: EvalWeakPoint[] = kps
    .filter((k) => !masteredSet.has(k.id))
    .slice(0, 3)
    .map((k) => ({ kpId: k.id, name: k.name, reason: '尚未通过阶段测试' }))

  const next = weakPoints[0]
  const suggestions: string[] = []
  if (weakPoints.length) suggestions.push(`优先复习薄弱点：${weakPoints.map((w) => w.name).join('、')}，配合讲义/图解后做阶段测试巩固。`)
  if (masteryProgress >= 80) suggestions.push('基础掌握扎实，可尝试上调讲义难度到「高级」拓展深度。')
  if (!suggestions.length) suggestions.push('继续按学习路径推进，保持测验与笔记的规律使用。')

  return {
    overallScore: overall,
    level,
    trend: 'stable',
    dimensions,
    weakPoints,
    summary:
      mastered > 0
        ? `你已掌握 ${mastered}/${total} 个核心知识点，整体处于「${level}」阶段。`
        : `你尚未通过任何核心知识点，整体处于「${level}」阶段，建议尽快做一次阶段测试以校准学情。`,
    suggestions: suggestions.slice(0, 4),
    adjustment: {
      nextKpId: next?.kpId ?? null,
      nextKpName: next?.name ?? null,
      difficultyAdvice: masteryProgress >= 80 ? '建议将讲义难度上调到「高级」深入。' : '建议维持「初级」难度，稳步推进、边学边测。',
      action: next ? `下一步优先攻克「${next.name}」：先看讲义 + 图解，再做阶段测试。` : '核心知识点已全部通过，可转入复习巩固或挑战更高难度内容。',
    },
    generatedBy: 'mock',
    signals: { masteredCount: mastered, totalCore: total, attemptCount: 0, avgBestScore: 0, stepsDone: 0, notesFilled: 0, retries: 0 },
  }
}
