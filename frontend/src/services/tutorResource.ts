/**
 * 智能辅导·按需资源生成（接口文档 8.8，C-fix 批3-bonus）。
 *
 * 学生在导学对话里提问 / 点「我没懂」→ 识别问题点 → 资源生成清单 → 勾选按需生成。
 * 联调走后端（复用既有讲义/图解/视频/例题生成能力）；mock 模式前端确定性合成（同结构）。
 */
import { USE_REAL_API, apiPost } from './api'
import { aggregateExternalResources } from './resource'
import { generateDiagram, generateLecture, generateVideo } from './documentLearning'
import type { ExternalResource } from '../components/ResourceAggregator'

export type RemedialType = 'diagram' | 'example' | 'video' | 'lecture'

/**
 * 文档辅导上下文标识：DocumentLearning 把当前文档写进 tutorContext.kpId，形如 `doc:<docId>`
 * （未选中文档时为无冒号的占位 `doc-learning`）。此处解出真实文档 id，用于把「即时辅导」
 * 的资源生成切到「基于该文档（文档专属向量集合）」的 /document/generate/* 链路。
 */
const DOC_CTX_PREFIX = 'doc:'
export function docIdOf(kpId: string): string | null {
  if (!kpId || !kpId.startsWith(DOC_CTX_PREFIX)) return null
  const id = kpId.slice(DOC_CTX_PREFIX.length).trim()
  return id || null
}
/** 文档上下文可复用的资源形态（一一映射到 /document/generate/*：图解 / 讲义 / 视频）。 */
const DOC_TYPES: RemedialType[] = ['diagram', 'lecture', 'video']
/** 文档场景生成时讲义/视频复用的难度档（辅导聚焦补缺，固定初级）。 */
const DOC_REMEDIAL_DIFFICULTY = '初级'

export interface RemedialSuggestion {
  id: string
  type: RemedialType
  title: string
  expect: string
}
export interface RemedialSuggestResult {
  kpId: string
  kpName: string
  problemPoint: string
  suggestions: RemedialSuggestion[]
}

export interface VideoScene {
  frame: number
  title: string
  points: string[]
  narration: string
}
export interface RemedialResource {
  type: RemedialType
  title: string
  mermaid?: string
  statement?: string
  solution?: string
  markdown?: string
  scenes?: VideoScene[]
}
export interface RemedialGenerateResult {
  kpId: string
  problemPoint: string
  results: RemedialResource[]
}

const TYPE_META: Record<RemedialType, { title: string; expect: string }> = {
  diagram: { title: '知识图解：{point}', expect: '用流程图直观呈现「{point}」的关键步骤与依赖关系' },
  example: { title: '例题精讲：{point}', expect: '一道围绕「{point}」的例题 + 分步解析' },
  video: { title: '短视频讲解：{point}', expect: '3-5 个分镜的动画讲解，配旁白逐步拆解「{point}」' },
  lecture: { title: '补充讲义片段：{point}', expect: '针对「{point}」的精炼讲义片段，含要点与小结' },
}
const ORDER: RemedialType[] = ['diagram', 'example', 'video', 'lecture']

const KEYWORD_POINTS: [RegExp, string][] = [
  [/激活|relu|sigmoid|tanh|非线性/i, '激活函数与非线性'],
  [/反向|梯度|backprop|链式|求导/i, '反向传播与梯度'],
  [/加权|求和|权重|偏置|bias/i, '神经元加权求和与偏置'],
  [/卷积|池化|感受野|卷积核/i, '卷积与池化'],
  [/注意力|attention|qkv|q\/k\/v|多头/i, '自注意力机制'],
  [/位置编码|position/i, '位置编码'],
  [/过拟合|正则|泛化/i, '过拟合与正则化'],
  [/优化器|sgd|adam|学习率/i, '优化器与学习率'],
  [/lora|微调|peft|对齐|rlhf|dpo/i, '大模型微调与对齐'],
]
const identifyProblem = (question: string, kpName: string): string => {
  for (const [re, point] of KEYWORD_POINTS) if (re.test(question || '')) return point
  return `${kpName}的核心概念`
}
const buildSuggestions = (point: string, types: RemedialType[] = ORDER): RemedialSuggestion[] =>
  types.map((t) => ({
    id: `r-${t}`,
    type: t,
    title: TYPE_META[t].title.replace('{point}', point),
    expect: TYPE_META[t].expect.replace('{point}', point),
  }))

/** 8.8 识别问题点 + 资源生成清单。 */
export async function suggestResources(
  kpId: string,
  question: string,
  kpName = '当前知识点'
): Promise<RemedialSuggestResult> {
  // 文档上下文：辅导资源要基于「当前文档」生成，而非内置知识点。/resource/tutor/suggest
  // 只认知识点（kp_id 非法 → 1004），故此处本地识别问题点 + 给出文档可复用的资源清单
  // （图解 / 讲义 / 视频），实际生成在 generateResources 里走 /document/generate/*。
  if (docIdOf(kpId)) {
    const point = identifyProblem(question, kpName)
    return { kpId, kpName, problemPoint: point, suggestions: buildSuggestions(point, DOC_TYPES) }
  }
  if (USE_REAL_API) {
    return apiPost<RemedialSuggestResult>('/resource/tutor/suggest', { kpId, question })
  }
  const point = identifyProblem(question, kpName)
  return { kpId, kpName, problemPoint: point, suggestions: buildSuggestions(point) }
}

/** mock 例题 / 讲义片段确定性合成（与后端 _mock_remedial_content 同口径）。 */
function mockContent(type: RemedialType, kpName: string, point: string): RemedialResource {
  if (type === 'example') {
    return {
      type,
      title: `例题 · ${point}`,
      statement: `【例题】围绕「${point}」：请说明它在「${kpName}」中的作用，并用一个具体例子说明其工作过程。`,
      solution: `解析：\n1. 先明确「${point}」的定义与要解决的问题；\n2. 结合「${kpName}」的整体流程，定位它处于哪一步、起什么作用；\n3. 举一个最小例子，代入数据走一遍，观察输入到输出的变化；\n小结：抓住「${point}」的本质，即可举一反三。`,
    }
  }
  return {
    type: 'lecture',
    title: `补充讲义 · ${point}`,
    markdown: `# 补充讲义 · ${point}\n\n> 针对你在「${kpName}」中卡住的「${point}」，这里做一段精炼补充。\n\n## 一、它解决什么问题\n\n「${point}」是「${kpName}」的关键一环——先理解它「要做什么」，再看「怎么做」。\n\n## 二、关键要点\n\n- 抓住「${point}」的输入、变换与输出三段式；\n- 留意它与相邻概念的衔接关系；\n- 用一个最小例子复现，建立可迁移的直觉。\n\n## 三、一句话小结\n\n理解「${point}」的本质，就抓住了这一段的核心。`,
  }
}

/** 文档上下文：把勾选类型逐项接到文档生成能力（/document/generate/*），并归一到 RemedialResource。
 *  这些接口基于该文档的专属向量集合生成、严格溯源，且后端旁路自动写入「我的资源库」。 */
async function generateDocResource(
  docId: string,
  type: RemedialType,
  point: string
): Promise<RemedialResource | null> {
  if (type === 'diagram') {
    const d = await generateDiagram(docId)
    return { type: 'diagram', title: `文档图解 · ${point}`, mermaid: d.mermaid }
  }
  if (type === 'lecture') {
    const d = await generateLecture(docId, DOC_REMEDIAL_DIFFICULTY)
    return { type: 'lecture', title: `文档讲义 · ${point}`, markdown: d.markdown }
  }
  if (type === 'video') {
    const d = await generateVideo(docId, DOC_REMEDIAL_DIFFICULTY)
    // 文档视频分镜（LectureScene）补 frame 铺帧位归一到 VideoScene（播放组件按 title/points/narration 渲染）。
    const scenes: VideoScene[] = d.scenes.map((s, i) => ({
      frame: i * 180,
      title: s.title,
      points: s.points,
      narration: s.narration,
    }))
    return { type: 'video', title: d.title || `文档视频讲解 · ${point}`, scenes }
  }
  return null
}

/** 8.8 按需生成勾选的针对性资源。mock 模式：图解/视频走静态占位，例题/讲义本地合成。 */
export async function generateResources(
  kpId: string,
  problemPoint: string,
  types: RemedialType[],
  kpName = '当前知识点'
): Promise<RemedialGenerateResult> {
  // 文档上下文：基于当前文档生成针对性资源（复用文档学习既有 /document/generate/* 能力）。
  const docId = docIdOf(kpId)
  if (docId) {
    const point = (problemPoint || '').trim() || `${kpName}的核心概念`
    const chosen = DOC_TYPES.filter((t) => types.includes(t))
    const results: RemedialResource[] = []
    for (const t of chosen) {
      const item = await generateDocResource(docId, t, point)
      if (item) results.push(item)
    }
    return { kpId, problemPoint: point, results }
  }
  if (USE_REAL_API) {
    return apiPost<RemedialGenerateResult>('/resource/tutor/generate', { kpId, problemPoint, types })
  }
  const chosen = ORDER.filter((t) => types.includes(t))
  const results: RemedialResource[] = chosen.map((t) => {
    if (t === 'diagram') {
      return {
        type: 'diagram',
        title: `知识图解 · ${problemPoint}`,
        mermaid: `flowchart LR\n  A["输入"] --> B(["${problemPoint}"])\n  B --> C["输出"]\n`,
      }
    }
    if (t === 'video') {
      return {
        type: 'video',
        title: `短视频讲解 · ${problemPoint}`,
        scenes: [
          { frame: 0, title: `导入 · ${problemPoint}`, points: ['建立整体认知'], narration: `本视频聚焦「${problemPoint}」，带你逐步拆解。` },
          { frame: 180, title: '关键步骤', points: ['核心要点 1', '核心要点 2'], narration: `拆解「${problemPoint}」的关键步骤。` },
        ],
      }
    }
    return mockContent(t, kpName, problemPoint)
  })
  return { kpId, problemPoint, results }
}

/**
 * 资源推荐（联网聚合的外部资源）作为成果项之一接入按需生成。
 * 复用既有 8.6 增量接口 aggregateExternalResources（不新增后端接口）；
 * mock 模式 / 后端无搜索能力时由前端确定性合成兜底（与 ResourceAggregator 种子同口径）。
 */
export async function fetchRecommendations(
  kpId: string,
  problemPoint: string,
  kpName = '当前知识点'
): Promise<ExternalResource[]> {
  // 文档上下文：外部资源聚合（8.6）以知识点为参数，不适用于文档 id，改用围绕问题点的
  // 确定性精选兜底（结构同 ExternalResource，可直接打开）。
  if (docIdOf(kpId)) return mockRecommendations(kpName, problemPoint)
  if (USE_REAL_API) {
    const res = await aggregateExternalResources(kpId, { weakPoints: [problemPoint] })
    return res.items
  }
  return mockRecommendations(kpName, problemPoint)
}

/** mock 外部资源推荐：围绕问题点的确定性精选（无后端搜索时兜底，结构同 ExternalResource）。 */
function mockRecommendations(kpName: string, point: string): ExternalResource[] {
  return [
    {
      id: 'rec-video',
      type: '视频',
      title: `可视化讲解：${point}`,
      source: 'YouTube · 3Blue1Brown',
      url: 'https://www.youtube.com/watch?v=aircAruvnKk',
      embed: 'https://www.youtube.com/embed/aircAruvnKk',
      duration: '19:13',
      relevance: 96,
      credibility: 97,
      reason: `用动画直观呈现「${point}」，契合你在「${kpName}」中卡住的盲区。`,
    },
    {
      id: 'rec-course',
      type: '课程',
      title: `系统课程：从基础到「${point}」`,
      source: 'Stanford 公开课 CS231n',
      url: 'https://cs231n.github.io/',
      duration: '系列',
      relevance: 90,
      credibility: 96,
      reason: `权威课程循序讲透「${kpName}」相关脉络，适合补齐「${point}」的体系认知。`,
    },
    {
      id: 'rec-doc',
      type: '文档',
      title: `动手实践：${point}`,
      source: 'PyTorch 官方教程',
      url: 'https://pytorch.org/tutorials/beginner/basics/buildmodel_tutorial.html',
      duration: '实操',
      relevance: 88,
      credibility: 95,
      reason: `把「${point}」落到可运行代码，边做边理解，巩固直觉。`,
    },
  ]
}
