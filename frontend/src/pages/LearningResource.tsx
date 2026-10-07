import { useEffect, useMemo, useRef, useState, lazy, Suspense } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import MarkdownRenderer from '../components/MarkdownRenderer'
import QuizRenderer, { type QuizQuestion, type QuizGrade, type ShortAnswerGrade } from '../components/QuizRenderer'
import { defaultSources, type SourceRef } from '../components/SourceTrace'
import WeakPointReinforce from '../components/WeakPointReinforce'
import PageHeader from '../components/PageHeader'
import LearningFlow from '../components/LearningFlow'
import StuckNudge from '../components/StuckNudge'
import { setTutorContext } from '../services/tutorBus'
import { reportStuck } from '../services/stuckSignal'
import ResourceIllustration, { type ResourceIllustrationType } from '../components/ResourceIllustration'
import ResourceTypeIcon from '../components/ResourceTypeIcon'
import { RevealGroup, RevealItem } from '../components/Reveal'
import { useMastery, STATUS_LABEL } from '../store/mastery'
import { kpById } from '../data/knowledgePoints'
import { ksPointById } from '../data/knowledgeSystem'
import { KP_RESOURCES, genericKpContent, kpCodeDemo } from '../data/kpResources'
import { USE_REAL_API } from '../services/api'
import { getDiagram, getLecture, getQuiz, getVideo, loadAggregateCached, submitQuiz, type LectureData } from '../services/resource'
import { fetchRecommendations } from '../services/tutorResource'
import { StatusChip, type ItemStatus, useStagedProgress, VIDEO_STAGES } from '../components/genStatus'
import { exportLectureMarkdown, exportLectureToPdf } from '../utils/lectureExport'
import { logResourceGeneration } from '../services/resourceHistory'
import '../components/TutorResourcePanel.css'
import { getSteps, type ReviewRef } from '../services/learningFlow'
import { executeWorkflow, connectWorkflowSocket } from '../services/workflow'
import { consumeResourceEntryTab, consumeResourceMode, getResourceKpId } from '../services/resourceNav'
import { setWorkflowReplay } from '../services/workflowNav'
import type { PageType } from '../App'
import './LearningResource.css'

/* B10：工作流阶段 → 迷你进度文案（资源页重新生成时内嵌展示） */
const REGEN_PHASE_LABEL: Record<string, string> = {
  idle: '排队中',
  diagnosis: '学情诊断',
  generation: '资源生成',
  validation: '内容审核',
  complete: '已完成',
}

/* 后端讲义 sources（confidence 0-1）→ SourceTrace 展示口径（0-100） */
const toSourceRefs = (sources: LectureData['sources']): SourceRef[] =>
  sources.map((s) => ({
    title: s.title,
    type: s.type as SourceRef['type'],
    confidence: Math.round(s.confidence * 100),
  }))

/* 重型多模态组件按需懒加载（打开对应 Tab 才加载，保持页面轻量）*/
const MindMap = lazy(() => import('../components/MindMap'))
const CodeSandbox = lazy(() => import('../components/CodeSandbox'))
const MermaidDiagram = lazy(() => import('../components/MermaidDiagram'))
const VideoLecture = lazy(() => import('../components/VideoLecture'))
const ResourceAggregator = lazy(() => import('../components/ResourceAggregator'))
const SocraticTutor = lazy(() => import('../components/SocraticTutor'))

/* 模拟"领域知识生成 Agent"为当前学习者生成的个性化讲义（基于画像，难度自适应）*/
const lectureContent = `# 神经网络基础

> 本讲义由**领域知识生成 Agent**基于你的学情画像生成，难度已适配为「初级」，并经**内容审核 Agent** RAG 交叉校验（幻觉率 < 5%）。

## 一、什么是神经网络

神经网络（Neural Network）是一种受生物神经系统启发的计算模型。它由大量**神经元（Neuron）**相互连接构成，通过调整连接权重来学习数据中的规律。

一个神经元完成三步运算：

1. 对输入做**加权求和**
2. 加上**偏置（bias）**
3. 经过**激活函数**输出

## 二、前向传播

前向传播是数据从输入层流向输出层的过程。对单个神经元：

\`\`\`python
import numpy as np

def neuron(inputs, weights, bias):
    # 1. 加权求和 + 偏置
    z = np.dot(inputs, weights) + bias
    # 2. ReLU 激活
    return np.maximum(0, z)

x = np.array([0.5, 0.8, 0.2])
w = np.array([0.4, 0.7, 0.1])
print(neuron(x, w, bias=0.1))  # -> 0.88
\`\`\`

## 三、常见激活函数

不同激活函数适用场景不同，下表做横向对比：

| 激活函数 | 公式 | 输出范围 | 适用场景 |
| --- | --- | --- | --- |
| ReLU | \`max(0, x)\` | [0, +∞) | 隐藏层首选，计算快、缓解梯度消失 |
| Sigmoid | \`1 / (1 + e^-x)\` | (0, 1) | 二分类输出层 |
| Tanh | \`(e^x - e^-x) / (e^x + e^-x)\` | (-1, 1) | 零均值，收敛通常快于 Sigmoid |

## 四、反向传播与学习

网络通过**反向传播（Backpropagation）**计算损失对每个权重的梯度，再用**梯度下降**更新权重：

\`w ← w − η · ∂L/∂w\`

其中 \`η\` 是学习率（learning rate）。多轮迭代后，网络逐步逼近最优参数。

> **小结**：神经元 → 前向传播 → 激活 → 反向传播更新权重，构成了神经网络学习的完整闭环。下一步建议学习「深度学习原理」。`

/* 难度自适应：同一知识点的「入门 / 初级 / 高级」三档讲义（演示 Agent 实时再生成）*/
const lectureByLevel: Record<string, string> = {
  入门: `# 神经网络基础（入门版）

> 本讲义由**领域知识生成 Agent**按「入门」难度为你重新生成——用最直白的比喻，少公式。

## 一、把神经元想成一个「打分员」

想象一个评委给选手打分：他听到几个信息（输入），每个信息**重要程度不同**（权重），把它们综合起来给个分数，分数太低就当作 0（激活）。这就是一个**神经元**。

## 二、三步走

1. 把每个输入 × 它的重要程度，加起来（**加权求和**）
2. 再加一个「基础分」（**偏置**）
3. 太小的就归零，留下有用的（**激活函数 ReLU**）

## 三、很多神经元连起来

一个评委不够，就请很多评委分层投票——这就是**神经网络**。它通过不断调整「重要程度」，越打越准。

> **一句话**：神经网络 = 一群会自我调整的打分员。下一步可以看「初级版」了解具体计算。`,

  初级: lectureContent,

  高级: `# 神经网络基础（高级版）

> 本讲义由**领域知识生成 Agent**按「高级」难度生成——侧重数学形式化与工程细节。

## 一、神经元的数学形式

第 \`l\` 层第 \`j\` 个神经元：

\`\`\`
z_j^(l) = Σ_i w_ji^(l) · a_i^(l-1) + b_j^(l)
a_j^(l) = σ(z_j^(l))
\`\`\`

其中 \`σ\` 为激活函数，\`a^(0) = x\` 为输入。

## 二、前向传播（向量化）

整层以矩阵形式一次计算，效率远高于逐神经元：

\`\`\`python
Z = W @ A_prev + b      # W:(n_l, n_{l-1})
A = relu(Z)
\`\`\`

## 三、反向传播与梯度

链式法则逐层回传误差 \`δ\`：

\`\`\`
δ^(L) = ∇_a L ⊙ σ'(z^(L))
δ^(l) = (W^(l+1)ᵀ δ^(l+1)) ⊙ σ'(z^(l))
∂L/∂W^(l) = δ^(l) (a^(l-1))ᵀ
\`\`\`

## 四、工程要点

- **初始化**：He 初始化配 ReLU，避免梯度消失/爆炸
- **优化器**：Adam 自适应学习率，收敛快于朴素 SGD
- **正则化**：Dropout、BatchNorm、权重衰减抑制过拟合

> **小结**：掌握向量化前向 + 链式反向 + 初始化/优化/正则三件套，即可手写一个可训练的 MLP。下一步建议「深度学习原理 / CNN」。`,
}

const LEVELS = ['入门', '初级', '中级', '高级', '精通'] as const

/* 纯 mock 离线兜底：五档难度 → 本地三档讲义内容映射（中级并入初级、精通并入高级）。
   联调（USE_REAL_API=true，默认）下五档均由后端按对应 depth 实时生成，不走此映射。 */
const MOCK_LECTURE_LEVEL: Record<(typeof LEVELS)[number], '入门' | '初级' | '高级'> = {
  入门: '入门', 初级: '初级', 中级: '初级', 高级: '高级', 精通: '高级',
}

/* 分阶测试（mock 兜底）：10 题——单选/多选/判断客观题 + 末尾 1 道简答题，
   与后端种子题库 nn 逐字对齐（联调走 GET /quiz/nn 真实题库）。简答题 options 为空、
   correct_answer 存参考要点、explanation 存参考答案。 */
const quizQuestions: QuizQuestion[] = [
  {
    question_id: 'nn_q1',
    question_type: 'single',
    question_text: '一个神经元的运算顺序是？',
    options: [
      { option_id: 'a', option_text: '激活函数 → 加权求和 → 加偏置' },
      { option_id: 'b', option_text: '加权求和 → 加偏置 → 激活函数' },
      { option_id: 'c', option_text: '加偏置 → 激活函数 → 加权求和' },
    ],
    correct_answer: 'b',
    explanation: '神经元先对输入加权求和，再加上偏置，最后通过激活函数得到输出。',
  },
  {
    question_id: 'nn_q2',
    question_type: 'multiple',
    question_text: '以下哪些是常见的激活函数？（多选）',
    options: [
      { option_id: 'a', option_text: 'ReLU' },
      { option_id: 'b', option_text: 'Sigmoid' },
      { option_id: 'c', option_text: 'Gradient' },
      { option_id: 'd', option_text: 'Tanh' },
    ],
    correct_answer: ['a', 'b', 'd'],
    explanation: 'ReLU、Sigmoid、Tanh 都是常见激活函数；Gradient（梯度）是反向传播中的概念，不是激活函数。',
  },
  {
    question_id: 'nn_q3',
    question_type: 'boolean',
    question_text: 'ReLU 激活函数有助于缓解梯度消失问题。',
    options: [
      { option_id: 'true', option_text: '正确' },
      { option_id: 'false', option_text: '错误' },
    ],
    correct_answer: 'true',
    explanation: 'ReLU 在正区间梯度恒为 1，相比 Sigmoid 能有效缓解深层网络的梯度消失问题。',
  },
  {
    question_id: 'nn_q4',
    question_type: 'single',
    question_text: '在前向传播中，神经网络的作用是？',
    options: [
      { option_id: 'a', option_text: '根据当前参数由输入计算出预测输出' },
      { option_id: 'b', option_text: '根据误差更新权重参数' },
      { option_id: 'c', option_text: '计算损失函数对权重的梯度' },
      { option_id: 'd', option_text: '对训练数据进行随机打乱' },
    ],
    correct_answer: 'a',
    explanation: '前向传播是指由输入逐层计算到输出的过程；更新权重和计算梯度属于反向传播阶段。',
  },
  {
    question_id: 'nn_q5',
    question_type: 'single',
    question_text: '引入激活函数最主要的目的是？',
    options: [
      { option_id: 'a', option_text: '加快矩阵乘法的运算速度' },
      { option_id: 'b', option_text: '为网络引入非线性表达能力' },
      { option_id: 'c', option_text: '减少网络的参数数量' },
      { option_id: 'd', option_text: '防止输入数据出现缺失值' },
    ],
    correct_answer: 'b',
    explanation: '若没有非线性激活函数，多层线性变换的叠加仍等价于单层线性变换，网络无法拟合复杂的非线性关系。',
  },
  {
    question_id: 'nn_q6',
    question_type: 'boolean',
    question_text: '如果所有隐藏层都不使用激活函数（即仅做线性变换），那么无论叠加多少层，整个网络都等价于一个线性模型。',
    options: [
      { option_id: 'true', option_text: '正确' },
      { option_id: 'false', option_text: '错误' },
    ],
    correct_answer: 'true',
    explanation: '多个线性变换的复合仍然是线性变换，因此缺少非线性激活的多层网络等价于单层线性模型。',
  },
  {
    question_id: 'nn_q7',
    question_type: 'multiple',
    question_text: '关于梯度下降法，以下说法正确的是？（多选）',
    options: [
      { option_id: 'a', option_text: '沿损失函数负梯度方向更新参数' },
      { option_id: 'b', option_text: '学习率控制每次参数更新的步长' },
      { option_id: 'c', option_text: '学习率过大可能导致损失震荡甚至发散' },
      { option_id: 'd', option_text: '梯度下降一定能找到全局最优解' },
    ],
    correct_answer: ['a', 'b', 'c'],
    explanation: '梯度下降沿负梯度方向更新参数，学习率决定步长且过大易震荡发散；但对于非凸损失，梯度下降只能保证收敛到局部最优或鞍点，不保证全局最优。',
  },
  {
    question_id: 'nn_q8',
    question_type: 'single',
    question_text: '偏置（bias）在神经元中的主要作用是？',
    options: [
      { option_id: 'a', option_text: '对输入特征进行归一化' },
      { option_id: 'b', option_text: '为激活函数提供可平移的截距项' },
      { option_id: 'c', option_text: '随机丢弃部分神经元' },
      { option_id: 'd', option_text: '对权重施加 L2 惩罚' },
    ],
    correct_answer: 'b',
    explanation: '偏置相当于线性函数中的截距，使决策边界可以平移，而不必强制通过原点，增强了模型的表达能力。',
  },
  {
    question_id: 'nn_q9',
    question_type: 'boolean',
    question_text: '将一个神经网络的所有权重都初始化为相同的常数（如全 0）是一种好的做法。',
    options: [
      { option_id: 'true', option_text: '正确' },
      { option_id: 'false', option_text: '错误' },
    ],
    correct_answer: 'false',
    explanation: '权重全部初始化为相同值会导致同一层神经元在前向和反向传播中完全对称、更新一致，无法学到不同特征，因此需要随机初始化打破对称性。',
  },
  {
    question_id: 'nn_q10',
    question_type: 'short_answer',
    question_text: '请简述神经网络中前向传播与反向传播的含义及二者的关系。',
    options: [],
    correct_answer: [
      '前向传播：由输入逐层计算得到预测输出',
      '反向传播：由输出端的损失反向逐层计算梯度',
      '反向传播依赖链式法则',
      '前向结果用于计算损失，反向梯度用于更新参数',
      '二者交替迭代完成训练',
    ],
    explanation:
      '前向传播是指给定输入后，依据当前网络参数从输入层经隐藏层逐层计算，最终得到预测输出，并据此计算损失函数值。反向传播则是从输出端的损失出发，利用链式法则反向逐层计算损失对各层权重和偏置的梯度。二者紧密关联：前向传播提供了计算损失所需的中间结果，反向传播据此求得梯度，再由梯度下降等优化器更新参数。训练过程就是前向传播与反向传播不断交替迭代，直至损失收敛。',
  },
]

/* 简答题本地确定性评分（mock 兜底，与后端 LLMClient._mock_score_short_answer 同口径：
   参考要点字符二元组覆盖度）。联调模式由后端 AI 评分回填，不走此函数。 */
const answerBigrams = (t: string): Set<string> => {
  const s = (t || '').toLowerCase().replace(/\s+/g, '')
  const out = new Set<string>()
  for (let i = 0; i < s.length - 1; i++) out.add(s.slice(i, i + 2))
  return out
}
const pointCoverage = (point: string, answer: string): number => {
  const pb = answerBigrams(point)
  if (pb.size === 0) return 0
  const ab = answerBigrams(answer)
  let hit = 0
  pb.forEach((b) => { if (ab.has(b)) hit++ })
  return hit / pb.size
}
const scoreShortLocally = (points: string[], answer: string): { score: number; comment: string } => {
  const ans = (answer || '').trim()
  if (!ans) return { score: 0, comment: points.length ? `未作答。参考要点：${points.join('；')}` : '未作答。' }
  if (!points.length) return { score: ans.length >= 16 ? 70 : ans.length >= 6 ? 50 : 30, comment: '已作答，按作答完整度给分。' }
  const per = points.map((p) => ({ c: pointCoverage(p, ans), p }))
  const covered = per.filter((x) => x.c >= 0.34).map((x) => x.p)
  const missing = per.filter((x) => x.c < 0.34).map((x) => x.p)
  const avg = per.reduce((s, x) => s + x.c, 0) / per.length
  const score = Math.max(0, Math.min(100, Math.round(Math.min(1, avg / 0.5) * 100)))
  const parts: string[] = []
  if (covered.length) parts.push(`已覆盖：${covered.join('、')}`)
  if (missing.length) parts.push(`可补充：${missing.join('、')}`)
  return { score, comment: parts.length ? `${parts.join('；')}。` : '已完成评分。' }
}
const arraysEqualLocal = (a: string[], b: string[]) =>
  a.length === b.length && [...a].sort().join(',') === [...b].sort().join(',')
/** mock 综合判分：客观题等权 + 简答 AI 折算（与后端 services.quiz.submit 同口径）。 */
const gradeQuizLocally = (
  qs: QuizQuestion[],
  answers: Record<string, string | string[]>
): QuizGrade => {
  let objectiveCorrect = 0
  let shortSum = 0
  const shortAnswers: ShortAnswerGrade[] = []
  for (const q of qs) {
    if (q.question_type === 'short_answer') {
      const a = answers[q.question_id]
      const text = typeof a === 'string' ? a : ''
      const points = Array.isArray(q.correct_answer) ? q.correct_answer : []
      const g = scoreShortLocally(points, text)
      shortSum += g.score
      shortAnswers.push({ questionId: q.question_id, score: g.score, comment: g.comment })
    } else {
      const a = answers[q.question_id]
      const ok = Array.isArray(q.correct_answer)
        ? Array.isArray(a) && arraysEqualLocal(a, q.correct_answer)
        : a === q.correct_answer
      if (ok) objectiveCorrect++
    }
  }
  const total = qs.length
  const score = total ? Math.round(((objectiveCorrect + shortSum / 100) / total) * 100) : 0
  return { score, passed: score >= 70, shortAnswers }
}

/* 思维导图大纲（由生成 Agent 从讲义结构化得到）*/
const mindmapMarkdown = `# 神经网络基础
## 神经元
### 加权求和 Σ w·x
### 加偏置 +b
### 激活函数
#### ReLU
#### Sigmoid
#### Tanh
## 前向传播
### 输入层 → 隐藏层 → 输出层
## 反向传播
### 计算梯度
### 梯度下降
### 学习率 η
## 进阶方向
### CNN
### RNN
### Transformer
`

/* 知识图解（神经元前向 / 反向流程）*/
const mermaidChart = `flowchart LR
  X["输入 x"] --> S(["加权求和<br/>Σ w·x"])
  W["权重 w"] --> S
  S --> B(["加偏置<br/>+ b"])
  B --> A{{"激活函数<br/>ReLU"}}
  A --> O["输出 a"]
  O -. 反向传播更新 .-> W
`

/* 资源中枢可打开的全部内容（5 张插画卡 + 测试 + 两个辅助项）；
   插画卡走 layoutId 共享元素过场，其余从主按钮/小 chip 打开。 */
type Tab = 'lecture' | 'video' | 'mindmap' | 'diagram' | 'code' | 'quiz' | 'external' | 'tutor'

/* 每个内容的详情头部主题色（沿用插画浅底，保持卡片→详情视觉连续）*/
const RESOURCE_META: Record<Tab, { title: string; theme: string }> = {
  lecture: { title: '定制讲义', theme: '#e7f4ee' },
  video: { title: '讲解视频', theme: '#e8f0fb' },
  mindmap: { title: '思维导图', theme: '#efeafb' },
  diagram: { title: '知识图解', theme: '#fbf2e2' },
  code: { title: '代码实操', theme: '#eceef3' },
  quiz: { title: '分阶测试', theme: 'color-mix(in srgb, var(--success-500) 14%, var(--surface))' },
  external: { title: '资源推荐', theme: '#e3f3f3' },
  tutor: { title: '导学对话', theme: 'color-mix(in srgb, var(--primary) 12%, var(--surface))' },
}

/* 学习内容 → 插画卡片网格（与 ResourceIllustration 的 5 个类型一一对应）*/
const RESOURCE_CARDS: { id: ResourceIllustrationType; title: string; desc: string }[] = [
  { id: 'lecture', title: '定制讲义', desc: '根据你的学习路径生成的个性化讲义，难度自适应、RAG 可溯源。' },
  { id: 'video', title: '讲解视频', desc: '动画演示与案例讲解，配同步旁白，更直观地理解知识点。' },
  { id: 'mindmap', title: '思维导图', desc: '把讲义结构化成知识脉络图，构建完整的知识体系。' },
  { id: 'diagram', title: '知识图解', desc: '图文结合的方式，更清晰地展示前向/反向的知识关系。' },
  { id: 'code', title: '代码实操', desc: '浏览器内可运行的示例，改完即时看结果，加深实践应用。' },
]

/* hub 首屏「核心资源速览」六类卡（5 张插画卡 + 资源推荐）；每张「立即查看」= 按需生成后打开。 */
const HUB_CARDS: { id: GenType; title: string; desc: string }[] = [
  ...RESOURCE_CARDS,
  { id: 'external', title: '资源推荐', desc: 'AI 联网聚合的优质外部资源（视频/课程/论文/文档），可直接打开。' },
]

const ALL_TAB_IDS = Object.keys(RESOURCE_META)
const isTab = (v: string | null): v is Tab => !!v && ALL_TAB_IDS.includes(v)

/* 主资源生成·可逐项生成的资源类型（5 张卡片 + 资源推荐并入成果区）。
   复用会话二 TutorResourcePanel 的「勾选 → 逐项调用既有单类型生成接口 → 逐项进度/逐项呈现」体验。 */
type GenType = 'lecture' | 'video' | 'mindmap' | 'diagram' | 'code' | 'external'
const GEN_ICON: Record<GenType, string> = {
  lecture: '📖',
  video: '🎬',
  mindmap: '🧠',
  diagram: '📊',
  code: '💻',
  external: '🔗',
}
/* 生成进度阶段文案（复用文档学习那套 useStagedProgress 阶段化推进机制，见 components/genStatus）：
   讲义/导图/图解/代码走通用 4 段（检索对象为 RAG 知识库）；视频沿用长任务 5 段；资源推荐按联网聚合语义措辞。 */
const RES_STAGES = ['检索知识库', '组织知识结构', '生成内容', '排版与渲染'] as const
const EXTERNAL_STAGES = ['联网检索资源', '聚合去重', '质量评估', '整理呈现'] as const
const stagesFor = (t: GenType) => (t === 'video' ? VIDEO_STAGES : t === 'external' ? EXTERNAL_STAGES : RES_STAGES)

const GEN_OPTIONS: { type: GenType; title: string; expect: string }[] = [
  { type: 'lecture', title: '定制讲义', expect: '按你的难度档生成的个性化讲义，RAG 可溯源' },
  { type: 'video', title: '讲解视频', expect: '动画分镜 + 同步旁白的讲解视频' },
  { type: 'mindmap', title: '思维导图', expect: '由讲义结构化的知识脉络图' },
  { type: 'diagram', title: '知识图解', expect: '按当前主题真实生成的 Mermaid 知识脉络图' },
  { type: 'code', title: '代码实操', expect: '浏览器内可运行的示例，改完即时看结果' },
  { type: 'external', title: '资源推荐', expect: 'AI 联网聚合的优质外部资源（视频/课程/论文/文档），可打开学习' },
]

/** 从讲义 markdown 提取标题大纲（跳过代码块内的 # 注释行），供思维导图结构化 */
function lectureOutline(md: string): string {
  let inFence = false
  const lines: string[] = []
  for (const line of md.split('\n')) {
    if (/^\s*```/.test(line)) {
      inFence = !inFence
      continue
    }
    if (!inFence && /^#{1,4}\s/.test(line)) lines.push(line)
  }
  return lines.join('\n')
}

const Loading = () => <div className="resource-loading">资源加载中…</div>

/* hub 首屏两大操作卡的场景插画（纯 SVG 绘制，套「分层柔和色 + 投影 + 星点」的既有插画风格；
   固定浅底面板，深浅主题均适配）。有序学习＝暖色路径地图 + 小红旗；资源中枢＝绿色开箱多资源。 */
function HeroFlowArt() {
  return (
    <svg className="res-hero__svg" viewBox="0 0 176 156" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs>
        <filter id="heroFlowSh" x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="3" stdDeviation="4" floodColor="#8a5a24" floodOpacity="0.2" />
        </filter>
      </defs>
      <rect width="176" height="156" rx="18" fill="#f6ecd6" />
      {/* 起伏地面 */}
      <path d="M0 118 Q44 100 88 116 T176 112 V156 H0 Z" fill="#efdcb0" opacity="0.7" />
      {/* 路径底带 + 虚线中线 */}
      <path d="M32 128 C 52 96, 96 120, 88 82 C 82 52, 122 66, 140 32" fill="none" stroke="#e6cf9c" strokeWidth="13" strokeLinecap="round" />
      <path d="M32 128 C 52 96, 96 120, 88 82 C 82 52, 122 66, 140 32" fill="none" stroke="#c58940" strokeWidth="3" strokeLinecap="round" strokeDasharray="1.5 12" />
      {/* 起点 */}
      <g filter="url(#heroFlowSh)">
        <circle cx="32" cy="128" r="9" fill="#fff" />
        <circle cx="32" cy="128" r="4.4" fill="#5b7f6e" />
      </g>
      {/* 途经里程碑 */}
      <g filter="url(#heroFlowSh)">
        <circle cx="88" cy="82" r="9" fill="#fff" />
        <circle cx="88" cy="82" r="4.4" fill="#c58940" />
      </g>
      {/* 终点小红旗 */}
      <g filter="url(#heroFlowSh)">
        <rect x="137" y="30" width="3.4" height="34" rx="1.7" fill="#7a5a34" />
        <path d="M140 30 L164 38 L140 47 Z" fill="#e0573b" />
      </g>
      <path d="M150 96 Q152 103 159 105 Q152 107 150 114 Q148 107 141 105 Q148 103 150 96Z" fill="#f0b94a" />
    </svg>
  )
}

function HeroBrowseArt() {
  return (
    <svg className="res-hero__svg" viewBox="0 0 176 156" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs>
        <filter id="heroBrowseSh" x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="3" stdDeviation="4" floodColor="#2f5a45" floodOpacity="0.2" />
        </filter>
      </defs>
      <rect width="176" height="156" rx="18" fill="#e6f1ea" />
      {/* 开箱翻盖（在最底层） */}
      <path d="M40 92 L28 78 L70 83 Z" fill="#496b5b" />
      <path d="M136 92 L148 78 L106 83 Z" fill="#496b5b" />
      {/* 从箱口探出的多类资源卡 */}
      <g filter="url(#heroBrowseSh)">
        <rect x="54" y="44" width="26" height="48" rx="5" fill="#e9c07a" transform="rotate(-13 67 68)" />
        <rect x="96" y="42" width="26" height="48" rx="5" fill="#8fc3a8" transform="rotate(12 109 66)" />
        <rect x="75" y="34" width="28" height="52" rx="5" fill="#ffffff" />
        <rect x="81" y="45" width="16" height="4" rx="2" fill="#cbdbd1" />
        <rect x="81" y="54" width="16" height="4" rx="2" fill="#dae6df" />
        <rect x="81" y="63" width="10" height="4" rx="2" fill="#dae6df" />
      </g>
      {/* 绿色资源箱（箱体盖住卡片下缘＝插入箱内） */}
      <g filter="url(#heroBrowseSh)">
        <path d="M40 92 L136 92 L128 134 L48 134 Z" fill="#5b7f6e" />
        <path d="M40 92 L88 102 L136 92 L88 82 Z" fill="#6f9280" />
        <rect x="72" y="106" width="32" height="9" rx="4.5" fill="#83a593" />
      </g>
      <path d="M150 104 Q152 111 159 113 Q152 115 150 122 Q148 115 141 113 Q148 111 150 104Z" fill="#f0b94a" />
    </svg>
  )
}

export default function LearningResource({ onNavigate }: { onNavigate?: (page: PageType) => void }) {
  /* 当前知识点：无论联调/mock 都从 resourceNav 路由传参通道取（页面随导航重挂载，挂载时读取即可）。
     mock 模式按 kpId 选对应知识点内容（KP_RESOURCES），故从学习路径点不同知识点会进入对应内容，
     不再全部落到神经网络；未命中的 kpId 回退到 nn 的精修常量（见下方 mockRes）。 */
  const kpId = getResourceKpId()
  /* mock 模式下当前知识点的本地内容包（讲义三档/测验/导图/图解）。
     KP_RESOURCES 未收录 nn——nn 沿用本文件下方既有精修常量作为默认兜底；
     非核心目录点（78 点体系）→ 按名称/简介参数化的通用模板包，不再误落 nn 内容。 */
  const ksPoint = ksPointById(kpId)
  const mockRes =
    KP_RESOURCES[kpId] ??
    (ksPoint && !ksPoint.isCore
      ? genericKpContent(ksPoint.name, ksPoint.description)
      : {
          lectureByLevel,
          quiz: quizQuestions,
          mindmap: mindmapMarkdown,
          diagram: mermaidChart,
        })
  /* 进入意图 + 落点 Tab 一次性消费（同一挂载内只读一次，供 view/openId 初值共享）。 */
  const initialMode = useMemo(() => consumeResourceMode(), [])
  const initialTab = useMemo(() => consumeResourceEntryTab(), [])

  /* 三视图：
     - hub    资源总览首屏（两大操作卡 + 状态条 + 六类速览 + 阶段测试条）——瘦身、核心操作优先
     - flow   有序学习（费曼+康奈尔有序流，内容同原「有序学习」模式，功能不变）
     - browse 资源中枢（选择性逐项生成 + 卡片网格，内容同原「资源中枢」模式，功能不变）
     进入意图：「开始学习」→flow、「查看资源」→browse（可带落点）；无意图（侧栏/图谱直达）→hub。 */
  const [view, setView] = useState<'hub' | 'flow' | 'browse'>(() => {
    if (initialMode === 'flow') return 'flow'
    if (initialMode === 'browse' || isTab(initialTab)) return 'browse'
    return 'hub'
  })
  /* 资源详情过场：openId=当前打开的内容（null=只显示卡片网格）。
     落点：路径页「查看资源」带的落点 Tab 会直接展开对应内容；无显式落点则停在网格。 */
  const [openId, setOpenId] = useState<Tab | null>(isTab(initialTab) ? initialTab : null)

  /* 费曼缺口「回看」：切到资源中枢，直接展开对应资源详情 */
  const REVIEW_KIND_TO_TAB: Record<string, Tab> = {
    lecture: 'lecture',
    video: 'video',
    diagram: 'diagram',
    mindmap: 'mindmap',
    quiz: 'quiz',
    code: 'code',
    external: 'external',
  }
  const handleReview = (ref: ReviewRef) => {
    setView('browse')
    setOpenId(REVIEW_KIND_TO_TAB[ref.kind] ?? 'lecture')
  }
  const [level, setLevel] = useState<(typeof LEVELS)[number]>('初级')
  const [regenerating, setRegenerating] = useState(false)
  const [wrongQs, setWrongQs] = useState<QuizQuestion[]>([])
  /* 综合判分结果（含简答 AI 评分）：提交后回填给 QuizRenderer 渲染分数/点评 */
  const [quizGrade, setQuizGrade] = useState<QuizGrade | null>(null)
  const [trustOpen, setTrustOpen] = useState(false)

  /* ---------- 主资源生成·选择性逐项生成（复用会话二 TutorResourcePanel 的那套体验） ----------
     勾选要生成的资源类型 → 逐项调用既有单类型生成接口（getLecture/getVideo/getDiagram，
     思维导图由讲义结构化、代码为可运行示例占位）→ 每项独立状态 + 顶部总进度 → 生成完一项
     即解锁对应卡片；资源推荐（fetchRecommendations 联网聚合）并入成果区为第 6 张卡片。
     未开始生成时全部卡片可直接浏览（保留既有「查看资源」落点/费曼回看入口不回归）。 */
  const [genPicked, setGenPicked] = useState<Set<GenType>>(() => new Set(GEN_OPTIONS.map((o) => o.type)))
  const [genRunning, setGenRunning] = useState(false)
  const [genStatus, setGenStatus] = useState<Partial<Record<GenType, ItemStatus>>>({})
  const [recoCount, setRecoCount] = useState<number | null>(null)
  /* 批量「选择性生成」是否已发起（仅 runGeneration 置位）。锁定规则只认它，
     使 hub 首屏「立即查看」的单项按需生成不会误锁资源中枢里其它未生成卡片。 */
  const [genBatchStarted, setGenBatchStarted] = useState(false)
  /* 各资源类型的生成进度（生成中 · N% · 当前阶段 + 进度条）——复用文档学习的
     阶段化推进共享原语，驱动 hub 速览卡 / 资源中枢卡片与勾选清单的进度反馈。 */
  const { progress: genProg, startProgress, stopProgress } = useStagedProgress<GenType>()
  /* 有序学习「上次学习进度」：6 步过程完成数（getSteps，mock/联调同源，非写死）。 */
  const [stepProg, setStepProg] = useState<{ done: number; total: number }>({ done: 0, total: 6 })

  const toggleGen = (t: GenType) =>
    setGenPicked((prev) => {
      const next = new Set(prev)
      next.has(t) ? next.delete(t) : next.add(t)
      return next
    })

  /* 生成派生态：未发起批量生成 → 卡片全部可浏览（不回归既有落点/回看入口）；
     发起批量后未完成项锁定，完成项解锁；进度仅按勾选项统计。
     注意锁定只认 genBatchStarted（批量），单项「立即查看」按需生成不触发锁定。 */
  const genLocked = (t: GenType) => genBatchStarted && genStatus[t] !== 'done'
  const genChosen = GEN_OPTIONS.filter((o) => genPicked.has(o.type))
  const genTotal = genChosen.length
  const genDone = genChosen.filter((o) => genStatus[o.type] === 'done' || genStatus[o.type] === 'error').length
  const genProgress = genTotal ? Math.round((genDone / genTotal) * 100) : 0

  /* 联调数据源（mock 模式下不使用，保持现有常量驱动）：
     questions 来自 GET /quiz/{kp}（联调初值为空，避免拉取期间闪现 nn 演示题）；
     lectureMap 缓存各难度档 markdown */
  const [questions, setQuestions] = useState<QuizQuestion[]>(USE_REAL_API ? [] : mockRes.quiz)
  const [lectureMap, setLectureMap] = useState<Record<string, string>>({})
  /* 知识图解 Mermaid：联调按当前 kpId 经后端 LLMClient 真实生成；mock 用本地常量 */
  const [diagramChart, setDiagramChart] = useState<string>('')
  /* B10：各难度档讲义的真实溯源 sources 与产出工作流 id（联调用，驱动溯源徽章/「查看生成过程」）*/
  const [lectureSources, setLectureSources] = useState<Record<string, SourceRef[]>>({})
  const [lectureWf, setLectureWf] = useState<Record<string, string | null>>({})
  /* B10：讲义「重新生成」闭环（仅联调）：确认弹层 / 实时进行中 / 迷你进度阶段文案 */
  const [regenConfirm, setRegenConfirm] = useState(false)
  const [regenRunning, setRegenRunning] = useState(false)
  const [regenPhase, setRegenPhase] = useState('诊断')
  const regenSocketRef = useRef<{ close: () => void } | null>(null)
  /* B-3 卡住信号：讲义选中即问的监听容器 + 同一内容反复浏览的计数 */
  const lectureRef = useRef<HTMLDivElement>(null)
  const openCountsRef = useRef<Record<string, number>>({})

  /* 联调：把一份讲义回包写入三个缓存映射（markdown / sources / workflowId） */
  const applyLecture = (lv: string, d: LectureData) => {
    setLectureMap((m) => ({ ...m, [lv]: d.markdown }))
    setLectureSources((m) => ({ ...m, [lv]: toSourceRefs(d.sources) }))
    setLectureWf((m) => ({ ...m, [lv]: d.workflowId }))
  }

  /* 知识点闭环状态：完成以"通过分阶测试(≥60%)"为唯一判定 */
  const kpStatus = useMastery((s) => s.status[kpId] ?? 'learning')
  const goCheck = useMastery((s) => s.goCheck)
  const markPassed = useMastery((s) => s.markPassed)
  const loadMastery = useMastery((s) => s.load)
  /* 名称解析覆盖 78 点全体系：6 核心走既有注册表，非核心目录点回退体系种子（按需生成开通） */
  const kpName = kpById(kpId)?.name ?? ksPointById(kpId)?.name ?? '当前知识点'

  /* 声明当前辅导上下文 → 供 App 顶层全局 dock / 选中即问就该知识点发起辅导。 */
  useEffect(() => {
    setTutorContext({ kpId, kpName })
  }, [kpId, kpName])

  /* 单类型生成：复用既有单类型生成接口（types 传单元素逐次调用，契约不变）。
     mock 模式内容为本地确定性常量，仅以短延时模拟「生成中」过程；external 走 fetchRecommendations。 */
  const generateOne = async (t: GenType): Promise<void> => {
    if (USE_REAL_API) {
      switch (t) {
        case 'lecture':
        case 'mindmap': {
          // 思维导图由讲义 markdown 实时结构化得到 → 复用既有讲义生成接口
          const d = await getLecture(kpId, level)
          applyLecture(level, d)
          // 讲义由后端 /resource/lecture 旁路埋点；思维导图无独立后端生成接口 → 前端补埋（「我的资源库」）
          if (t === 'mindmap') await logResourceGeneration({ kpId, kind: 'mindmap' })
          break
        }
        case 'video':
          await getVideo(kpId, level) // 既有单类型视频接口（打开 VideoLecture 时复用同源数据）
          break
        case 'diagram': {
          const d = await getDiagram(kpId)
          setDiagramChart(d.mermaid)
          break
        }
        case 'external': {
          // 与资源推荐弹层共享同一会话级缓存/在途请求（services/resource）：
          // 「立即查看」预生成一次后，弹层打开直接复用，不再触发第二轮联网聚合。
          const snap = await loadAggregateCached(kpId)
          setRecoCount(snap.items.length)
          break
        }
        case 'code':
          // 代码实操为浏览器内可运行示例（CodeSandbox），无单独生成接口，直接就绪；前端补埋（「我的资源库」）
          await logResourceGeneration({ kpId, kind: 'code' })
          break
      }
      return
    }
    // mock：内容为本地常量，短延时模拟逐项生成过程（时长仅够阶段化进度走过 2-3 段，视频稍长）
    if (t === 'external') {
      const items = await fetchRecommendations(kpId, kpName, kpName)
      setRecoCount(items.length)
      return
    }
    await new Promise((r) => window.setTimeout(r, t === 'video' ? 2600 : 1400))
  }

  /* 带阶段化进度的单类型生成：hub「立即查看」与批量逐项生成共用（进度条与文档学习页同一套体验）。 */
  const generateOneWithProgress = async (t: GenType): Promise<void> => {
    startProgress(t, stagesFor(t), { slow: t === 'video' })
    try {
      await generateOne(t)
      stopProgress(t, true)
    } catch (e) {
      stopProgress(t, false)
      throw e
    }
  }

  /* 逐项生成：勾选项 pending→running→done/error 逐个推进，生成完一项即解锁对应卡片；
     未勾选项标记为「待生成」（不生成、卡片锁定），实现「未勾选的不生成」。 */
  const runGeneration = async () => {
    if (!genPicked.size || genRunning) return
    const chosen = GEN_OPTIONS.filter((o) => genPicked.has(o.type)).map((o) => o.type)
    setGenRunning(true)
    setGenBatchStarted(true)
    setGenStatus(Object.fromEntries(GEN_OPTIONS.map((o) => [o.type, 'pending'])) as Record<GenType, ItemStatus>)
    if (genPicked.has('external')) setRecoCount(null)
    for (const t of chosen) {
      setGenStatus((prev) => ({ ...prev, [t]: 'running' }))
      try {
        await generateOneWithProgress(t)
        setGenStatus((prev) => ({ ...prev, [t]: 'done' }))
      } catch (e) {
        console.error(`[resource] 生成「${t}」失败`, e)
        setGenStatus((prev) => ({ ...prev, [t]: 'error' }))
      }
    }
    setGenRunning(false)
  }

  /* 有序学习「上次学习进度」：拉取 6 步过程完成数（mock/联调同源，随知识点变化，非写死）。 */
  useEffect(() => {
    let cancelled = false
    getSteps(kpId)
      .then((s) => { if (!cancelled) setStepProg({ done: s.completedCount, total: s.total }) })
      .catch((e) => console.error('[resource] 加载学习进度失败', e))
    return () => { cancelled = true }
  }, [kpId, view])

  /* 联调初始化：拉取后端测验题 + 当前难度讲义 + 刷新掌握度（mock 模式跳过）*/
  useEffect(() => {
    if (!USE_REAL_API) return
    loadMastery()
    getQuiz(kpId)
      .then((d) => setQuestions(d.questions))
      .catch((e) => console.error('[resource] 加载测验题失败', e))
    getLecture(kpId, level)
      .then((d) => applyLecture(level, d))
      .catch((e) => console.error('[resource] 加载讲义失败', e))
    getDiagram(kpId)
      .then((d) => setDiagramChart(d.mermaid))
      .catch((e) => console.error('[resource] 加载知识图解失败', e))
    // 仅挂载时执行一次（level 初值为「初级」；kpId 挂载期内不变）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* 卸载清理：断开可能在跑的重新生成 WS（不触发回退） */
  useEffect(() => () => regenSocketRef.current?.close(), [])

  /* 当前展示的讲义内容：联调取后端缓存，mock 取本地三档常量 */
  const activeLecture = USE_REAL_API
    ? lectureMap[level] ?? ''
    : mockRes.lectureByLevel[MOCK_LECTURE_LEVEL[level]]

  /* 思维导图：联调由当前讲义 markdown 实时结构化（标题大纲，与"从讲义结构化得到"的
     设计一致；后端 8.4 导图接口未实现，讲义已按 kpId 请求故大纲随 kpId 变化）；
     mock 保持本地大纲常量 */
  const activeMindmap = USE_REAL_API ? lectureOutline(activeLecture) : mockRes.mindmap

  /* 讲义导出（纯前端，导出"当前已渲染"的内容，不重新请求生成）。文件名：讲义-知识点-难度 */
  const exportBaseName = `讲义-${kpName}-${level}`
  const handleExportMarkdown = () => {
    if (!activeLecture) return
    exportLectureMarkdown(activeLecture, exportBaseName)
  }
  const handleExportPdf = () => {
    const body = lectureRef.current?.querySelector('.markdown-body') as HTMLElement | null
    if (!body) return
    const meta = `${kpName} · 难度：${level} · 导出于 ${new Date().toLocaleString('zh-CN')}`
    const ok = exportLectureToPdf(body, exportBaseName, meta)
    if (!ok) window.alert('浏览器拦截了打印窗口，请允许本站弹出窗口后重试导出 PDF。')
  }

  const handleQuizResult = (
    wrong: QuizQuestion[],
    submitted?: boolean,
    answers?: Record<string, string | string[]>
  ) => {
    setWrongQs(wrong)
    if (!submitted) {
      setQuizGrade(null) // 重做：清空上次综合判分
      return
    }
    // B-3 卡住信号：错题较多即视为强卡顿信号（达阈值触发主动关怀，限频见 StuckNudge）
    if (wrong.length >= 3) reportStuck(kpId, 2)
    if (USE_REAL_API) {
      // 提交作答给后端判分（客观题 + 简答 AI 评分）；≥60 后端联动置 passed
      submitQuiz(kpId, answers ?? {})
        .then((r) => {
          setQuizGrade({ score: r.score, passed: r.passed, shortAnswers: r.shortAnswers })
          if (!r.passed) reportStuck(kpId, 2)
          loadMastery()
        })
        .catch((e) => console.error('[resource] 提交测验失败', e))
    } else {
      // mock：本地综合判分（客观 + 简答确定性评分），≥60 点亮已掌握
      const grade = gradeQuizLocally(questions, answers ?? {})
      setQuizGrade(grade)
      if (grade.passed) markPassed(kpId)
      else reportStuck(kpId, 2)
    }
  }

  /* 难度自适应：切换难度 → Agent 实时再生成讲义（mock 模拟 1.1s；联调请求后端）*/
  const changeLevel = (lv: (typeof LEVELS)[number]) => {
    if (lv === level || regenerating) return
    setRegenerating(true)
    if (USE_REAL_API) {
      getLecture(kpId, lv)
        .then((d) => {
          applyLecture(lv, d)
          setLevel(lv)
          setRegenerating(false)
        })
        .catch((e) => {
          console.error('[resource] 切换难度重生成失败', e)
          setLevel(lv)
          setRegenerating(false)
        })
      return
    }
    window.setTimeout(() => {
      setLevel(lv)
      setRegenerating(false)
    }, 1100)
  }

  /* ---------- B10：讲义「重新生成」闭环（仅联调；mock 模式不渲染入口） ---------- */

  /** 重新生成完成后轮询讲义直至命中本次工作流产物（后台 complete 后才提交回写）。 */
  const finishRegen = async (wfId: string) => {
    for (let i = 0; i < 15; i++) {
      try {
        const d = await getLecture(kpId, level)
        if (d.workflowId === wfId || i === 14) {
          applyLecture(level, d)
          break
        }
      } catch (e) {
        console.error('[resource] 重新生成后刷新讲义失败', e)
        break
      }
      await new Promise((r) => window.setTimeout(r, 400))
    }
    setRegenRunning(false)
  }

  /** 确认后触发：11.1 execute（当前 kp+难度）→ 11.2 WS 迷你进度 → complete 刷新讲义。 */
  const confirmRegen = async () => {
    setRegenConfirm(false)
    setRegenRunning(true)
    setRegenPhase('诊断')
    let wfId: string
    try {
      wfId = (await executeWorkflow({ kpId, difficulty: level })).workflowId
    } catch (e) {
      console.error('[resource] 触发重新生成失败', e)
      setRegenRunning(false)
      return
    }
    regenSocketRef.current = connectWorkflowSocket(wfId, {
      onFrame: (snap) => setRegenPhase(REGEN_PHASE_LABEL[snap.phase] ?? snap.phase),
      onComplete: () => {
        regenSocketRef.current = null
        void finishRegen(wfId)
      },
      onFail: (reason) => {
        console.error('[resource] 重新生成实时通道异常：', reason)
        regenSocketRef.current = null
        setRegenRunning(false)
      },
    })
  }

  /** 跳转大屏回放产出当前讲义的工作流（查看生成过程）。 */
  const viewGenerationProcess = () => {
    const wf = lectureWf[level]
    if (!wf) return
    setWorkflowReplay(wf)
    onNavigate?.('workflow')
  }

  /* ---------- 资源详情过场：打开 / 关闭 + Esc + 焦点管理 + 滚动锁 ---------- */
  const ILLUSTRATED = new Set<Tab>(['lecture', 'video', 'mindmap', 'diagram', 'code', 'external'])
  /* 实际打开某内容详情（含检验前置 + 卡住信号埋点）。不做锁定判断，供 openCard/viewResource 复用。 */
  const doOpen = (id: Tab) => {
    // 进入「分阶测试」沿用既有检验前置：未掌握时置 pending-check（passed 不回退）
    if (id === 'quiz' && kpStatus === 'learning') goCheck(kpId)
    setOpenId(id)
    // B-3 卡住信号：同一讲解类内容反复打开（第 3 次）→ 视为「反复看同一点」的卡顿
    const n = (openCountsRef.current[id] = (openCountsRef.current[id] ?? 0) + 1)
    if (n >= 3 && (id === 'lecture' || id === 'diagram' || id === 'video')) reportStuck(kpId, 2)
  }
  const openCard = (id: Tab) => {
    // 选择性（批量）生成开启后：未生成完成的资源卡锁定，不可打开（quiz/tutor 不受逐项生成约束）
    if (id !== 'quiz' && id !== 'tutor' && genLocked(id)) return
    doOpen(id)
  }
  /* hub 首屏「立即查看」= 按需生成单项（只生成点的那一项，不强制预生成其它），完成即打开。
     已生成则直接打开；生成中重复点忽略。复用既有单类型生成 generateOne（契约不变）。 */
  const viewResource = async (t: GenType) => {
    const tab = t as Tab
    const st = genStatus[t]
    if (st === 'done') { doOpen(tab); return }
    if (st === 'running') return
    setGenStatus((prev) => ({ ...prev, [t]: 'running' }))
    try {
      await generateOneWithProgress(t)
      setGenStatus((prev) => ({ ...prev, [t]: 'done' }))
      doOpen(tab)
    } catch (e) {
      console.error(`[resource] 立即查看·按需生成「${t}」失败`, e)
      setGenStatus((prev) => ({ ...prev, [t]: 'error' }))
    }
  }
  const closeCard = () => setOpenId(null)

  /* 打开详情时：Esc 关闭、锁背景滚动、关闭后焦点回到触发元素 */
  useEffect(() => {
    if (!openId) return
    const prev = document.activeElement as HTMLElement | null
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpenId(null)
    }
    window.addEventListener('keydown', onKey)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
      prev?.focus?.()
    }
  }, [openId])

  /* 导学对话·醒目入口横幅（苏格拉底式启发导学是本产品核心方法论，不再埋在底部工具行）：
     hub 速览与资源中枢均以整幅横幅呈现，点击直接打开导学对话。 */
  const tutorBanner = (
    <button type="button" className="tutor-banner" onClick={() => doOpen('tutor')}>
      <span className="tutor-banner__icon" aria-hidden="true">💬</span>
      <span className="tutor-banner__body">
        <span className="tutor-banner__title">
          AI 导师 · 导学对话
          <em className="tutor-banner__tag">苏格拉底式启发引导</em>
        </span>
        <span className="tutor-banner__desc">
          不直接给答案——通过一步步追问，引导你自己想通「{kpName}」。学讲义、看视频卡住时，随时开聊。
        </span>
      </span>
      <span className="tutor-banner__cta">开始导学 →</span>
    </button>
  )

  /* 单个内容的真实渲染（复用既有 /resource/* 数据与多模态组件）*/
  const renderBody = (id: Tab) => {
    switch (id) {
      case 'lecture':
        return (
          <>
            {/* 导学对话召唤条（讲义顶部）：学讲义时明显可见「有 AI 导师可启发式对话」 */}
            <button type="button" className="lecture-tutor-cue" onClick={() => doOpen('tutor')}>
              <span className="lecture-tutor-cue__icon" aria-hidden="true">💬</span>
              <span className="lecture-tutor-cue__text">
                <strong>AI 导师 · 苏格拉底式导学</strong>——不直接给答案，用追问引导你自己想通；学到哪儿卡住，随时切进对话。
              </span>
              <span className="lecture-tutor-cue__cta">开始导学 →</span>
            </button>

            {/* 难度自适应再生成（讲义专属操作，归位到讲义详情）*/}
            <div className="level-switch">
              <span className="level-switch__label">难度自适应：</span>
              <div className="level-switch__seg">
                {LEVELS.map((lv) => (
                  <button
                    key={lv}
                    className={`level-switch__btn ${level === lv ? 'level-switch__btn--active' : ''}`}
                    onClick={() => changeLevel(lv)}
                    disabled={regenerating}
                  >
                    {lv}
                  </button>
                ))}
              </div>
              <span className="level-switch__hint">切换难度，AI 实时重生成讲义</span>
            </div>

            {/* B10：本档讲义产出工作流（仅联调）——查看生成过程 / 重新生成进行中提示 */}
            {USE_REAL_API && regenRunning && (
              <div className="lecture-regen-bar">
                <span className="lecture-regen-bar__progress">
                  <span className="lecture-regen-bar__orb" />
                  多智能体工作流 · {regenPhase}…
                </span>
              </div>
            )}
            {USE_REAL_API && !regenRunning && lectureWf[level] && (
              <div className="lecture-regen-bar">
                <button type="button" className="lecture-regen-bar__link" onClick={viewGenerationProcess}>
                  查看生成过程 →
                </button>
              </div>
            )}

            {/* 讲义导出：导出当前已渲染内容，不重新生成。.md 原文最稳；.pdf 经浏览器打印另存 */}
            <div className="lecture-export">
              <span className="lecture-export__label">导出讲义：</span>
              <button
                type="button"
                className="lecture-export__btn"
                onClick={handleExportMarkdown}
                disabled={!activeLecture}
                title="下载讲义 Markdown 原文（.md）"
              >
                <span aria-hidden="true">⬇</span> Markdown
              </button>
              <button
                type="button"
                className="lecture-export__btn lecture-export__btn--pdf"
                onClick={handleExportPdf}
                disabled={!activeLecture}
                title="导出为 PDF（经浏览器打印 → 另存为 PDF，保留公式/图解/表格/图片）"
              >
                <span aria-hidden="true">🖨</span> PDF
              </button>
            </div>

            <div className="lecture-body" ref={lectureRef}>
              {/* B-2：选中即问已升为全局能力（见 App 顶层 GlobalTutorLayer），此处无需再单接。
                  论文级 RAG 溯源：正文相应段落织入 [1][2] 上标引用、末尾汇聚成学术「参考文献」。*/}
              {activeLecture ? (
                <MarkdownRenderer content={activeLecture} sources={lectureSources[level] ?? defaultSources} />
              ) : (
                <div className="resource-loading">「{kpName}」的定制讲义生成中，请稍候…</div>
              )}
              <AnimatePresence>
                {regenerating && (
                  <motion.div
                    className="lecture-regen"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                  >
                    <div className="lecture-regen__orb" />
                    <span>领域知识生成 Agent 正在按新难度重生成讲义…</span>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </>
        )
      case 'video':
        return (
          <Suspense fallback={<Loading />}>
            <div className="resource-modal-hint">AI 生成的讲解视频（Remotion 渲染）+ 同步旁白，点击播放：</div>
            <VideoLecture difficulty={level} />
          </Suspense>
        )
      case 'mindmap':
        return (
          <Suspense fallback={<Loading />}>
            <div className="resource-modal-hint">AI 已将讲义结构化为知识脉络图，可缩放/拖拽：</div>
            {activeMindmap ? (
              <MindMap markdown={activeMindmap} downloadName={`思维导图-${kpName}`} />
            ) : (
              <div className="resource-loading">「{kpName}」的讲义生成后将自动结构化为思维导图…</div>
            )}
          </Suspense>
        )
      case 'code': {
        const codeDemo = kpCodeDemo(kpId)
        return (
          <Suspense fallback={<Loading />}>
            <div className="resource-modal-hint">{codeDemo.hint}</div>
            <CodeSandbox js={codeDemo.js} baseName={`代码-${kpName}`} />
          </Suspense>
        )
      }
      case 'diagram':
        return (
          <Suspense fallback={<Loading />}>
            <div className="resource-modal-hint">「{kpName}」知识脉络图（AI 按当前主题生成，可缩放/拖拽）：</div>
            {USE_REAL_API && !diagramChart ? (
              <div className="resource-loading">「{kpName}」的知识图解生成中，请稍候…</div>
            ) : (
              <MermaidDiagram chart={USE_REAL_API ? diagramChart : mockRes.diagram} downloadName={`图解-${kpName}`} />
            )}
          </Suspense>
        )
      case 'external':
        return (
          <Suspense fallback={<Loading />}>
            <ResourceAggregator />
          </Suspense>
        )
      case 'tutor':
        return (
          <Suspense fallback={<Loading />}>
            <SocraticTutor />
          </Suspense>
        )
      case 'quiz':
        return (
          <>
            <div className="resource-modal-hint">
              通过即点亮「已掌握」并推进进度（综合 ≥ 70 分判定通过）。
            </div>
            {questions.length > 0 ? (
              <QuizRenderer questions={questions} onSubmitResult={handleQuizResult} grade={quizGrade} passMark={70} />
            ) : kpById(kpId) ? (
              <div className="resource-loading">「{kpName}」的分阶测试题准备中，请稍候…</div>
            ) : (
              <div className="resource-loading">
                「{kpName}」为体系拓展点，分阶测试题库暂未覆盖——可先通过讲义 / 图解 / 视频等按需生成的资源学习，测试将随题库扩充开放。
              </div>
            )}
            {wrongQs.length > 0 && <WeakPointReinforce wrong={wrongQs} />}
          </>
        )
    }
  }

  /* hub 首屏派生展示值（全部接真实态，非写死）：
     - flowPct   有序学习进度%（6 步过程完成占比）
     - genDoneCount 已按需/批量生成完成的资源类数
     - ragCount  RAG 引用文档数（联调取当前讲义真实溯源条数；mock 取讲义溯源实际列出条数） */
  const flowPct = stepProg.total ? Math.round((stepProg.done / stepProg.total) * 100) : 0
  const genDoneCount = GEN_OPTIONS.filter((o) => genStatus[o.type] === 'done').length
  const ragCount = USE_REAL_API ? lectureSources[level]?.length ?? null : defaultSources.length

  return (
    <div className="resource-page">
      {/* 统一标题区：一句话说明 + 右上「已校验·幻觉率<5%」可信度徽章（可点查看机制）。
          原来的只读状态徽章组下沉到瘦身「状态信息条」，标题区不再占大块。 */}
      <PageHeader
        title="个性化学习资源"
        highlight="学习资源"
        subtitle="AI 多模态资源包 · 讲义 / 思维导图 / 代码 / 图解 / 测试 · 讲义按难度自适应生成"
        crumb="学习资源"
        onBack={() => onNavigate?.('dashboard')}
        actions={
          <button
            type="button"
            className={`resource-trust ${trustOpen ? 'resource-trust--open' : ''}`}
            onClick={() => setTrustOpen((o) => !o)}
            aria-expanded={trustOpen}
          >
            <svg className="resource-trust__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2 4 5v6c0 5 3.4 8.5 8 10 4.6-1.5 8-5 8-10V5l-8-3z" />
              <path d="m9 12 2 2 4-4" />
            </svg>
            <span className="resource-trust__main">
              <span className="resource-trust__value">已校验 · 幻觉率&lt;5%</span>
              <span className="resource-trust__label">可信度 · 点击查看机制</span>
            </span>
          </button>
        }
      />

      {trustOpen && (
        <div className="resource-trust-detail">
          <strong>多智能体交叉校验</strong>：领域知识生成 Agent 产出内容后，由内容审核 Agent 基于 RAG 知识库逐条核对术语与事实，一旦发现幻觉即打回重生成（最多 3 轮），最终幻觉率压至 <strong>&lt; 5%</strong>，确保你看到的讲义可信、可溯源。
        </div>
      )}

      {/* ===== hub 资源总览首屏：两大操作卡 + 瘦身状态条 + 六类速览 + 阶段测试条（核心操作优先） ===== */}
      {view === 'hub' && (
        <RevealGroup className="res-hub">
          {/* 主操作区：两个大卡片并列，首屏突出、无需滚动即可操作 */}
          <RevealItem className="res-hero-grid">
            <button type="button" className="res-hero res-hero--flow" onClick={() => setView('flow')}>
              <span className="res-hero__tag res-hero__tag--rec">推荐</span>
              <span className="res-hero__body">
                <span className="res-hero__title">有序学习</span>
                <span className="res-hero__desc">费曼讲解 + 康奈尔笔记，按最优顺序把「{kpName}」一步步学透。</span>
                <span className="res-hero__foot">
                  <span className="res-hero__prog">
                    <span className="res-hero__prog-bar">
                      <span className="res-hero__prog-fill" style={{ width: `${flowPct}%` }} />
                    </span>
                    <span className="res-hero__prog-text">
                      {flowPct > 0 ? `上次学到 ${stepProg.done}/${stepProg.total} 步 · ${flowPct}%` : '尚未开始 · 从第 1 步开始'}
                    </span>
                  </span>
                  <span className="res-hero__cta">继续学习 →</span>
                </span>
              </span>
              <span className="res-hero__art" aria-hidden="true"><HeroFlowArt /></span>
            </button>

            <button type="button" className="res-hero res-hero--browse" onClick={() => setView('browse')}>
              <span className="res-hero__tag res-hero__tag--all">全部资源</span>
              <span className="res-hero__body">
                <span className="res-hero__title">资源中枢</span>
                <span className="res-hero__desc">按需生成 6 类多模态资源，讲义 / 视频 / 导图 / 图解 / 代码 / 推荐自由浏览。</span>
                <span className="res-hero__foot">
                  <span className="res-hero__stat">
                    <b>{HUB_CARDS.length}</b> 类资源可用{genDoneCount > 0 ? ` · 已生成 ${genDoneCount}` : ''}
                  </span>
                  <span className="res-hero__cta">进入资源库 →</span>
                </span>
              </span>
              <span className="res-hero__art" aria-hidden="true"><HeroBrowseArt /></span>
            </button>
          </RevealItem>

          {/* 状态信息条（瘦身）：只读状态压成一行紧凑信息条，小尺寸、不占主视觉 */}
          <RevealItem className="res-statusbar">
            <span className="res-statusbar__item">
              <b className="res-statusbar__v">{kpName}</b>
              <i className="res-statusbar__k">当前知识点</i>
            </span>
            <span className={`res-statusbar__item res-statusbar__item--${kpStatus}`}>
              <b className="res-statusbar__v">{STATUS_LABEL[kpStatus]}</b>
              <i className="res-statusbar__k">状态</i>
            </span>
            <span className="res-statusbar__item">
              <b className="res-statusbar__v">{level}</b>
              <i className="res-statusbar__k">讲义难度</i>
            </span>
            <span className="res-statusbar__item">
              <b className="res-statusbar__v">{ragCount ?? '…'}</b>
              <i className="res-statusbar__k">RAG 引用</i>
            </span>
            <span className="res-statusbar__item res-statusbar__item--safe">
              <b className="res-statusbar__v">&lt;5%</b>
              <i className="res-statusbar__k">幻觉率</i>
            </span>
          </RevealItem>

          {/* 核心资源速览：六类资源卡片网格，每个「立即查看」= 按需生成后打开（未点的不生成） */}
          <RevealItem className="res-hub-head">
            <span className="res-hub-head__title">核心资源速览</span>
            <span className="res-hub-head__sub">点「立即查看」按需生成对应资源，未点的不生成</span>
          </RevealItem>
          <RevealItem className="rescard-grid">
            {HUB_CARDS.map((c) => {
              const st = genStatus[c.id]
              const busy = st === 'running'
              const pr = genProg[c.id]
              const pct = Math.round(pr?.pct ?? 0)
              return (
                <motion.button
                  key={c.id}
                  type="button"
                  className={`rescard ${busy ? 'rescard--busy' : ''}`}
                  onClick={() => void viewResource(c.id)}
                  disabled={busy}
                  whileHover={busy ? undefined : { y: -5 }}
                  whileTap={busy ? undefined : { scale: 0.98 }}
                >
                  <span className="rescard__illu" style={{ background: RESOURCE_META[c.id].theme }}>
                    <ResourceIllustration type={c.id} />
                  </span>
                  <span className="rescard__meta">
                    <span className="rescard__title">
                      {c.title}
                      {st && <StatusChip status={st} />}
                    </span>
                    <span className="rescard__desc">
                      {c.id === 'external' && recoCount != null
                        ? `AI 已联网聚合 ${recoCount} 条优质外部资源，可打开学习。`
                        : c.desc}
                    </span>
                    <span className="rescard__cta">
                      {busy && pr
                        ? `生成中 · ${pct}% · ${pr.stage}`
                        : st === 'done' ? '查看 →' : '立即查看 →'}
                    </span>
                  </span>
                  {busy && (
                    <span className="rescard__progress" aria-hidden="true">
                      <span className="rescard__progress-fill" style={{ width: `${pct}%` }} />
                    </span>
                  )}
                </motion.button>
              )
            })}
          </RevealItem>

          {/* 导学对话·醒目主入口（苏格拉底式启发导学）：与资源速览并列的主功能，不埋在工具行 */}
          <RevealItem>{tutorBanner}</RevealItem>

          {/* 底部：阶段测试说明条（走完各资源后在有序学习末尾解锁、点亮「已掌握」推进进度） */}
          <RevealItem className="browse-hint">
            <span className="browse-hint__icon">🏁</span>
            <span className="browse-hint__text">
              阶段测试在「<button type="button" className="browse-hint__link" onClick={() => setView('flow')}>🧭 有序学习</button>」流程末尾——
              走完视频 / 讲义 / 图解 / 笔记 / 费曼 / 实操后自动解锁，通过即点亮「已掌握」并推进进度。
            </span>
          </RevealItem>
        </RevealGroup>
      )}

      {/* ===== flow 有序学习：内容/功能与原「有序学习」一致；顶部加「返回资源总览」 ===== */}
      {view === 'flow' && (
        <RevealGroup>
          <RevealItem>
            <button type="button" className="res-back" onClick={() => setView('hub')}>← 资源总览</button>
          </RevealItem>
          <RevealItem className="resource-card">
            <LearningFlow
              kpId={kpId}
              kpName={kpName}
              level={level}
              lectureMarkdown={activeLecture}
              lectureSources={lectureSources[level]}
              diagramChart={USE_REAL_API ? diagramChart : mockRes.diagram}
              questions={questions}
              kpStatus={kpStatus}
              quizGrade={quizGrade}
              onQuizResult={handleQuizResult}
              onGoCheck={() => goCheck(kpId)}
              onReview={handleReview}
              onNavigate={onNavigate}
            />
          </RevealItem>
        </RevealGroup>
      )}

      {/* ===== browse 资源中枢：内容/功能与原「资源中枢」一致；顶部加「返回资源总览」 ===== */}
      {view === 'browse' && (
        <RevealGroup>
          <RevealItem>
            <button type="button" className="res-back" onClick={() => setView('hub')}>← 资源总览</button>
          </RevealItem>
          {/* 选择性逐项生成面板（复用会话二 TutorResourcePanel 的勾选/进度/状态 UI 与样式）*/}
          <RevealItem className="rescard-genpanel">
            <div className="trp trp--embed">
              <div className="trp__head">
                <span className="trp__badge">✦ 资源生成</span>
                <span className="trp__point">
                  勾选要生成的学习资源，<strong>逐项生成</strong> —— 生成完一项即解锁对应卡片
                </span>
              </div>
              <p className="trp__hint">按需选择，未勾选的不生成；「资源推荐」为 AI 联网聚合，与讲义/视频/图解并列：</p>

              <div className="trp__list rescard-genpanel__list">
                {GEN_OPTIONS.map((o) => {
                  const pr = genProg[o.type]
                  const busy = genStatus[o.type] === 'running'
                  const pct = Math.round(pr?.pct ?? 0)
                  return (
                    <label key={o.type} className={`trp__item ${genPicked.has(o.type) ? 'is-picked' : ''}`}>
                      <input
                        type="checkbox"
                        checked={genPicked.has(o.type)}
                        disabled={genRunning}
                        onChange={() => toggleGen(o.type)}
                      />
                      <span className="trp__item-icon">
                        {o.type === 'external' ? GEN_ICON.external : <ResourceTypeIcon kind={o.type} size={26} />}
                      </span>
                      <span className="trp__item-body">
                        <span className="trp__item-title">{o.title}</span>
                        {/* 生成中：期望文案切换为「N% · 当前阶段」+ 细进度条（与文档学习同一套进度体验） */}
                        {busy && pr ? (
                          <span className="trp__item-progressline">
                            <span className="trp__item-progressbar" aria-hidden="true">
                              <span className="trp__item-progressbar-fill" style={{ width: `${pct}%` }} />
                            </span>
                            <span className="trp__item-progresstext">{pct}% · {pr.stage}</span>
                          </span>
                        ) : (
                          <span className="trp__item-expect">{o.expect}</span>
                        )}
                      </span>
                      {genStatus[o.type] && <StatusChip status={genStatus[o.type]!} />}
                    </label>
                  )
                })}
              </div>

              <button className="trp__gen" disabled={!genPicked.size || genRunning} onClick={() => void runGeneration()}>
                {genRunning
                  ? `正在逐项生成…（${genDone}/${genTotal}）`
                  : genBatchStarted
                    ? `重新生成所选（${genPicked.size}）`
                    : `生成所选（${genPicked.size}）`}
              </button>

              {genBatchStarted && (
                <div
                  className="trp__progress"
                  role="progressbar"
                  aria-valuenow={genProgress}
                  aria-valuemin={0}
                  aria-valuemax={100}
                >
                  <span className="trp__progress-fill" style={{ width: `${genProgress}%` }} />
                  <span className="trp__progress-text">
                    {genRunning ? `生成中 ${genDone}/${genTotal}` : `已完成 ${genDone}/${genTotal}`}
                  </span>
                </div>
              )}
            </div>
          </RevealItem>

          {/* 导学对话·醒目主入口（苏格拉底式启发导学）：与资源卡并列的主操作，置于内容区顶部 */}
          <RevealItem>{tutorBanner}</RevealItem>

          {/* 学习内容 → 插画卡片网格（点击走 layoutId 过场展开详情；生成完成才解锁）*/}
          <RevealItem className="rescard-grid">
            {RESOURCE_CARDS.map((c) => {
              const st = genStatus[c.id]
              const locked = genLocked(c.id)
              const pr = genProg[c.id]
              const busy = st === 'running'
              const pct = Math.round(pr?.pct ?? 0)
              return (
                <motion.button
                  key={c.id}
                  type="button"
                  className={`rescard ${locked ? 'rescard--locked' : ''} ${busy ? 'rescard--busy' : ''}`}
                  onClick={() => openCard(c.id)}
                  disabled={locked}
                  whileHover={locked ? undefined : { y: -5 }}
                  whileTap={locked ? undefined : { scale: 0.98 }}
                >
                  <motion.span
                    className="rescard__illu"
                    layoutId={`res-illu-${c.id}`}
                    style={{ background: RESOURCE_META[c.id].theme }}
                  >
                    <ResourceIllustration type={c.id} />
                  </motion.span>
                  <span className="rescard__meta">
                    <span className="rescard__title">
                      {c.title}
                      {st && <StatusChip status={st} />}
                    </span>
                    <span className="rescard__desc">
                      {busy && pr ? `生成中 · ${pct}% · ${pr.stage}` : c.desc}
                    </span>
                  </span>
                  {busy && (
                    <span className="rescard__progress" aria-hidden="true">
                      <span className="rescard__progress-fill" style={{ width: `${pct}%` }} />
                    </span>
                  )}
                </motion.button>
              )
            })}

            {/* 资源推荐并入成果区：作为第 6 张卡片与讲义/视频/图解并列，可打开 */}
            {(() => {
              const st = genStatus.external
              const locked = genLocked('external')
              const pr = genProg.external
              const busy = st === 'running'
              const pct = Math.round(pr?.pct ?? 0)
              return (
                <motion.button
                  key="external"
                  type="button"
                  className={`rescard ${locked ? 'rescard--locked' : ''} ${busy ? 'rescard--busy' : ''}`}
                  onClick={() => openCard('external')}
                  disabled={locked}
                  whileHover={locked ? undefined : { y: -5 }}
                  whileTap={locked ? undefined : { scale: 0.98 }}
                >
                  <motion.span
                    className="rescard__illu"
                    layoutId="res-illu-external"
                    style={{ background: RESOURCE_META.external.theme }}
                  >
                    <ResourceIllustration type="external" />
                  </motion.span>
                  <span className="rescard__meta">
                    <span className="rescard__title">
                      资源推荐
                      {st && <StatusChip status={st} />}
                    </span>
                    <span className="rescard__desc">
                      {busy && pr
                        ? `生成中 · ${pct}% · ${pr.stage}`
                        : recoCount != null
                          ? `AI 已联网聚合 ${recoCount} 条优质外部资源，可打开学习。`
                          : 'AI 联网聚合的优质外部资源（视频/课程/论文/文档），可直接打开。'}
                    </span>
                  </span>
                  {busy && (
                    <span className="rescard__progress" aria-hidden="true">
                      <span className="rescard__progress-fill" style={{ width: `${pct}%` }} />
                    </span>
                  )}
                </motion.button>
              )
            })()}
          </RevealItem>

          {/* 阶段测试不再挂在资源中枢——它是有序学习流唯一的「终点 gate」（见 LearningFlow）。
             资源中枢仅用于自由浏览各形态资源，避免「看视频时却显示测试已完成」的跨资源串显。
             去测试请切到「🧭 有序学习」，走完学习步骤后在末尾解锁。 */}
          <RevealItem className="browse-hint">
            <span className="browse-hint__icon">🏁</span>
            <span className="browse-hint__text">
              阶段测试在「<button type="button" className="browse-hint__link" onClick={() => setView('flow')}>🧭 有序学习</button>」流程末尾——
              走完视频 / 讲义 / 图解 / 笔记 / 费曼 / 实操后自动解锁，通过即点亮「已掌握」并推进进度。
            </span>
          </RevealItem>

          {/* 辅助项降级：次要小 chip 工具行（不与内容卡争视觉权重）。
             导学对话已升为上方醒目横幅主入口，不再收在此处。 */}
          {USE_REAL_API && (
            <RevealItem className="aux-chips">
              <span className="aux-chips__label">更多工具</span>
              <button
                type="button"
                className="aux-chip"
                onClick={() => setRegenConfirm(true)}
                disabled={regenRunning || regenerating}
              >
                {regenRunning ? `⏳ 重新生成中 · ${regenPhase}…` : '🔄 重新生成讲义'}
              </button>
            </RevealItem>
          )}
        </RevealGroup>
      )}

      {/* 资源详情过场：layoutId 共享元素「长大」成详情头部，内容淡入；× / Esc / 点遮罩关闭 */}
      <AnimatePresence>
        {openId && (
          <motion.div
            className="rescard-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={closeCard}
            role="dialog"
            aria-modal="true"
            aria-label={RESOURCE_META[openId].title}
          >
            <motion.div className="rescard-detail" onClick={(e) => e.stopPropagation()}>
              {ILLUSTRATED.has(openId) ? (
                <motion.div
                  className="rescard-detail__head"
                  layoutId={`res-illu-${openId}`}
                  style={{ background: RESOURCE_META[openId].theme }}
                >
                  <ResourceIllustration type={openId as ResourceIllustrationType} />
                </motion.div>
              ) : (
                <motion.div
                  className="rescard-detail__head rescard-detail__head--plain"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  style={{ background: RESOURCE_META[openId].theme }}
                />
              )}
              <h3 className="rescard-detail__title">{RESOURCE_META[openId].title}</h3>
              <button
                type="button"
                className="rescard-detail__close"
                onClick={closeCard}
                autoFocus
                aria-label="关闭"
              >
                ×
              </button>

              <motion.div
                className="rescard-detail__body"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.12, duration: 0.25 }}
              >
                {renderBody(openId)}
              </motion.div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* B10：重新生成确认弹层（约需 20 秒提示） */}
      <AnimatePresence>
        {regenConfirm && (
          <motion.div
            className="regen-modal"
            role="dialog"
            aria-modal="true"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setRegenConfirm(false)}
          >
            <motion.div
              className="regen-modal__card"
              initial={{ opacity: 0, scale: 0.94, y: 12 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.94, y: 12 }}
              onClick={(e) => e.stopPropagation()}
            >
              <h4 className="regen-modal__title">重新生成讲义</h4>
              <p className="regen-modal__text">
                将调用<strong>多智能体工作流</strong>为「{kpName} · {level}」重新生成讲义
                （学情诊断 → 检索生成 → 内容审核），<strong>约需 20 秒</strong>。完成后讲义内容与
                溯源徽章将自动刷新。
              </p>
              <div className="regen-modal__actions">
                <button
                  type="button"
                  className="regen-modal__btn regen-modal__btn--ghost"
                  onClick={() => setRegenConfirm(false)}
                >
                  取消
                </button>
                <button
                  type="button"
                  className="regen-modal__btn regen-modal__btn--primary"
                  onClick={() => void confirmRegen()}
                >
                  确定生成
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 即时辅导 · 常驻辅导入口已升为全局层（App 顶层 GlobalTutorLayer）：本页选中即问 / 卡顿关怀
          经 tutorBus 命令全局 dock 打开，无需再在此单挂（避免与全局 dock 重复订阅）。 */}

      {/* B-3 主动察觉卡顿：行为出现卡住信号时，AI 主动弹轻量关怀小卡（同一知识点/会话限频，关闭后不再弹）。 */}
      <StuckNudge kpId={kpId} kpName={kpName} />
    </div>
  )
}
