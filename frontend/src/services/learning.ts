/**
 * 学习路径数据获取层（接口文档第 6 章 + 异步任务 1.4 / 15.2）。
 * 生成路径为耗时操作：提交后拿 taskId，轮询 GET /tasks/{taskId} 至终态。
 *
 * C2：路径**真由后端 planner 按画像规划**（节点/顺序/每步推荐资源因画像而变）；
 * Mock 模式（无后端）下本地依据画像 store + 掌握度 store 合成等价的个性化路径，
 * 保证「无后端也跑通且因画像而变」。
 */
import { USE_REAL_API, apiGet, apiPost } from './api'
import { KNOWLEDGE_POINTS } from '../data/knowledgePoints'
import { KS_POINTS, LEVEL_ORDER, type KsPoint } from '../data/knowledgeSystem'
import { usePortrait } from '../store/portrait'
import { useMastery } from '../store/mastery'
import { getAbilityPortrait } from './profileDialogue'

/** 路径节点配套资源（6.3 additive）：指向该 kpId 真实资源端点，首项为按偏好默认推荐 */
export interface PathResource {
  kind: string
  title: string
  kpId: string
  endpoint: string
  difficulty?: string
  recommended?: boolean
  recommendReason?: string
}

export interface Lesson {
  sequence: number
  topic: string
  difficulty: string
  status: 'completed' | 'in_progress' | 'pending'
  progress: number
  description: string
  /** 6.3 additive（个性化路径）：知识点 id / 排程理由 / 配套资源（首项按偏好推荐） */
  kpId?: string
  reason?: string
  resources?: PathResource[]
  /** 会话三 additive（时间线）：该步预计时长（分钟，按层级/难度估算） */
  estimatedMinutes?: number
}

/** 会话三 additive：整条路径时间线聚合（预计周期/总时长/进度/节奏建议）。 */
export interface PathTimeline {
  totalCount: number
  learnedCount: number
  totalMinutes: number
  totalHours: number
  spentMinutes: number
  completionPct: number
  pacePerDay: number
  cycleDays: number
  cycleWeeks: number
  pacing: string
}

export interface LearningPathData {
  lessons: Lesson[]
  milestones: { id: number; title: string; completed: boolean; date: string | null }[]
  summary: { completedCount: number; inProgressCount: number; overallProgress: number } & {
    /** C2：整体规划叙述（"为你这样规划的理由"，由 planner 产出） */
    narrative?: string
    /** 会话三：时间线聚合（预计周期/总时长/进度百分比/节奏建议） */
    timeline?: PathTimeline
  }
}

/** 6.1 获取个性化学习路径（真后端 / mock 本地合成）。 */
export function getLearningPath(): Promise<LearningPathData> {
  return USE_REAL_API ? apiGet<LearningPathData>('/learning-path') : mockLearningPath()
}

interface TaskState<R> {
  taskId: string
  status: 'pending' | 'running' | 'succeeded' | 'failed'
  progress?: number
  result?: R
  error?: { code: number; message: string } | null
}

/** 轮询异步任务至终态（接口文档 15.2）。succeeded 返回 result，failed/超时抛错。 */
export async function pollTask<R = unknown>(
  taskId: string,
  { intervalMs = 600, timeoutMs = 30000 }: { intervalMs?: number; timeoutMs?: number } = {}
): Promise<R | undefined> {
  const start = Date.now()
  for (;;) {
    const t = await apiGet<TaskState<R>>(`/tasks/${taskId}`)
    if (t.status === 'succeeded') return t.result
    if (t.status === 'failed') throw new Error(t.error?.message ?? '任务执行失败')
    if (Date.now() - start > timeoutMs) throw new Error('任务轮询超时')
    await new Promise((resolve) => setTimeout(resolve, intervalMs))
  }
}

/** 6.2 生成 / 重新规划学习路径：提交任务并轮询至完成（mock 无后端则空操作）。 */
export async function generatePath(targetJobId?: string): Promise<void> {
  if (!USE_REAL_API) return
  const { taskId } = await apiPost<{ taskId: string }>(
    '/learning-path/generate',
    targetJobId ? { targetJobId } : {}
  )
  await pollTask(taskId)
}

/* ============ 本地 mock 个性化规划（镜像后端 planner，因画像而变） ============ */

const _PROFICIENT = 70
const _DIFF_LADDER = ['入门', '初级', '中级', '高级', '精通']
// 各知识点基础难度（与后端种子 Lesson.difficulty 同序）
const _BASE_DIFF: Record<string, string> = {
  ml: '入门', nn: '初级', dl: '中级', cnn: '中级', transformer: '高级', finetune: '精通',
}
const _STYLE_PRIMARY: Record<string, string> = { visual: 'diagram', textual: 'lecture', example: 'external' }
const _PACE_PRIMARY: Record<string, string> = { overview: 'mindmap', deepdive: 'lecture' }
const _RESOURCE_SPECS: { kind: string; title: string; endpoint: string; diff?: boolean }[] = [
  { kind: 'lecture', title: '《{t}》自适应讲义', endpoint: '/resource/lecture', diff: true },
  { kind: 'mindmap', title: '{t} · 思维导图', endpoint: '/resource/mindmap' },
  { kind: 'diagram', title: '{t} · 知识图解', endpoint: '/resource/diagram' },
  { kind: 'video', title: '{t} · 讲解视频', endpoint: '/resource/video', diff: true },
  { kind: 'quiz', title: '{t} · 巩固测验', endpoint: '/quiz' },
  { kind: 'external', title: '{t} · 精选外部资源', endpoint: '/resource/external' },
]
const _KIND_LABEL: Record<string, string> = {
  lecture: '自适应讲义', mindmap: '思维导图', diagram: '知识图解',
  video: '讲解视频', quiz: '巩固测验', external: '精选外部资源',
}
const _STYLE_LABEL: Record<string, string> = { visual: '图像型', textual: '文字型', example: '案例型' }
const _PACE_LABEL: Record<string, string> = { overview: '快速概览型', deepdive: '稳步细钻型' }

/* ===== 会话三：在 78 点树上按目标聚焦裁剪 + 时间线估算（镜像后端 planner_agent） ===== */
const _LEVEL_BASE_MIN: Record<string, number> = { 入门: 60, 进阶: 120, 前沿: 180 }
const _DIFF_DELTA_MIN: Record<string, number> = { 入门: -15, 初级: 0, 中级: 15, 高级: 30, 精通: 45 }
const _LEVEL_TO_DIFF: Record<string, string> = { 入门: '入门', 进阶: '中级', 前沿: '高级' }
const _CORE_SEQ: Record<string, number> = { ml: 1, nn: 2, dl: 3, cnn: 4, transformer: 5, finetune: 6 }
const _MAX_STEPS = 12
const _BOARD_KEYWORDS: [string, string[]][] = [
  ['LLM', ['大模型', 'llm', '语言模型', 'nlp', '自然语言', 'gpt', 'chatgpt', '文本', 'prompt', '提示词', 'rag']],
  ['GEN', ['生成式', '生成模型', '扩散', 'diffusion', 'aigc', '文生图', '绘画', 'stable diffusion', 'sora', '图像生成']],
  ['AGT', ['智能体', 'agent', '多智能体', 'langchain', '工作流编排', 'autogpt', 'autogen']],
  ['CV', ['计算机视觉', '视觉', '图像识别', '目标检测', '图像分割', 'opencv', 'yolo']],
  ['RLX', ['强化学习', 'reinforcement', '决策', '博弈', '机器人控制']],
  ['DL', ['深度学习', '神经网络', 'deep learning', 'cnn', 'rnn', '卷积']],
  ['ML', ['机器学习', '数据分析', '数据挖掘', '特征工程', '回归', '聚类', '数据科学']],
]

function _estimateMinutes(level: string, difficulty: string): number {
  const base = _LEVEL_BASE_MIN[level] ?? 120
  const delta = _DIFF_DELTA_MIN[difficulty] ?? 0
  const raw = Math.max(45, Math.min(240, base + delta))
  return Math.round(raw / 15) * 15
}

function _focusBoards(goal: string, job: string): string[] {
  const hay = `${goal} ${job}`.toLowerCase()
  const out: string[] = []
  for (const [board, kws] of _BOARD_KEYWORDS) {
    if (out.includes(board)) continue
    if (kws.some((k) => hay.includes(k.toLowerCase()))) out.push(board)
    if (out.length >= 2) break
  }
  return out
}

/** 先修链最长深度（拓扑序骨架）：目录点无 lessonSeq，用先修深度排「由浅入深」。 */
function _topoRank(): Record<string, number> {
  const byId: Record<string, KsPoint> = Object.fromEntries(KS_POINTS.map((p) => [p.id, p]))
  const memo: Record<string, number> = {}
  const depth = (id: string, seen: Set<string>): number => {
    if (memo[id] != null) return memo[id]
    const p = byId[id]
    if (!p || !p.prerequisites.length || seen.has(id)) return (memo[id] = 0)
    seen.add(id)
    const d = 1 + Math.max(0, ...p.prerequisites.map((pr) => (byId[pr] ? depth(pr, seen) : 0)))
    seen.delete(id)
    return (memo[id] = d)
  }
  KS_POINTS.forEach((p) => depth(p.id, new Set()))
  return memo
}
const _RANK = _topoRank()
const _IDX: Record<string, number> = Object.fromEntries(KS_POINTS.map((p, i) => [p.id, i]))
/** 排序骨架序：核心 1-6；目录点 1000 + 先修深度 + 声明序微偏（先修在前、依赖在后）。 */
const _seq = (id: string): number =>
  _CORE_SEQ[id] ?? 1000 + (_RANK[id] ?? 0) * 10 + (_IDX[id] ?? 0) * 0.01

function _closure(ids: Set<string>, byId: Record<string, KsPoint>): Set<string> {
  const closed = new Set(ids)
  const stack = [...ids]
  while (stack.length) {
    const cur = stack.pop() as string
    for (const pre of byId[cur]?.prerequisites ?? []) {
      if (byId[pre] && !closed.has(pre)) {
        closed.add(pre)
        stack.push(pre)
      }
    }
  }
  return closed
}

/** 据目标聚焦选出参与规划的知识点：无目标 → 6 核心；有目标 → 6 核心 + 板块细化点（裁剪 ≤12）。 */
function _selectPoints(focus: string[]): KsPoint[] {
  const byId: Record<string, KsPoint> = Object.fromEntries(KS_POINTS.map((p) => [p.id, p]))
  const coreIds = new Set(KS_POINTS.filter((p) => p.isCore).map((p) => p.id))
  if (!focus.length) return KS_POINTS.filter((p) => p.isCore)
  const inFocus = new Set(KS_POINTS.filter((p) => focus.includes(p.category)).map((p) => p.id))
  const closed = _closure(new Set([...coreIds, ...inFocus]), byId)
  const ext = [...closed].filter((i) => !coreIds.has(i))
  ext.sort(
    (a, b) =>
      (inFocus.has(a) ? 0 : 1) - (inFocus.has(b) ? 0 : 1) ||
      (LEVEL_ORDER[byId[a].level] - LEVEL_ORDER[byId[b].level]) ||
      _seq(a) - _seq(b)
  )
  const keep = new Set(ext.slice(0, _MAX_STEPS - coreIds.size))
  return KS_POINTS.filter((p) => coreIds.has(p.id) || keep.has(p.id))
}

function _buildTimeline(lessons: Lesson[], pace: string): PathTimeline {
  const totalCount = lessons.length
  const totalMinutes = lessons.reduce((s, l) => s + (l.estimatedMinutes ?? 0), 0)
  const learnedCount = lessons.filter((l) => l.status === 'completed').length
  const spentMinutes = Math.round(
    lessons.reduce((s, l) => s + (l.estimatedMinutes ?? 0) * (l.progress / 100), 0)
  )
  const completionPct = totalMinutes ? Math.round((spentMinutes / totalMinutes) * 100) : 0
  const pacePerDay = pace === 'overview' ? 2 : 1
  const cycleDays = totalCount ? Math.max(1, Math.ceil(totalCount / pacePerDay)) : 0
  const cycleWeeks = cycleDays ? Math.max(1, Math.ceil(cycleDays / 7)) : 0
  const perLabel = pacePerDay >= 2 ? '1-2' : '1'
  const pacing = totalCount
    ? `建议每天 ${perLabel} 个知识点，预计 ${cycleWeeks} 周（约 ${cycleDays} 天）学完。`
    : '完成学情诊断后即可生成个性化学习节奏建议。'
  return {
    totalCount,
    learnedCount,
    totalMinutes,
    totalHours: Math.round((totalMinutes / 60) * 10) / 10,
    spentMinutes,
    completionPct,
    pacePerDay,
    cycleDays,
    cycleWeeks,
    pacing,
  }
}

const _ksLevel = (id: string): string => KS_POINTS.find((p) => p.id === id)?.level ?? '入门'

function _adaptDifficulty(base: string, foundation: number | null): string {
  let idx = _DIFF_LADDER.indexOf(base)
  if (idx < 0) idx = 1
  if (foundation != null) {
    if (foundation >= 75) idx = Math.min(_DIFF_LADDER.length - 1, idx + 1)
    else if (foundation < 40) idx = Math.max(0, idx - 1)
  }
  return _DIFF_LADDER[idx]
}

function _lectureDiff(d: string): string {
  return ({ 入门: '入门', 初级: '初级', 中级: '初级', 高级: '高级', 精通: '高级' } as Record<string, string>)[d] ?? '初级'
}

function _prefKindOrder(style: string, pace: string): string[] {
  const order: string[] = []
  if (_STYLE_PRIMARY[style]) order.push(_STYLE_PRIMARY[style])
  if (_PACE_PRIMARY[pace] && !order.includes(_PACE_PRIMARY[pace])) order.push(_PACE_PRIMARY[pace])
  for (const s of _RESOURCE_SPECS) if (!order.includes(s.kind)) order.push(s.kind)
  return order
}

function _recommendReason(kind: string, style: string, pace: string): string {
  const label = _KIND_LABEL[kind] ?? kind
  if (_STYLE_LABEL[style] && _STYLE_PRIMARY[style] === kind) return `你是${_STYLE_LABEL[style]}学习者，优先推「${label}」建立直观理解。`
  if (_PACE_LABEL[pace] && _PACE_PRIMARY[pace] === kind) return `你偏${_PACE_LABEL[pace]}，优先用「${label}」${pace === 'overview' ? '先鸟瞰全局再补细节' : '逐点稳稳钻透'}。`
  return `默认从「${label}」入手。`
}

function _buildResources(kpId: string, topic: string, difficulty: string, style: string, pace: string): PathResource[] {
  const lec = _lectureDiff(difficulty)
  const order = style || pace ? _prefKindOrder(style, pace) : _RESOURCE_SPECS.map((s) => s.kind)
  const specByKind = Object.fromEntries(_RESOURCE_SPECS.map((s) => [s.kind, s]))
  const hasPref = !!(style || pace)
  return order.map((kind, idx) => {
    const spec = specByKind[kind]
    const item: PathResource = { kind, title: spec.title.replace('{t}', topic), kpId, endpoint: spec.endpoint }
    if (spec.diff) item.difficulty = lec
    if (hasPref && idx === 0) { item.recommended = true; item.recommendReason = _recommendReason(kind, style, pace) }
    return item
  })
}

async function mockLearningPath(): Promise<LearningPathData> {
  const dims = usePortrait.getState().dims
  const status = useMastery.getState().status
  const diagnosed = dims.length > 0
  if (!diagnosed) {
    // 未诊断 → 种子静态 6 课（与后端 GET 未诊断回落同义）
    const seed: Lesson[] = KNOWLEDGE_POINTS.map((k) => ({
      sequence: k.lessonSeq, topic: k.name, difficulty: _BASE_DIFF[k.id], status: 'pending' as const,
      progress: 0, description: '', kpId: k.id,
      estimatedMinutes: _estimateMinutes(_ksLevel(k.id), _BASE_DIFF[k.id]),
    }))
    return {
      lessons: seed,
      milestones: _milestones(false, false, seed),
      summary: { ..._summary(seed), narrative: '', timeline: _buildTimeline(seed, '') },
    }
  }

  const ability = await getAbilityPortrait() // mock 按上次微测合成的 per-KP 能力分（6 核心）
  const scoreByKp: Record<string, number> = {}
  KNOWLEDGE_POINTS.forEach((k, i) => (scoreByKp[k.id] = ability.values[i] ?? 0))
  const kb = dims.find((d) => d.key === 'knowledge_base')?.score
  const foundation = typeof kb === 'number' ? kb : null
  const style = dims.find((d) => d.key === 'cognitive_style')?.optionKey ?? ''
  const pace = dims.find((d) => d.key === 'learning_pace')?.optionKey ?? ''
  const goal = dims.find((d) => d.key === 'learning_goal')?.value ?? ''

  // 会话三：按学习目标在 78 点树上聚焦板块细化（无目标 → 6 核心主干，与既有一致）
  const pool = _selectPoints(_focusBoards(goal, ''))

  const scored = pool.map((p) => {
    const st = status[p.id]
    const mastered = st === 'passed'
    const kpScore = scoreByKp[p.id] ?? 0            // 目录点未测 → 0（薄弱、优先补）
    const proficient = mastered || kpScore >= _PROFICIENT
    const seq = _seq(p.id)
    const foundational = seq <= 2                    // 仅 ml/nn（后续基础）
    let score = seq
    if (proficient) score += 100
    else {
      score += (kpScore / 100) * 0.8
      if (foundational && foundation != null) score += foundation >= 75 ? 0.4 : foundation < 40 ? -0.4 : 0
    }
    return { p, st, mastered, proficient, kpScore, foundational, score }
  }).sort((a, b) => a.score - b.score || _seq(a.p.id) - _seq(b.p.id))

  let weakCount = 0
  let proficientCount = 0
  const lessons: Lesson[] = scored.map((e, i) => {
    const baseDiff = _BASE_DIFF[e.p.id] ?? _LEVEL_TO_DIFF[e.p.level] ?? '初级'
    const difficulty = _adaptDifficulty(baseDiff, foundation)
    const order = i + 1
    // 「已完成」只来自真实通过测验（passed）；「进行中」只来自真实学习行为（mastery 状态）。
    // 微测能力分（kpScore）仅用于排序/薄弱判定，**绝不**让节点显示已完成/进行中（微测≠学完）。
    let status_: Lesson['status'] = 'pending'
    let progress = 0
    if (e.mastered) { status_ = 'completed'; progress = 100 }
    else if (e.st === 'learning' || e.st === 'pending-check') { status_ = 'in_progress'; progress = e.st === 'pending-check' ? 70 : 40 }
    const ab = e.kpScore > 0 ? `（微测能力分 ${e.kpScore}）` : ''
    let reason = ''
    if (e.mastered) { proficientCount++; reason = `你已掌握「${e.p.name}」（测验通过），后置到第 ${order} 步用于巩固复习，可快速跳过。` }
    else if (e.proficient) { proficientCount++; reason = `微测显示你对「${e.p.name}」已基本达标${ab}，后置到第 ${order} 步快速复习、可略读。` }
    else if (e.foundational) { weakCount++; reason = `「${e.p.name}」是后续内容的基础且你尚未掌握${ab}，优先安排在第 ${order} 步打牢根基。` }
    else { weakCount++; reason = `「${e.p.name}」是你的薄弱点${ab}，按先修顺序安排在第 ${order} 步集中攻克。` }
    return {
      sequence: order, topic: e.p.name, difficulty, status: status_, progress,
      description: e.p.description || '', kpId: e.p.id, reason,
      estimatedMinutes: _estimateMinutes(e.p.level, difficulty),
      resources: _buildResources(e.p.id, e.p.name, difficulty, style, pace),
    }
  })

  const level = foundation == null ? '中等' : foundation >= 75 ? '扎实' : foundation < 40 ? '薄弱' : '一般'
  const tag = [_STYLE_LABEL[style], _PACE_LABEL[pace]].filter(Boolean).join('·')
  const how = tag ? `；并按你的学习偏好（${tag}）为每步默认推荐最适合的资源形式` : ''
  const narrative = `本路径依据你的画像（基础${level}${goal ? `、目标「${goal}」` : ''}）与各知识点实测能力规划：` +
    `将 ${weakCount} 个薄弱点按先修顺序前置、${proficientCount} 个已达标点后置复习，共 ${lessons.length} 步${how}。`

  return {
    lessons,
    milestones: _milestones(true, true, lessons),
    summary: { ..._summary(lessons), narrative, timeline: _buildTimeline(lessons, pace) },
  }
}

function _summary(lessons: Lesson[]) {
  const completed = lessons.filter((l) => l.status === 'completed').length
  const inProgress = lessons.filter((l) => l.status === 'in_progress').length
  const overall = lessons.length ? Math.round(lessons.reduce((s, l) => s + l.progress, 0) / lessons.length) : 0
  return { completedCount: completed, inProgressCount: inProgress, overallProgress: overall }
}

function _milestones(diagnosed: boolean, generated: boolean, lessons: Lesson[]) {
  const completed = lessons.filter((l) => l.status === 'completed').length
  const allDone = lessons.length > 0 && completed === lessons.length
  return [
    { id: 1, title: '完成画像诊断', completed: diagnosed, date: diagnosed ? '2026-05-20' : null },
    { id: 2, title: '生成学习路径', completed: generated, date: generated ? '2026-05-22' : null },
    { id: 3, title: '完成基础课程', completed: completed >= 2, date: completed >= 2 ? '2026-05-28' : null },
    { id: 4, title: '掌握核心架构', completed: allDone, date: null },
  ]
}
