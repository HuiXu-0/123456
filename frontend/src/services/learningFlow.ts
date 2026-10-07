/**
 * 学习流程（费曼 + 康奈尔）数据获取层（接口文档第 18 章，接口 39–44）。
 *
 * - 18.1 POST /learning/cornell-cues   康奈尔线索 + 主笔记骨架 + 总结引导
 * - 18.2 POST /learning/feynman        费曼讲解评估（SSE/JSON 双模式，SSE 同 15.4/17.1 体例）
 * - 18.3 GET/POST /learning/steps/{kp} 6 步过程进度（video|lecture|diagram|note|feynman|practice）
 * - 18.4 GET  /learning/notes/{kp}     读取康奈尔笔记
 * - 18.5 PUT  /learning/notes/{kp}     整体覆盖保存（前端防抖调用）
 *
 * Mock-first：USE_REAL_API=false 时返回与知识点相关的确定性假数据，并用模块内存
 * （noteStore / stepStore）维持「保存→回读一致」，无后端即可走通整条有序流。
 */
import { apiGet, apiPost, apiRequest, getToken } from './api'
import { USE_REAL_API } from './api'
import { kpById } from '../data/knowledgePoints'
import { ksPointById } from '../data/knowledgeSystem'
import type { KPStatus } from '../store/mastery'

/* ---------------- 类型（严格对齐 18 章契约字段名） ---------------- */

export interface CornellCue {
  id: string
  type: 'question' | 'keyword'
  text: string
}

export interface NoteOutline {
  id: string
  cueId: string | null
  heading: string
  points: string[]
}

export interface CornellCues {
  kpId: string
  difficulty: string
  cues: CornellCue[]
  noteOutline: NoteOutline[]
  summaryHint: string
  sources: { title: string; type: string; confidence: number }[]
}

export interface CueNote {
  cueId: string
  text: string
}

export interface CornellNote {
  kpId: string
  cueNotes: CueNote[]
  mainNotes: string
  summary: string
  updatedAt: string
}

export type StepKey = 'video' | 'lecture' | 'diagram' | 'note' | 'feynman' | 'practice'
export const STEP_KEYS: StepKey[] = ['video', 'lecture', 'diagram', 'note', 'feynman', 'practice']

export interface StepProgress {
  kpId: string
  steps: Record<string, boolean>
  mastery: KPStatus
  completedCount: number
  total: number
  updatedAt: string
}

export interface ReviewRef {
  kind: string
  title: string
  kpId: string
  endpoint: string
  difficulty?: string
}

export interface FeynmanGap {
  id: string
  kpId: string
  title: string
  detail: string
  severity: 'high' | 'medium' | 'low'
  review?: ReviewRef[]
}

export interface FeynmanDone {
  sessionId: string
  score: number | null
  followups: string[]
  complete: boolean
}

/* ---------------- 18.1 康奈尔线索生成 ---------------- */

/** nn（神经网络基础）的确定性 mock 线索/骨架；其余知识点用通用骨架按名称填充。 */
function mockCues(kpId: string, difficulty: string): CornellCues {
  const name = kpById(kpId)?.name ?? ksPointById(kpId)?.name ?? '当前知识点'
  if (kpId === 'nn') {
    return {
      kpId,
      difficulty,
      cues: [
        { id: 'c1', type: 'question', text: '为什么神经网络需要激活函数？' },
        { id: 'c2', type: 'question', text: '前向传播与反向传播分别解决什么问题？' },
        { id: 'c3', type: 'keyword', text: '加权求和 → 偏置 → 激活' },
        { id: 'c4', type: 'question', text: '梯度下降是如何更新权重的？' },
        { id: 'c5', type: 'keyword', text: 'ReLU / Sigmoid / Tanh' },
      ],
      noteOutline: [
        {
          id: 'n1',
          cueId: 'c1',
          heading: '激活函数的作用',
          points: ['引入非线性表达能力', '若无激活，多层网络等价于单层线性变换'],
        },
        {
          id: 'n2',
          cueId: 'c2',
          heading: '前向 / 反向传播',
          points: ['前向：输入逐层加权激活得到输出', '反向：链式法则回传误差，算出各权重梯度'],
        },
        {
          id: 'n3',
          cueId: 'c3',
          heading: '神经元三步运算',
          points: ['加权求和 Σ w·x', '加偏置 +b', '经激活函数输出'],
        },
      ],
      summaryHint: '用一句话概括：神经网络如何通过逐层加权与非线性激活拟合复杂函数？',
      sources: [
        { title: '《深度学习》花书 第 6 章', type: '教材', confidence: 0.9 },
        { title: 'CS231n: Neural Networks（Stanford）', type: '课程', confidence: 0.88 },
      ],
    }
  }
  return {
    kpId,
    difficulty,
    cues: [
      { id: 'c1', type: 'question', text: `「${name}」要解决的核心问题是什么？` },
      { id: 'c2', type: 'question', text: `「${name}」的关键步骤/组成有哪些？` },
      { id: 'c3', type: 'keyword', text: `${name} · 核心概念` },
      { id: 'c4', type: 'question', text: `它与上一个知识点是如何衔接的？` },
    ],
    noteOutline: [
      { id: 'n1', cueId: 'c1', heading: `${name} 概述`, points: ['它解决的问题', '为什么重要'] },
      { id: 'n2', cueId: 'c2', heading: '关键步骤', points: ['步骤一', '步骤二'] },
    ],
    summaryHint: `用一句话概括「${name}」的核心思想。`,
    sources: [{ title: 'RAG 知识库检索来源', type: '文档', confidence: 0.86 }],
  }
}

/** 18.1 生成康奈尔线索 + 主笔记骨架 + 总结引导。 */
export async function getCornellCues(kpId: string, difficulty = '初级'): Promise<CornellCues> {
  if (!USE_REAL_API) return mockCues(kpId, difficulty)
  return apiPost<CornellCues>('/learning/cornell-cues', { kpId, difficulty })
}

/* ---------------- 18.4 / 18.5 康奈尔笔记（mock 用内存维持回读一致） ---------------- */

const noteStore = new Map<string, CornellNote>()
const nowIso = () => new Date().toISOString()

/** 18.4 读取康奈尔笔记；无则返回空笔记（不视为错误，同契约口径）。 */
export async function getCornellNote(kpId: string): Promise<CornellNote> {
  if (!USE_REAL_API) {
    return (
      noteStore.get(kpId) ?? {
        kpId,
        cueNotes: [],
        mainNotes: '',
        summary: '',
        updatedAt: nowIso(),
      }
    )
  }
  return apiGet<CornellNote>(`/learning/notes/${kpId}`)
}

/** 18.5 整体覆盖保存康奈尔笔记（前端防抖调用），返回盖戳后的笔记。 */
export async function saveCornellNote(
  kpId: string,
  note: { cueNotes: CueNote[]; mainNotes: string; summary: string }
): Promise<CornellNote> {
  if (!USE_REAL_API) {
    const saved: CornellNote = { kpId, ...note, updatedAt: nowIso() }
    noteStore.set(kpId, saved)
    return saved
  }
  return apiRequest<CornellNote>(`/learning/notes/${kpId}`, { method: 'PUT', body: note })
}

/* ---------------- 18.3 学习步骤进度（mock 用内存维持） ---------------- */

const stepStore = new Map<string, Record<string, boolean>>()

function buildProgress(kpId: string, steps: Record<string, boolean>, mastery: KPStatus): StepProgress {
  const completedCount = STEP_KEYS.filter((k) => steps[k]).length
  return { kpId, steps, mastery, completedCount, total: STEP_KEYS.length, updatedAt: nowIso() }
}

/** 18.3 读取某知识点 6 步过程进度（mastery 透传，仅作展示；「已掌握」以 7.1 为准）。 */
export async function getSteps(kpId: string): Promise<StepProgress> {
  if (!USE_REAL_API) {
    const steps = stepStore.get(kpId) ?? {}
    return buildProgress(kpId, steps, 'learning')
  }
  return apiGet<StepProgress>(`/learning/steps/${kpId}`)
}

/** 18.3 标记某步骤完成 / 取消（幂等），返回刷新后的整体进度。 */
export async function markStep(kpId: string, step: StepKey, done: boolean): Promise<StepProgress> {
  if (!USE_REAL_API) {
    const steps = { ...(stepStore.get(kpId) ?? {}), [step]: done }
    stepStore.set(kpId, steps)
    return buildProgress(kpId, steps, 'learning')
  }
  return apiPost<StepProgress>(`/learning/steps/${kpId}`, { step, done })
}

/* ---------------- 18.2 费曼讲解评估（SSE / mock 流式） ---------------- */

export interface FeynmanStreamOptions {
  kpId: string
  /** 多轮上下文，首轮可空（由后端 done 事件带回）。 */
  sessionId?: string
  /** 学生本轮「讲解」文本。 */
  explanation: string
  /** 逐 delta 点评 / 追问片段。 */
  onDelta: (delta: string) => void
  /** 本轮识别出的知识缺口（通常末尾下发一次）。 */
  onGaps: (gaps: FeynmanGap[]) => void
}

/** mock：每个 sessionId 的轮次计数，用于第二轮起减少缺口并判定 complete（避免重复点评）。 */
const mockFeynmanTurn = new Map<string, number>()
const delay = (ms: number) => new Promise((r) => window.setTimeout(r, ms))

/** 费曼评估的概念检查项：pattern 命中视为「讲到了」，未命中生成对应知识缺口。 */
interface FeynmanCheck {
  /** 概念名（点评文案中引用） */
  concept: string
  pattern: RegExp
  gapTitle: string
  gapDetail: string
  severity: 'high' | 'medium'
  /** 缺口回看资源形态（标题按 kpName 生成） */
  reviewKinds: ('lecture' | 'diagram' | 'video')[]
  /** 该概念未讲到时的追问 */
  followup: string
}

/** 6 核心点各自的讲解主线（anchor，点评起手肯定语）与关键概念清单；体系点走 genericFeynmanSpec。 */
const FEYNMAN_SPECS: Record<string, { anchor: string; checks: FeynmanCheck[] }> = {
  nn: {
    anchor: '加权求和',
    checks: [
      {
        concept: '激活函数',
        pattern: /激活|非线性|relu|sigmoid|tanh/i,
        gapTitle: '遗漏激活函数（非线性）',
        gapDetail: '讲解中只到加权求和，未提到激活函数，而非线性正是神经网络区别于线性模型的核心。',
        severity: 'high',
        reviewKinds: ['lecture', 'diagram'],
        followup: '如果去掉激活函数，三层网络和一层有什么区别？',
      },
      {
        concept: '反向传播（网络如何学习）',
        pattern: /反向|梯度|backprop|损失|下降/i,
        gapTitle: '未说明「网络如何学习」',
        gapDetail: '没有提到反向传播与梯度下降——这是权重得以更新、网络得以收敛的机制。',
        severity: 'medium',
        reviewKinds: ['video'],
        followup: '损失变大时，权重应该往哪个方向调整？',
      },
    ],
  },
  ml: {
    anchor: '从数据里找规律',
    checks: [
      {
        concept: '三大学习范式',
        pattern: /监督|无监督|强化|标签|聚类/i,
        gapTitle: '未区分三大学习范式',
        gapDetail: '没讲到监督/无监督/强化学习的区别——例子有没有标签、要不要试错，是选择方法的第一步。',
        severity: 'high',
        reviewKinds: ['lecture', 'diagram'],
        followup: '判断垃圾邮件和给用户分群，各属于哪种学习范式？为什么？',
      },
      {
        concept: '泛化与过拟合',
        pattern: /泛化|过拟合|欠拟合|测试集|新数据/i,
        gapTitle: '没讲泛化与过拟合',
        gapDetail: '模型的价值在于对新数据有效；只在训练集上准叫过拟合，这是机器学习最核心的风险。',
        severity: 'medium',
        reviewKinds: ['video'],
        followup: '训练集 99 分、测试集 60 分，说明发生了什么？该怎么办？',
      },
    ],
  },
  dl: {
    anchor: '多层网络逐层提特征',
    checks: [
      {
        concept: '反向传播',
        pattern: /反向|链式|回传|backprop/i,
        gapTitle: '没讲反向传播',
        gapDetail: '深度网络靠反向传播用链式法则把梯度逐层回传——不讲它，就说不清网络是怎么"学"的。',
        severity: 'high',
        reviewKinds: ['lecture', 'diagram'],
        followup: '梯度是怎么从最后一层传回第一层的？靠什么法则？',
      },
      {
        concept: '梯度下降与学习率',
        pattern: /梯度下降|学习率|优化器|adam|sgd|步长/i,
        gapTitle: '未说明参数如何更新',
        gapDetail: '没有提到梯度下降与学习率——参数沿梯度反方向走多大步，直接决定训练收敛还是发散。',
        severity: 'medium',
        reviewKinds: ['video'],
        followup: '学习率调得过大会发生什么？过小呢？',
      },
    ],
  },
  cnn: {
    anchor: '卷积操作',
    checks: [
      {
        concept: '卷积核与局部感受野',
        pattern: /卷积核|滑窗|局部|感受野|参数共享|滤波/i,
        gapTitle: '卷积核机制没讲清',
        gapDetail: '没讲到卷积核滑窗与局部感受野——参数共享、局部连接正是 CNN 远省于全连接网络的原因。',
        severity: 'high',
        reviewKinds: ['lecture', 'diagram'],
        followup: '同一个卷积核在整张图上共享参数，这带来了什么好处？',
      },
      {
        concept: '池化（降采样）',
        pattern: /池化|降采样|下采样|pooling/i,
        gapTitle: '遗漏池化层',
        gapDetail: '没有提到池化——它缩小特征图、扩大感受野并带来平移鲁棒性，是经典 CNN 结构的另一半。',
        severity: 'medium',
        reviewKinds: ['video'],
        followup: '最大池化保留的是什么？为什么它让网络对平移更鲁棒？',
      },
    ],
  },
  transformer: {
    anchor: '注意力机制',
    checks: [
      {
        concept: '自注意力（Q·K → softmax → V）',
        pattern: /注意力|attention|softmax|query|key|相关性|加权/i,
        gapTitle: '自注意力计算流程没讲清',
        gapDetail: '没讲清 Query 与 Key 算相关性、softmax 归一为权重、再加权聚合 Value 这条主流程——它是 Transformer 的心脏。',
        severity: 'high',
        reviewKinds: ['lecture', 'diagram'],
        followup: '注意力权重为什么要过一遍 softmax？不归一化会怎样？',
      },
      {
        concept: '位置编码与多头',
        pattern: /位置编码|顺序|多头|position|head/i,
        gapTitle: '未提位置编码/多头',
        gapDetail: '注意力本身不感知顺序，靠位置编码补充；多头则让模型同时关注多种关系——两者都没讲到。',
        severity: 'medium',
        reviewKinds: ['video'],
        followup: '如果去掉位置编码，"猫追狗"和"狗追猫"在模型眼里有区别吗？',
      },
    ],
  },
  finetune: {
    anchor: '在预训练基础上做适配',
    checks: [
      {
        concept: '冻结/解冻与参数高效微调',
        pattern: /冻结|解冻|lora|adapter|低秩|参数高效|peft|分类头/i,
        gapTitle: '微调策略没讲清',
        gapDetail: '没讲到冻结/解冻哪些层、或 LoRA 等参数高效方法——训练多少参数正是各种微调策略的分水岭。',
        severity: 'high',
        reviewKinds: ['lecture', 'diagram'],
        followup: '冻结主干只训分类头，和全量微调相比省在哪里？可能牺牲什么？',
      },
      {
        concept: '预训练与下游任务的关系',
        pattern: /预训练|下游|少量数据|适配|遗忘/i,
        gapTitle: '未说明为什么能用少量数据微调',
        gapDetail: '没有讲到通用能力来自预训练、微调只做任务适配——这是微调成本远低于重训的根本原因。',
        severity: 'medium',
        reviewKinds: ['video'],
        followup: '为什么几千条数据就能微调出可用的领域模型？',
      },
    ],
  },
}

/** 非核心体系点的通用评估：讲清「解决什么问题」与「怎么运作」两条底线。 */
function genericFeynmanSpec(kpName: string, description: string): { anchor: string; checks: FeynmanCheck[] } {
  const desc = description || `${kpName} 的核心概念与应用`
  return {
    anchor: '整体框架',
    checks: [
      {
        concept: '要解决的问题与适用场景',
        pattern: /问题|解决|用来|目的|场景|为什么/i,
        gapTitle: `没讲清「${kpName}」要解决什么问题`,
        gapDetail: `先讲清它为什么存在（${desc}）——不明确问题与场景，机制就成了空中楼阁。`,
        severity: 'high',
        reviewKinds: ['lecture', 'diagram'],
        followup: `如果只用一句话向初学者说明「${kpName}」解决什么问题，你会怎么说？`,
      },
      {
        concept: '核心机制与工作流程',
        pattern: /机制|原理|流程|步骤|过程|工作|通过/i,
        gapTitle: '核心机制还没展开',
        gapDetail: `还听不出「${kpName}」内部是怎么一步步运作的——试着按"输入 → 处理 → 输出"的顺序走一遍。`,
        severity: 'medium',
        reviewKinds: ['video'],
        followup: `「${kpName}」从输入到输出要经过哪些关键步骤？`,
      },
    ],
  }
}

/** kpId → 该知识点的评估规格（核心点精修清单，体系点按名称/简介参数化）。 */
function feynmanSpecFor(kpId: string): { kpName: string; anchor: string; checks: FeynmanCheck[] } {
  const ks = ksPointById(kpId)
  const kpName = kpById(kpId)?.name ?? ks?.name ?? '当前知识点'
  const spec = FEYNMAN_SPECS[kpId] ?? genericFeynmanSpec(kpName, ks?.description ?? '')
  return { kpName, ...spec }
}

/** 按回看形态生成资源直达引用（标题跟随知识点名）。 */
function reviewRefsFor(kinds: FeynmanCheck['reviewKinds'], kpId: string, kpName: string): ReviewRef[] {
  return kinds.map((kind) =>
    kind === 'lecture'
      ? { kind, title: `《${kpName}》自适应讲义`, kpId, endpoint: '/resource/lecture', difficulty: '初级' }
      : kind === 'diagram'
        ? { kind, title: `${kpName} · 知识图解`, kpId, endpoint: '/resource/diagram' }
        : { kind, title: `${kpName} · 讲解视频`, kpId, endpoint: '/resource/video' }
  )
}

async function mockFeynman(opts: FeynmanStreamOptions): Promise<FeynmanDone> {
  const sid = opts.sessionId ?? `f_mock_${noteStore.size}_${stepStore.size}_${opts.explanation.length}`
  const turn = (mockFeynmanTurn.get(sid) ?? 0) + 1
  mockFeynmanTurn.set(sid, turn)

  const said = opts.explanation
  const { kpName, anchor, checks } = feynmanSpecFor(opts.kpId)
  const hit = checks.map((c) => c.pattern.test(said))

  let missSeen = 0
  const first =
    turn === 1
      ? [
          `我听到了你对「${kpName}」的讲解，`,
          `你抓住了「${anchor}」这一步，思路是对的——`,
          ...checks.map((c, i) => {
            if (hit[i]) return `也讲到了${c.concept}，很好。`
            missSeen += 1
            return missSeen === 1
              ? `但有个关键环节被跳过了：你还没有讲清**${c.concept}**。`
              : `另外，**${c.concept}**也还没讲清。`
          }),
        ]
      : [
          '这一版比上一轮完整多了！',
          `你把${checks.map((c) => c.concept).join('、')}都讲到了，`,
          '说明你已经把这条链路想通了 🎉。',
        ]

  for (const chunk of first) {
    await delay(280)
    opts.onDelta(chunk)
  }

  const missed = turn === 1 ? checks.filter((_, i) => !hit[i]) : []
  const gaps: FeynmanGap[] = missed.map((c, i) => ({
    id: `g${i + 1}`,
    kpId: opts.kpId,
    title: c.gapTitle,
    detail: c.gapDetail,
    severity: c.severity,
    review: reviewRefsFor(c.reviewKinds, opts.kpId, kpName),
  }))
  if (gaps.length) {
    await delay(320)
    opts.onGaps(gaps)
  }

  const complete = gaps.length === 0
  const score = turn === 1 ? (hit[0] ? 78 : 62) : 92
  const followups = complete ? [] : missed.map((c) => c.followup)
  return { sessionId: sid, score, followups, complete }
}

/** 18.2 费曼讲解评估（SSE 流式；mock 模式走本地流式模拟）。失败抛错由调用方兜底。 */
export async function feynmanStream(opts: FeynmanStreamOptions): Promise<FeynmanDone> {
  if (!USE_REAL_API) return mockFeynman(opts)

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch('/api/v1/learning/feynman', {
    method: 'POST',
    headers,
    body: JSON.stringify({ kpId: opts.kpId, sessionId: opts.sessionId, explanation: opts.explanation }),
  })

  const ctype = res.headers.get('content-type') ?? ''
  // 后端未走 SSE（返回整体 JSON 信封或错误）：解信封取终态字段
  if (!ctype.includes('text/event-stream')) {
    const env = (await res.json()) as { code?: number; message?: string; data?: Record<string, unknown> }
    if (env.code && env.code !== 0) throw new Error(env.message || '费曼评估失败')
    const d = (env.data ?? {}) as Record<string, unknown>
    if (typeof d.feedback === 'string') opts.onDelta(d.feedback)
    if (Array.isArray(d.gaps)) opts.onGaps(d.gaps as FeynmanGap[])
    return {
      sessionId: String(d.sessionId ?? ''),
      score: (d.score as number) ?? null,
      followups: (d.followups as string[]) ?? [],
      complete: Boolean(d.complete),
    }
  }
  if (!res.body) throw new Error('当前环境不支持流式读取')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let done: FeynmanDone | null = null

  const handleEvent = (block: string) => {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }
    if (!dataLines.length) return
    const payload = JSON.parse(dataLines.join('\n'))
    if (event === 'gaps') {
      opts.onGaps((payload.gaps ?? []) as FeynmanGap[])
    } else if (event === 'done') {
      done = {
        sessionId: payload.sessionId,
        score: payload.score ?? null,
        followups: payload.followups ?? [],
        complete: Boolean(payload.complete),
      }
    } else if (event === 'error') {
      throw new Error(payload.message || '费曼评估生成失败')
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
    reader.cancel().catch(() => {})
  }

  if (!done) throw new Error('费曼流式响应未正常结束')
  return done
}

/* ---------------- 工具：18.1 noteOutline → 主笔记区预填 Markdown ---------------- */

/** 把主笔记骨架渲染为可继续编辑的 Markdown（康奈尔右栏初始内容）。 */
export function outlineToMarkdown(outline: NoteOutline[]): string {
  return outline
    .map((o) => `## ${o.heading}\n${o.points.map((p) => `- ${p}`).join('\n')}`)
    .join('\n\n')
}
