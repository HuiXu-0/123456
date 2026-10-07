/**
 * 对话式学习画像诊断数据获取层（接口文档第 17 章，接口 37/38；C2 三段式重构）。
 *
 * - 17.1 `POST /profile/dialogue`：SSE 双模式——`data:{delta}` 逐句下发回复，
 *   `event: portrait` 下发画像维度增量，`event: interaction` 下发「引导式抛出」的
 *   微测题 / 偏好选择题（前端渲染为可点选卡片，作答经 `answer` 回传），
 *   `event: done` 终态（sessionId/suggestions/diagnosisComplete）。
 * - 17.2/17.3 `GET /profile/student-portrait`：异质画像（三分类 ability/preference/subjective）。
 * - 4.4 `GET /profile/ability-portrait`：知识点能力雷达（由微测/quiz 写入的 Mastery 分驱动）。
 *
 * C2：画像分三类，各用各的测法——**能力靠测打分、偏好归类型、主观靠对话**：
 *   - ability    可打分(0-100)+依据 basis，由诊断微测行为反推；
 *   - preference 只有类型(optionKey)、无分数，由偏好选择题归类；
 *   - subjective 描述性，对话采集。
 *
 * Mock-first：无后端（USE_REAL_API=false）时本地三段式状态机驱动全链路。
 */
import { ApiError, USE_REAL_API, apiGet, apiPut, getToken } from './api'

export type DimKind = 'ability' | 'preference' | 'subjective'

/** 17.2 PortraitDimension（亦即 17.1 portraitUpdates[] / event:portrait 的 updates[] 元素） */
export interface PortraitDimension {
  key: string
  label: string
  /** 三分类：ability 打分 / preference 归类型 / subjective 描述（旧客户端可能不带，按 key 归类） */
  kind?: DimKind
  value: string
  /** 仅 ability 维给出，0-100（偏好/主观维严禁打分） */
  score?: number
  /** ability 维「依据」：分数来自哪几道题 / 哪些作答（可解释、防臆造） */
  basis?: string
  /** preference 维类型码（visual/deepdive/concept…） */
  optionKey?: string
  /** 置信度 0-1；inferred 维度须偏低（≤0.6） */
  confidence: number
  /** 画像维来源（决定来源徽章 + 整体置信度）：
   *  - diagnostic 诊断微测实测（高）· dialogue 对话采集 · manual 手填 · inferred 推断（低）
   *  - self_report 一段话自述解析（中，自陈非实测）· default 跳过问答的零基础默认（未测） */
  source: 'dialogue' | 'manual' | 'inferred' | 'diagnostic' | 'self_report' | 'default'
  updatedAt?: string
}

/** 17.2 异质学生动态画像 */
export interface StudentPortrait {
  dimensions: PortraitDimension[]
  version: string
  updatedAt: string
}

/** 17.1 引导式交互（微测题 / 偏好选择题） */
export interface DialogueInteraction {
  id: string
  type: 'quiz' | 'preference'
  dimKey: string
  prompt: string
  options: { value: string; label: string; hint?: string }[]
  meta?: { kpId?: string; kpName?: string }
}

/** 17.1 本轮终态（event:done） */
export interface DialogueDone {
  sessionId: string
  suggestions: string[]
  diagnosisComplete: boolean
}

/** 4.4 知识点能力雷达 */
export interface AbilityPortrait {
  dimensions: string[]
  values: number[]
}

/** 能力雷达固定 6 轴（知识点，与后端 4.4 ABILITY_DIMENSIONS 同序） */
export const ABILITY_RADAR_DIMS: string[] = [
  '机器学习基础', '神经网络', '深度学习', '注意力机制', 'Transformer', '大模型微调',
]

export interface DialogueStreamOptions {
  sessionId?: string
  message: string
  /** 上一轮抛出微测/偏好题时，携带点选的稳定值（微测=option_id；偏好=optionKey） */
  answer?: string
  context?: { major?: string; goal?: string }
  onDelta: (delta: string) => void
  onPortrait: (updates: PortraitDimension[]) => void
  /** 本轮抛出的待作答交互（无则 null） */
  onInteraction: (interaction: DialogueInteraction | null) => void
}

/** 维度分类工具：按 kind（缺省按 key）归类，供面板分区渲染。 */
export const DIM_KIND_BY_KEY: Record<string, DimKind> = {
  knowledge_base: 'ability',
  prior_experience: 'subjective',
  learning_goal: 'subjective',
  cognitive_style: 'preference',
  learning_pace: 'preference',
  error_preference: 'preference',
}
export function dimKind(d: PortraitDimension): DimKind {
  return d.kind ?? DIM_KIND_BY_KEY[d.key] ?? 'subjective'
}

/** 建议维度集（17.2，key 稳定）：分三类，面板按此顺序展示「N/6」与待采集占位。 */
export const CANONICAL_DIMS: { key: string; label: string; kind: DimKind }[] = [
  { key: 'knowledge_base', label: '知识基础', kind: 'ability' },
  { key: 'cognitive_style', label: '认知风格', kind: 'preference' },
  { key: 'learning_pace', label: '学习节奏', kind: 'preference' },
  { key: 'error_preference', label: '易错点偏好', kind: 'preference' },
  { key: 'learning_goal', label: '学习目标', kind: 'subjective' },
  { key: 'prior_experience', label: '先验经验', kind: 'subjective' },
]

/* ============ 三入口统一画像产物（能力 + 偏好 + 主观，同一套 CANONICAL_DIMS） ============ */

/** 画像整体来源/置信度档位（任务 3）：三入口产出同一套结构，仅来源与置信度不同。 */
export type ProvenanceTier = 'measured' | 'self_report' | 'default' | 'manual'
export interface PortraitProvenance {
  tier: ProvenanceTier
  /** 徽章主文案，如「实测 · 高置信」 */
  label: string
  /** 副说明，便于答辩讲「如实标注可信度」 */
  hint: string
  /** 置信度配色档：high/mid/low（复用 sp-conf 配色） */
  tone: 'high' | 'mid' | 'low'
  /** 各维平均置信度（0-100） */
  confidencePct: number
}

/**
 * 由画像维度的 source/confidence **派生**整体来源与置信度（复用既有字段，不另造一套）：
 * 含 diagnostic（诊断微测实测）→ 实测·高；否则含 self_report → 自述·中；
 * 否则含 default → 未测·默认零基础；否则 manual/其它 → 自填·中。
 */
export function portraitProvenance(dims: PortraitDimension[]): PortraitProvenance {
  const sources = new Set(dims.map((d) => d.source))
  const avg = dims.length
    ? Math.round((dims.reduce((s, d) => s + (d.confidence ?? 0), 0) / dims.length) * 100)
    : 0
  if (sources.has('diagnostic'))
    return { tier: 'measured', label: '实测 · 高置信', hint: '能力由诊断微测按答题行为反推，非自陈', tone: 'high', confidencePct: avg }
  if (sources.has('self_report'))
    return { tier: 'self_report', label: '自述 · 中置信', hint: '据你的一段话描述解析推断，非实测，建议后续做题校准', tone: 'mid', confidencePct: avg }
  if (sources.has('default'))
    return { tier: 'default', label: '未测 · 默认零基础', hint: '已跳过测评，按零基础默认建立，路径将从头学起', tone: 'low', confidencePct: avg }
  if (sources.has('dialogue'))
    return { tier: 'measured', label: '对话采集 · 中置信', hint: '由对话采集主观信息，能力维待微测后升级为实测·高置信', tone: 'mid', confidencePct: avg }
  return { tier: 'manual', label: '自填 · 中置信', hint: '据你手动填写的信息建立', tone: 'mid', confidencePct: avg }
}

/** 一段话自述解析的输入（与 4.1 /profile/parse 回包结构对齐，仅取所需字段）。 */
export interface SelfReportInput {
  education?: string
  major?: string
  goal?: string
  skills: { name: string; level: number }[]
}

/**
 * 入口②「一段话描述」：把 /profile/parse 的解析结果映射为同一套 CANONICAL_DIMS 画像。
 * 能力(knowledge_base)由自述技能估分（source=self_report，中置信）；主观维同源；
 * 偏好维一段话通常未涉及 → 标 inferred 低置信（待对话/学习中校准），保持六维结构齐整。
 */
export function selfReportPortrait(p: SelfReportInput): PortraitDimension[] {
  const ts = now()
  const skills = p.skills.length ? p.skills : [{ name: '综合', level: 50 }]
  const avg = Math.round(skills.reduce((s, k) => s + k.level, 0) / skills.length)
  const weakest = skills.reduce((min, k) => (k.level < min.level ? k : min), skills[0])
  const verdict = avg >= 80 ? '扎实' : avg >= 50 ? '一般' : '薄弱'
  const label = (key: string) => CANONICAL_DIMS.find((d) => d.key === key)?.label ?? key
  return [
    { key: 'knowledge_base', label: label('knowledge_base'), kind: 'ability', value: verdict, score: avg,
      basis: `据你的自述描述解析估计（非实测）：${skills.map((k) => `${k.name}≈${k.level}`).join('、')}。`,
      confidence: 0.55, source: 'self_report', updatedAt: ts },
    { key: 'error_preference', label: label('error_preference'), kind: 'preference',
      value: `${weakest.name}偏薄弱（自述）`, confidence: 0.45, source: 'self_report', updatedAt: ts },
    { key: 'cognitive_style', label: label('cognitive_style'), kind: 'preference',
      value: '待对话/学习中校准', confidence: 0.4, source: 'inferred', updatedAt: ts },
    { key: 'learning_pace', label: label('learning_pace'), kind: 'preference',
      value: '待对话/学习中校准', confidence: 0.4, source: 'inferred', updatedAt: ts },
    { key: 'learning_goal', label: label('learning_goal'), kind: 'subjective',
      value: p.goal || '提升能力', confidence: 0.6, source: 'self_report', updatedAt: ts },
    { key: 'prior_experience', label: label('prior_experience'), kind: 'subjective',
      value: [p.education, p.major].filter(Boolean).join(' · ') || '自述背景', confidence: 0.55, source: 'self_report', updatedAt: ts },
  ]
}

/**
 * 入口③「跳过 → 零基础」：产出零基础默认画像（同一套 CANONICAL_DIMS）。
 * 能力未测（无 score，标未测）、各偏好取默认、主观零基础起步；全部 source=default 低置信。
 * 不写任何 Mastery passed → 学习路径全节点未开始、绝不出现「已完成」。
 */
export function zeroBasePortrait(): PortraitDimension[] {
  const ts = now()
  const label = (key: string) => CANONICAL_DIMS.find((d) => d.key === key)?.label ?? key
  return [
    { key: 'knowledge_base', label: label('knowledge_base'), kind: 'ability',
      value: '零基础 · 各知识点未掌握', basis: '未做诊断微测，按零基础默认起步（未测）。',
      confidence: 0.3, source: 'default', updatedAt: ts },
    { key: 'cognitive_style', label: label('cognitive_style'), kind: 'preference',
      value: '默认（待学习中校准）', confidence: 0.3, source: 'default', updatedAt: ts },
    { key: 'learning_pace', label: label('learning_pace'), kind: 'preference',
      value: '默认（待学习中校准）', confidence: 0.3, source: 'default', updatedAt: ts },
    { key: 'error_preference', label: label('error_preference'), kind: 'preference',
      value: '默认（待学习中校准）', confidence: 0.3, source: 'default', updatedAt: ts },
    { key: 'learning_goal', label: label('learning_goal'), kind: 'subjective',
      value: '零基础系统入门', confidence: 0.3, source: 'default', updatedAt: ts },
    { key: 'prior_experience', label: label('prior_experience'), kind: 'subjective',
      value: '暂无（零基础起步）', confidence: 0.3, source: 'default', updatedAt: ts },
  ]
}

/* ============ 对外统一入口（按总开关切真实 / mock） ============ */

export function profileDialogueStream(opts: DialogueStreamOptions): Promise<DialogueDone> {
  return USE_REAL_API ? realDialogue(opts) : mockDialogue(opts)
}

export function getStudentPortrait(): Promise<StudentPortrait> {
  if (USE_REAL_API) return apiGet<StudentPortrait>('/profile/student-portrait')
  return Promise.resolve({ dimensions: [], version: 'v1', updatedAt: new Date().toISOString() })
}

/** 4.4 能力雷达（知识点掌握度，由微测/quiz 驱动）。mock 据已测画像合成。 */
export function getAbilityPortrait(): Promise<AbilityPortrait> {
  if (USE_REAL_API) return apiGet<AbilityPortrait>('/profile/ability-portrait')
  return Promise.resolve(mockAbilityRadar())
}

export function saveStudentPortrait(dimensions: PortraitDimension[]): Promise<void> {
  if (!USE_REAL_API) return Promise.resolve()
  return apiPut<unknown>('/profile/student-portrait', { dimensions }).then(() => undefined)
}

/* ============ 真实后端 SSE（新增 event:interaction 处理） ============ */

async function realDialogue(opts: DialogueStreamOptions): Promise<DialogueDone> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch('/api/v1/profile/dialogue', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      sessionId: opts.sessionId,
      message: opts.message,
      answer: opts.answer,
      context: opts.context,
    }),
  })

  const ctype = res.headers.get('content-type') ?? ''
  if (!ctype.includes('text/event-stream')) {
    let env: { code?: number; message?: string } = {}
    try {
      env = await res.json()
    } catch {
      /* 非 JSON 体走 HTTP 状态兜底 */
    }
    throw new ApiError(env.code ?? res.status, env.message || `请求失败（HTTP ${res.status}）`)
  }
  if (!res.body) throw new ApiError(2001, '当前环境不支持流式读取')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let done: DialogueDone | null = null

  const handleEvent = (block: string) => {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }
    if (!dataLines.length) return
    const payload = JSON.parse(dataLines.join('\n'))
    if (event === 'done') {
      done = {
        sessionId: payload.sessionId,
        suggestions: payload.suggestions ?? [],
        diagnosisComplete: !!payload.diagnosisComplete,
      }
    } else if (event === 'portrait') {
      if (Array.isArray(payload.updates) && payload.updates.length) opts.onPortrait(payload.updates)
    } else if (event === 'interaction') {
      opts.onInteraction(payload as DialogueInteraction)
    } else if (event === 'error') {
      throw new ApiError(payload.code ?? 2001, payload.message || '画像诊断回复生成失败')
    } else if (typeof payload.delta === 'string') {
      opts.onDelta(payload.delta)
    }
  }

  try {
    for (;;) {
      const { value, done: eof } = await reader.read()
      if (eof) break
      buf += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, idx).replace(/\r/g, '')
        buf = buf.slice(idx + 2)
        if (block.trim()) handleEvent(block)
      }
    }
  } finally {
    reader.cancel().catch(() => {
      /* 流已结束时忽略 */
    })
  }

  if (!done) throw new ApiError(2001, '流式响应未正常结束')
  return done
}

/* ============ 本地 mock：三段式状态机（开场→微测→偏好），逐 token 流式 ============ */

interface MockMicroQuestion {
  kpId: string
  kpName: string
  prompt: string
  options: { value: string; label: string }[]
  correct: string
}

// 微测题（mock，由浅入深）：每题含正确答案，按作答行为反推能力（答对/答错）
const MOCK_MICROTEST: MockMicroQuestion[] = [
  {
    kpId: 'ml', kpName: '机器学习基础',
    prompt: '下列哪一项属于典型的无监督学习任务？',
    options: [
      { value: 'a', label: '根据房屋特征预测房价' },
      { value: 'b', label: '对用户按行为相似度进行聚类' },
      { value: 'c', label: '判断邮件是否为垃圾邮件' },
    ],
    correct: 'b',
  },
  {
    kpId: 'nn', kpName: '神经网络基础',
    prompt: '引入激活函数最主要的目的是？',
    options: [
      { value: 'a', label: '加快矩阵乘法运算速度' },
      { value: 'b', label: '为网络引入非线性表达能力' },
      { value: 'c', label: '减少网络参数数量' },
    ],
    correct: 'b',
  },
  {
    kpId: 'dl', kpName: '深度学习原理',
    prompt: '反向传播算法的核心数学工具是？',
    options: [
      { value: 'a', label: '贝叶斯定理' },
      { value: 'b', label: '链式法则' },
      { value: 'c', label: '傅里叶变换' },
    ],
    correct: 'b',
  },
  {
    kpId: 'transformer', kpName: 'Transformer架构',
    prompt: '自注意力机制的核心作用是？',
    options: [
      { value: 'a', label: '让每个位置都能关注序列中其他位置' },
      { value: 'b', label: '对图像做卷积下采样' },
      { value: 'c', label: '随机丢弃部分神经元' },
    ],
    correct: 'a',
  },
  {
    kpId: 'finetune', kpName: '大模型微调技术',
    prompt: 'LoRA 微调的主要优势是？',
    options: [
      { value: 'a', label: '必须更新全部参数' },
      { value: 'b', label: '只训练少量低秩矩阵、显存开销小' },
      { value: 'c', label: '完全不需要任何训练数据' },
    ],
    correct: 'b',
  },
]

const MOCK_PREFERENCE: { dimKey: string; label: string; prompt: string; options: { value: string; label: string; hint: string }[] }[] = [
  {
    dimKey: 'cognitive_style', label: '认知风格',
    prompt: '遇到一个全新的概念，你更想先看到哪一种？',
    options: [
      { value: 'visual', label: '图像型', hint: '先看一张示意图/结构图' },
      { value: 'textual', label: '文字型', hint: '先读一段准确定义' },
      { value: 'example', label: '案例型', hint: '先看一个具体例子' },
    ],
  },
  {
    dimKey: 'learning_pace', label: '学习节奏',
    prompt: '拿到一章新内容，你更倾向怎么推进？',
    options: [
      { value: 'overview', label: '快速概览型', hint: '先快速过全局再补细节' },
      { value: 'deepdive', label: '稳步细钻型', hint: '从头逐点稳稳钻透' },
    ],
  },
  {
    dimKey: 'error_preference', label: '易错点偏好',
    prompt: '回想以往做错的题，最常见的原因是哪一类？',
    options: [
      { value: 'concept', label: '概念混淆', hint: '概念记混/理解偏差' },
      { value: 'calculation', label: '计算粗心', hint: '看错条件/算错一步' },
      { value: 'coding', label: '代码卡壳', hint: '思路对但实现写不对' },
    ],
  },
]
const PREF_LABELS: Record<string, Record<string, string>> = Object.fromEntries(
  MOCK_PREFERENCE.map((p) => [p.dimKey, Object.fromEntries(p.options.map((o) => [o.value, o.label]))])
)

interface MockSession {
  stage: 'opening' | 'microtest' | 'preference' | 'done'
  openingTurns: number
  microIdx: number
  microCorrect: number
  microResults: { kpName: string; correct: boolean }[]
  prefIdx: number
  pending: DialogueInteraction | null
}
const mockSessions = new Map<string, MockSession>()
let mockSeq = 0
let mockAbility: AbilityPortrait = { dimensions: [...ABILITY_RADAR_DIMS], values: [0, 0, 0, 0, 0, 0] }
function mockAbilityRadar(): AbilityPortrait {
  return { dimensions: [...mockAbility.dimensions], values: [...mockAbility.values] }
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))
async function streamText(text: string, onDelta: (d: string) => void) {
  for (let i = 0; i < text.length; i += 2) {
    onDelta(text.slice(i, i + 2))
    await sleep(18)
  }
}
const now = () => new Date().toISOString()

function quizInteraction(idx: number): DialogueInteraction {
  const q = MOCK_MICROTEST[idx]
  return {
    id: `mock:microtest:${idx}`,
    type: 'quiz',
    dimKey: 'knowledge_base',
    prompt: q.prompt,
    options: q.options,
    meta: { kpId: q.kpId, kpName: q.kpName },
  }
}
function prefInteraction(idx: number): DialogueInteraction {
  const p = MOCK_PREFERENCE[idx]
  return { id: `mock:preference:${idx}`, type: 'preference', dimKey: p.dimKey, prompt: p.prompt, options: p.options }
}

async function mockDialogue(opts: DialogueStreamOptions): Promise<DialogueDone> {
  const sid = opts.sessionId ?? `d_mock_${++mockSeq}`
  let s = mockSessions.get(sid)
  if (!s) {
    s = { stage: 'opening', openingTurns: 0, microIdx: 0, microCorrect: 0, microResults: [], prefIdx: 0, pending: null }
    mockSessions.set(sid, s)
  }
  const ans = opts.answer ?? opts.message
  let reply = ''
  const updates: PortraitDimension[] = []
  let interaction: DialogueInteraction | null = null
  let suggestions: string[] = []

  if (s.stage === 'opening') {
    const key = s.openingTurns === 0 ? 'prior_experience' : 'learning_goal'
    const label = key === 'prior_experience' ? '先验经验' : '学习目标'
    const v = (opts.message || '').trim()
    updates.push({ key, label, kind: 'subjective', value: v && v.length <= 24 ? v : key === 'prior_experience' ? '有相关经验' : '提升能力', confidence: 0.55, source: 'dialogue', updatedAt: now() })
    s.openingTurns += 1
    if (s.openingTurns >= 2) {
      s.stage = 'microtest'
      interaction = quizInteraction(0)
      s.pending = interaction
      reply = '好的，主观情况我了解了。下面做几道小题快速「测」一下你的真实基础——凭直觉选就行。'
      reply += `\n关于「${MOCK_MICROTEST[0].kpName}」：${MOCK_MICROTEST[0].prompt}`
    } else {
      reply = '了解了。那这次学习你最想达成的目标是什么？'
      suggestions = ['转岗 / 求职', '考试 / 认证', '兴趣自学']
    }
  } else if (s.stage === 'microtest') {
    const q = MOCK_MICROTEST[s.microIdx]
    const correct = ans === q.correct
    s.microResults.push({ kpName: q.kpName, correct })
    if (correct) s.microCorrect += 1
    s.microIdx += 1
    if (s.microIdx < MOCK_MICROTEST.length) {
      const nq = MOCK_MICROTEST[s.microIdx]
      interaction = quizInteraction(s.microIdx)
      s.pending = interaction
      reply = `好，记下了。下面这道关于「${nq.kpName}」的题，你的判断是？\n${nq.prompt}`
    } else {
      // 微测结束：据作答反推能力 + 合成能力雷达
      const total = s.microResults.length
      const score = Math.round((s.microCorrect / total) * 100)
      const verdict = score >= 80 ? '扎实' : score >= 50 ? '一般' : '薄弱'
      const correctKps = s.microResults.filter((r) => r.correct).map((r) => r.kpName)
      const wrongKps = s.microResults.filter((r) => !r.correct).map((r) => r.kpName)
      const basis = `诊断微测答对 ${s.microCorrect}/${total} 题` +
        (correctKps.length ? `；答对：${correctKps.join('、')}` : '') +
        (wrongKps.length ? `；答错：${wrongKps.join('、')}` : '') + '。'
      updates.push({ key: 'knowledge_base', label: '知识基础', kind: 'ability', value: verdict, score, basis, confidence: Math.min(0.7, 0.4 + 0.06 * total), source: 'diagnostic', updatedAt: now() })
      // mock 能力雷达：答对的知识点轴高、答错的低
      mockAbility = {
        dimensions: [...ABILITY_RADAR_DIMS],
        values: ABILITY_RADAR_DIMS.map((name) => {
          const r = s!.microResults.find((x) => x.kpName.includes(name.slice(0, 3)) || name.includes(x.kpName.slice(0, 3)))
          return r ? (r.correct ? 78 : 32) : score
        }),
      }
      s.stage = 'preference'
      interaction = prefInteraction(0)
      s.pending = interaction
      reply = `速测完成 ✦，能力画像已据作答测出（${verdict}）。最后了解一下学习偏好（只归类型、不打分）——\n${MOCK_PREFERENCE[0].prompt}`
    }
  } else if (s.stage === 'preference') {
    const p = MOCK_PREFERENCE[s.prefIdx]
    const optionKey = p.options.some((o) => o.value === ans) ? ans : p.options[0].value
    updates.push({ key: p.dimKey, label: p.label, kind: 'preference', value: PREF_LABELS[p.dimKey][optionKey], optionKey, confidence: 0.9, source: 'dialogue', updatedAt: now() })
    s.prefIdx += 1
    if (s.prefIdx < MOCK_PREFERENCE.length) {
      const np = MOCK_PREFERENCE[s.prefIdx]
      interaction = prefInteraction(s.prefIdx)
      s.pending = interaction
      reply = `好的。${np.prompt}`
    } else {
      s.stage = 'done'
      reply = '都记下了 🎉 能力靠测、偏好归类型、主观靠对话，你的动态学习画像已构建完整，可据此生成个性化学习路径了。'
      suggestions = ['生成学习路径']
    }
  } else {
    reply = '你的画像已构建完整，随时可以生成学习路径。'
    suggestions = ['生成学习路径']
  }

  await streamText(reply, opts.onDelta)
  if (updates.length) {
    await sleep(120)
    opts.onPortrait(updates)
  }
  opts.onInteraction(interaction)
  return { sessionId: sid, suggestions, diagnosisComplete: s.stage === 'done' }
}
