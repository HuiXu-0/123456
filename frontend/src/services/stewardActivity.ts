/**
 * 学情管家·活动聚合层（纯前端只读派生，不新增后端接口）。
 *
 * 「学情管家」是现有能力的收拢展示层：它把系统里各专家 Agent 已经做过的事
 * （诊断 Agent 生成画像、规划 Agent 规划路径、生成 Agent 产出资源、评估 Agent 完成评估）
 * 从既有数据源（journey / portrait / mastery / learningEval / resourceHistory）聚合成
 * 「管家看板」，并基于监测到的真实学情派生「主动建议」。
 *
 * 设计约束：
 * - 不做 Agent 自创建/自调度；此处只读聚合，不改任何既有逻辑与接口签名。
 * - 只用真实/派生数据，绝不臆造（无对应信号的建议不生成）。
 * - 纯函数、无副作用（时间戳等由调用方注入），mock 与联调同口径。
 */
import type { PageType } from '../App'
import type { LearningEvaluation } from './learningEval'

/** 各专家 Agent 的活动状态（监测视角）：done=已产出 / active=进行中 / idle=待触发 */
export type AgentActivityStatus = 'done' | 'active' | 'idle'

export interface AgentActivity {
  key: 'diagnosis' | 'planning' | 'generation' | 'evaluation' | 'docLearning'
  /** 专家 Agent 名称 */
  name: string
  /** 职责一句话 */
  role: string
  status: AgentActivityStatus
  /** 该 Agent 已产出的计量（无则 0） */
  metric: number
  /** 计量单位/名称，如「维画像」「份资源」 */
  metricLabel: string
  /** 最近一次活动摘要 */
  lastAction: string
  /** 点击跳转的关联页面 */
  page: PageType
}

export type SuggestionTone = 'primary' | 'warn' | 'info'

export interface StewardSuggestion {
  id: string
  tone: SuggestionTone
  text: string
  /** 可选行动入口 */
  cta?: string
  page?: PageType
}

export interface StewardSummary {
  agents: AgentActivity[]
  suggestions: StewardSuggestion[]
  /** 已完成产出的 Agent 数 / 总 Agent 数（看板汇总用） */
  activeAgents: number
  totalAgents: number
}

export interface StewardInput {
  hasDiagnosed: boolean
  hasGeneratedPath: boolean
  portraitDimCount: number
  targetJobName?: string | null
  masteredCore: number
  totalCore: number
  /** 我的资源库累计生成数（resourceHistory.total） */
  resourceCount: number
  /** 学习评估（真实或本地合成，均同口径） */
  evaluation: LearningEvaluation
  /** 待提升知识点（来自 overview.weak_topics） */
  weakTopics: { name: string }[]
  /** 已上传文档数（documentLearning.listDocuments().length），缺省不展示文档学习 Agent 计量 */
  docCount?: number
}

/**
 * 聚合各专家 Agent 的活动记录 + 派生主动建议。
 * 全部来自既有能力，纯派生、无网络请求。
 */
export function synthesizeSteward(input: StewardInput): StewardSummary {
  const {
    hasDiagnosed,
    hasGeneratedPath,
    portraitDimCount,
    targetJobName,
    masteredCore,
    resourceCount,
    evaluation,
    weakTopics,
    docCount = 0,
  } = input

  const agents: AgentActivity[] = [
    {
      key: 'diagnosis',
      name: '诊断专家 Agent',
      role: '知识画像与能力诊断',
      status: hasDiagnosed ? 'done' : 'idle',
      metric: portraitDimCount,
      metricLabel: '维画像',
      lastAction: hasDiagnosed
        ? `已生成 ${portraitDimCount} 维能力/偏好画像`
        : '等待画像诊断触发',
      page: 'profile',
    },
    {
      key: 'planning',
      name: '规划专家 Agent',
      role: '个性化学习路径规划',
      status: hasGeneratedPath ? 'done' : 'idle',
      metric: hasGeneratedPath ? 1 : 0,
      metricLabel: '条路径',
      lastAction: hasGeneratedPath
        ? targetJobName
          ? `已规划面向「${targetJobName}」的学习路径`
          : '已生成个性化学习路径'
        : '等待路径规划触发',
      page: 'learning-path',
    },
    {
      key: 'generation',
      name: '生成专家 Agent',
      role: '多模态学习资源生成',
      status: resourceCount > 0 ? 'done' : 'idle',
      metric: resourceCount,
      metricLabel: '份资源',
      lastAction:
        resourceCount > 0
          ? `已产出 ${resourceCount} 份多模态学习资源`
          : '尚未生成学习资源',
      page: 'my-resources',
    },
    {
      key: 'evaluation',
      name: '评估专家 Agent',
      role: '学习过程评估与调整',
      status: masteredCore > 0 || evaluation.signals.attemptCount > 0 ? 'done' : 'idle',
      metric: masteredCore,
      metricLabel: '个知识点',
      lastAction:
        masteredCore > 0 || evaluation.signals.attemptCount > 0
          ? `当前评估「${evaluation.level}」· 综合 ${evaluation.overallScore} 分`
          : '等待学习活动以生成评估',
      page: 'learning-resource',
    },
    {
      key: 'docLearning',
      name: '文档学习 Agent',
      role: '自带文档解析与资料生成',
      status: docCount > 0 ? 'done' : 'idle',
      metric: docCount,
      metricLabel: '篇文档',
      lastAction:
        docCount > 0
          ? `已解析 ${docCount} 篇文档，可基于文档生成资料与问答`
          : '等待上传文档以开启文档学习',
      page: 'document-learning',
    },
  ]

  /* ---- 主动建议：全部基于监测到的真实学情派生，不臆造 ---- */
  const suggestions: StewardSuggestion[] = []

  // 1) 薄弱点优先补强（来自 overview.weak_topics）
  if (weakTopics[0]) {
    suggestions.push({
      id: 'weak',
      tone: 'warn',
      text: `监测到你在「${weakTopics[0].name}」较薄弱，建议优先补强。`,
      cta: '去补强',
      page: 'learning-resource',
    })
  }

  // 2) 下一步（来自评估 Agent 的动态调整）
  if (evaluation.adjustment.nextKpName) {
    suggestions.push({
      id: 'next',
      tone: 'primary',
      text: evaluation.adjustment.action,
      cta: '继续学习',
      page: 'learning-path',
    })
  }

  // 3) 阶段测试巩固：已掌握若干但做题记录偏少时提示
  if (masteredCore >= 2 && evaluation.signals.attemptCount < masteredCore) {
    suggestions.push({
      id: 'quiz',
      tone: 'info',
      text: `你已掌握 ${masteredCore} 个知识点，建议做一次阶段测试巩固、校准学情。`,
      cta: '去测试',
      page: 'learning-resource',
    })
  }

  // 4) 评估 Agent 的方法建议（去重后补入，凑满信息密度）
  for (const s of evaluation.suggestions) {
    if (suggestions.length >= 4) break
    if (suggestions.some((x) => x.text === s)) continue
    suggestions.push({ id: `eval-${suggestions.length}`, tone: 'info', text: s })
  }

  // 5) 全部空态时的兜底引导（仍是真实状态：尚未诊断）
  if (suggestions.length === 0) {
    suggestions.push({
      id: 'start',
      tone: 'primary',
      text: '先完成一次画像诊断，管家才能据此持续监测并给出建议。',
      cta: '去诊断',
      page: 'profile',
    })
  }

  const activeAgents = agents.filter((a) => a.status === 'done').length

  return {
    agents,
    suggestions: suggestions.slice(0, 4),
    activeAgents,
    totalAgents: agents.length,
  }
}
