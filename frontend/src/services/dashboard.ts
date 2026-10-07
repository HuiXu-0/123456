/**
 * 学情概览数据获取层（接口文档第 12 章，接口 29）。
 * 聚合接口：综合分 / 强弱项 / 雷达 / 对标摘要等均与明细接口同口径。
 */
import { apiGet } from './api'
import { ABILITY_RADAR_DIMS, type AbilityPortrait, dimKind, type PortraitDimension } from './profileDialogue'

export interface TopicMastery {
  name: string
  mastery: number
}

/** 偏好类型标签（C2：只归类型、无分数、不上雷达轴） */
export interface PreferenceTag {
  key: string
  label: string
  value: string
  optionKey?: string | null
}

export interface DashboardOverview {
  overall_level: string
  overall_score: number
  /** 图谱覆盖率 0-1 */
  knowledge_graph_coverage: number
  learned_resources: number
  strong_topics: TopicMastery[]
  weak_topics: TopicMastery[]
  /** C2：能力雷达——各知识点掌握度（由微测/quiz 写入的 Mastery 分驱动） */
  radar: {
    dimensions: string[]
    values: number[]
  }
  /** C2：偏好类型标签（不上雷达轴） */
  preferences: PreferenceTag[]
  comparison: { betterThanPct: number }
  /** 岗位对标摘要（来自 Journey） */
  targetSummary: {
    hasDiagnosed: boolean
    targetJobName: string | null
    matchPct: number | null
  }
}

/** 12.1 获取学情概览聚合数据。 */
export function getDashboardOverview(): Promise<DashboardOverview> {
  return apiGet<DashboardOverview>('/dashboard/overview')
}

/** synthesizeOverview 产物（C2）：能力雷达 + 偏好类型标签 + 综合分/水平/强弱项。 */
export interface SynthesizedOverview {
  overall_level: string
  overall_score: number
  /** 能力雷达（各知识点掌握度，靠测） */
  radar: { dimensions: string[]; values: number[] }
  strong_topics: TopicMastery[]
  weak_topics: TopicMastery[]
  /** 偏好类型标签（无分数、不上轴） */
  preferences: PreferenceTag[]
}

/**
 * 由「真实学习画像 + 能力雷达」合成学情概览——确认弹窗与 Mock 模式首页共用，口径与后端
 * dashboard.overview 一致（C2 能力靠测、偏好归类型）：
 * - 雷达：知识点能力分（abilityRadar 来自 4.4 /profile/ability-portrait，由微测/quiz 驱动）；
 * - 优势 = 已测（>0）得分高、盲区 = 已测且 <60（未测不计强弱）；综合分 = 各能力轴均值；
 * - 偏好：从画像偏好维取类型标签（无分数、不上轴）。
 *
 * 不合成 knowledge_graph_coverage / learned_resources（属「学习活动」指标，随真实 Mastery 增长）。
 */
export function synthesizeOverview(
  dims: PortraitDimension[],
  abilityRadar?: AbilityPortrait
): SynthesizedOverview {
  const radar = abilityRadar ?? abilityFromDims(dims)
  const scored = radar.dimensions.map((name, i) => ({ name, mastery: radar.values[i] ?? 0 }))
  const tested = scored.filter((s) => s.mastery > 0)
  const strong_topics = [...tested].sort((a, b) => b.mastery - a.mastery).slice(0, 3)
  const weak_topics = tested.filter((s) => s.mastery < 60).sort((a, b) => a.mastery - b.mastery).slice(0, 3)

  const overall_score = scored.length
    ? Math.round((scored.reduce((sum, s) => sum + s.mastery, 0) / scored.length) * 10) / 10
    : 0
  const overall_level =
    overall_score >= 80 ? '高级' : overall_score >= 60 ? '中级' : overall_score >= 40 ? '入门' : '初学'

  const preferences: PreferenceTag[] = dims
    .filter((d) => dimKind(d) === 'preference')
    .map((d) => ({ key: d.key, label: d.label, value: d.value, optionKey: d.optionKey }))

  return { overall_level, overall_score, radar, strong_topics, weak_topics, preferences }
}

/**
 * abilityRadar 缺省时的兜底（Mock 模式）：以 knowledge_base 能力分均匀铺到 6 个知识点轴，
 * 构造完整能力雷达（真实联调下由 4.4 /profile/ability-portrait 给逐知识点实测分）。
 * 无能力分（未测）→ 全 0（不臆造）。
 */
function abilityFromDims(dims: PortraitDimension[]): AbilityPortrait {
  const kb = dims.find((d) => d.key === 'knowledge_base' && typeof d.score === 'number')
  const base = kb ? (kb.score as number) : 0
  return { dimensions: [...ABILITY_RADAR_DIMS], values: ABILITY_RADAR_DIMS.map(() => base) }
}
