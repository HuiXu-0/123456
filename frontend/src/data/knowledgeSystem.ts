/* AI 知识体系（78 点 / 7 板块）前端种子 + 板块元信息。
   —— 会话二「知识图谱分层可视化」数据底座。
   与后端 GET /api/v1/knowledge-system（10.2）契约对齐；真实模式以后端为权威，
   本文件作为 mock 模式数据源 + 请求失败兜底 + 共享类型/板块色系来源。
   数据由 backend/app/services/knowledge_catalog.py 的 CATALOG 导出（幂等一致）。 */

export type KsLevel = '入门' | '进阶' | '前沿'

/** 单个知识点（后端 list_catalog 契约，camelCase）。 */
export interface KsPoint {
  id: string
  code: string
  name: string
  /** 板块码 ML/DL/CV/LLM/GEN/AGT/RLX */
  category: string
  level: KsLevel
  /** 先修知识点 id 列表（跨板块交叉亦以 id 记） */
  prerequisites: string[]
  isCore: boolean
  description: string
}

/** 板块级能力覆盖（后端 board_coverage；mock 模式可缺省）。 */
export interface KsCoverage {
  board: string
  name: string
  total: number
  tested: number
  inferred: number
  covered: number
  coveragePct: number
}

export interface KnowledgeSystemData {
  boards: { code: string; name: string }[]
  points: KsPoint[]
  coverage: KsCoverage[]
}

/** 7 板块展示元信息：顺序 = 学习递进；color = 各板块专属色系（护眼中性色，深浅主题皆可读）。
    这些是「类别色板」，与 chartTheme 令牌互补（ECharts canvas 需显式色值，无法吃 CSS 变量的 7 色分区）。 */
export interface BoardMeta {
  code: string
  name: string
  /** 图内短码（板块气泡中心） */
  short: string
  /** 板块专属色（HEX） */
  color: string
  /** 布局锚点（虚拟坐标，layout:'none' 用；渲染时统一缩放居中） */
  pos: [number, number]
}

export const BOARD_META: BoardMeta[] = [
  { code: 'ML', name: '机器学习基础', short: 'ML', color: '#5B7F6E', pos: [0, 0] },
  { code: 'DL', name: '深度学习核心', short: 'DL', color: '#3F7B8C', pos: [1.15, 0] },
  { code: 'CV', name: '计算机视觉', short: 'CV', color: '#5C6CA6', pos: [2.3, -1.05] },
  { code: 'LLM', name: '大模型与 NLP', short: 'LLM', color: '#7B5EA0', pos: [2.3, 1.05] },
  { code: 'GEN', name: '生成式模型与扩散', short: 'GEN', color: '#B0614C', pos: [3.5, 1.05] },
  { code: 'AGT', name: 'AI Agent 智能体', short: 'AGT', color: '#C58940', pos: [3.5, -1.05] },
  { code: 'RLX', name: '强化学习与前沿伦理', short: 'RL·X', color: '#8A7F46', pos: [4.65, 0] },
]

export const BOARD_BY_CODE: Record<string, BoardMeta> = Object.fromEntries(
  BOARD_META.map((b) => [b.code, b])
)

/** 层级 → 递进列序（入门→进阶→前沿，左→右，与先修流向一致）。 */
export const LEVEL_ORDER: Record<KsLevel, number> = { 入门: 0, 进阶: 1, 前沿: 2 }
export const LEVELS: KsLevel[] = ['入门', '进阶', '前沿']

/** 78 点种子（板块序 + code 序，与后端 list_catalog 排序一致）。 */
export const KS_POINTS: KsPoint[] = [
  { id: 'ml', code: 'ML-1', name: '机器学习概述', category: 'ML', level: '入门', prerequisites: [], isCore: true, description: '机器学习范式全景：监督/无监督/强化，任务与评估直觉。' },
  { id: 'ML-10', code: 'ML-10', name: '集成学习', category: 'ML', level: '进阶', prerequisites: ['ML-5'], isCore: false, description: 'Bagging/Boosting、随机森林、GBDT/XGBoost。' },
  { id: 'ML-11', code: 'ML-11', name: '模型评估与调优', category: 'ML', level: '进阶', prerequisites: ['ML-4'], isCore: false, description: '交叉验证、偏差方差、网格/随机搜索调参。' },
  { id: 'ML-12', code: 'ML-12', name: '概率图模型入门', category: 'ML', level: '进阶', prerequisites: ['ML-7'], isCore: false, description: '贝叶斯网络、马尔可夫随机场、推断直觉。' },
  { id: 'ML-2', code: 'ML-2', name: '数据与特征工程', category: 'ML', level: '入门', prerequisites: ['ml'], isCore: false, description: '数据清洗、特征构造与选择、标准化与编码。' },
  { id: 'ML-3', code: 'ML-3', name: '监督学习：回归', category: 'ML', level: '入门', prerequisites: ['ml'], isCore: false, description: '线性/多项式回归、损失与最小二乘、拟合与误差。' },
  { id: 'ML-4', code: 'ML-4', name: '监督学习：分类', category: 'ML', level: '入门', prerequisites: ['ML-3'], isCore: false, description: '逻辑回归、决策边界、分类评估指标。' },
  { id: 'ML-5', code: 'ML-5', name: '决策树', category: 'ML', level: '进阶', prerequisites: ['ML-4'], isCore: false, description: '信息增益/基尼、树的生长与剪枝。' },
  { id: 'ML-6', code: 'ML-6', name: '支持向量机 SVM', category: 'ML', level: '进阶', prerequisites: ['ML-4'], isCore: false, description: '最大间隔、核技巧、软间隔与正则。' },
  { id: 'ML-7', code: 'ML-7', name: '朴素贝叶斯与 KNN', category: 'ML', level: '进阶', prerequisites: ['ML-4'], isCore: false, description: '贝叶斯分类、条件独立假设、近邻投票。' },
  { id: 'ML-8', code: 'ML-8', name: '无监督：聚类', category: 'ML', level: '进阶', prerequisites: ['ML-2'], isCore: false, description: 'K-Means、层次聚类、密度聚类与评估。' },
  { id: 'ML-9', code: 'ML-9', name: '降维 PCA/t-SNE', category: 'ML', level: '进阶', prerequisites: ['ML-2'], isCore: false, description: '主成分分析、流形降维与可视化。' },
  { id: 'nn', code: 'DL-1', name: '神经网络基础', category: 'DL', level: '入门', prerequisites: ['ML-3'], isCore: true, description: '神经元、前向传播、激活函数。' },
  { id: 'DL-10', code: 'DL-10', name: '注意力机制', category: 'DL', level: '进阶', prerequisites: ['DL-8'], isCore: false, description: '对齐权重、软注意力、Query-Key-Value。' },
  { id: 'DL-11', code: 'DL-11', name: '自编码器 VAE', category: 'DL', level: '进阶', prerequisites: ['dl'], isCore: false, description: '编码-解码、隐空间、重构与生成。' },
  { id: 'DL-12', code: 'DL-12', name: '生成对抗网络 GAN', category: 'DL', level: '前沿', prerequisites: ['cnn'], isCore: false, description: '生成器-判别器对抗、训练稳定性。' },
  { id: 'DL-13', code: 'DL-13', name: '图神经网络 GNN', category: 'DL', level: '前沿', prerequisites: ['dl'], isCore: false, description: '图上消息传递、节点/图表示学习。' },
  { id: 'DL-2', code: 'DL-2', name: '反向传播与梯度下降', category: 'DL', level: '入门', prerequisites: ['nn'], isCore: false, description: '链式法则、梯度计算、参数更新。' },
  { id: 'DL-3', code: 'DL-3', name: '优化器（Adam等）', category: 'DL', level: '进阶', prerequisites: ['DL-2'], isCore: false, description: '动量、自适应学习率、Adam/RMSprop。' },
  { id: 'dl', code: 'DL-4', name: '深度神经网络', category: 'DL', level: '入门', prerequisites: ['DL-2'], isCore: true, description: '多层感知机、深度带来的表达力与难点。' },
  { id: 'DL-5', code: 'DL-5', name: '正则化与训练技巧', category: 'DL', level: '进阶', prerequisites: ['dl'], isCore: false, description: 'Dropout、BatchNorm、早停与数据增强。' },
  { id: 'cnn', code: 'DL-6', name: '卷积神经网络 CNN', category: 'DL', level: '进阶', prerequisites: ['dl'], isCore: true, description: '卷积、池化、感受野与权值共享。' },
  { id: 'DL-7', code: 'DL-7', name: '经典 CNN 架构', category: 'DL', level: '进阶', prerequisites: ['cnn'], isCore: false, description: 'LeNet/AlexNet/VGG/ResNet 演进。' },
  { id: 'DL-8', code: 'DL-8', name: '循环神经网络 RNN', category: 'DL', level: '进阶', prerequisites: ['dl'], isCore: false, description: '序列建模、时间展开与梯度问题。' },
  { id: 'DL-9', code: 'DL-9', name: 'LSTM 与 GRU', category: 'DL', level: '进阶', prerequisites: ['DL-8'], isCore: false, description: '门控机制、长程依赖与记忆单元。' },
  { id: 'CV-1', code: 'CV-1', name: '图像处理基础', category: 'CV', level: '入门', prerequisites: ['cnn'], isCore: false, description: '像素、卷积滤波、边缘与特征。' },
  { id: 'CV-2', code: 'CV-2', name: '图像分类', category: 'CV', level: '进阶', prerequisites: ['DL-7'], isCore: false, description: '分类流水线、数据增强、经典基准。' },
  { id: 'CV-3', code: 'CV-3', name: '目标检测（YOLO）', category: 'CV', level: '前沿', prerequisites: ['CV-2'], isCore: false, description: '边界框回归、单阶段检测、NMS。' },
  { id: 'CV-4', code: 'CV-4', name: '图像分割（U-Net）', category: 'CV', level: '前沿', prerequisites: ['CV-2'], isCore: false, description: '语义/实例分割、编码解码与跳连。' },
  { id: 'CV-5', code: 'CV-5', name: '迁移学习', category: 'CV', level: '进阶', prerequisites: ['CV-2'], isCore: false, description: '预训练特征复用、微调与冻结策略。' },
  { id: 'CV-6', code: 'CV-6', name: '视觉 Transformer ViT', category: 'CV', level: '前沿', prerequisites: ['CV-2', 'transformer'], isCore: false, description: '图像切块、位置编码、纯注意力视觉。' },
  { id: 'CV-7', code: 'CV-7', name: '多模态视觉语言（CLIP）', category: 'CV', level: '前沿', prerequisites: ['CV-6', 'transformer'], isCore: false, description: '图文对比学习、跨模态检索与零样本。' },
  { id: 'LLM-1', code: 'LLM-1', name: '文本表示与词向量', category: 'LLM', level: '入门', prerequisites: ['DL-8'], isCore: false, description: '分词、Word2Vec/GloVe、语义嵌入。' },
  { id: 'LLM-10', code: 'LLM-10', name: '向量数据库与嵌入', category: 'LLM', level: '前沿', prerequisites: ['LLM-9'], isCore: false, description: '嵌入检索、近邻索引、向量库工程。' },
  { id: 'LLM-11', code: 'LLM-11', name: '大模型评估', category: 'LLM', level: '前沿', prerequisites: ['LLM-4'], isCore: false, description: '基准评测、人评与自动评、幻觉度量。' },
  { id: 'LLM-12', code: 'LLM-12', name: '推理优化与部署', category: 'LLM', level: '前沿', prerequisites: ['LLM-4'], isCore: false, description: '量化、KV Cache、批处理与服务化。' },
  { id: 'transformer', code: 'LLM-2', name: 'Transformer 架构', category: 'LLM', level: '进阶', prerequisites: ['DL-10'], isCore: true, description: '自注意力、多头注意力、位置编码。' },
  { id: 'LLM-3', code: 'LLM-3', name: '预训练与微调范式', category: 'LLM', level: '进阶', prerequisites: ['transformer'], isCore: false, description: '自监督预训练、下游微调、BERT/GPT 路线。' },
  { id: 'LLM-4', code: 'LLM-4', name: '大语言模型概述', category: 'LLM', level: '进阶', prerequisites: ['LLM-3'], isCore: false, description: '规模定律、涌现能力、主流大模型全景。' },
  { id: 'finetune', code: 'LLM-5', name: '参数高效微调 LoRA/PEFT', category: 'LLM', level: '前沿', prerequisites: ['LLM-3'], isCore: true, description: '低秩适配、Adapter、Prompt Tuning。' },
  { id: 'LLM-6', code: 'LLM-6', name: '指令微调 SFT', category: 'LLM', level: '前沿', prerequisites: ['LLM-4'], isCore: false, description: '指令-回答有监督微调、遵循人类指令。' },
  { id: 'LLM-7', code: 'LLM-7', name: '对齐技术 RLHF/DPO', category: 'LLM', level: '前沿', prerequisites: ['LLM-6'], isCore: false, description: '奖励模型、人类偏好优化、安全对齐。' },
  { id: 'LLM-8', code: 'LLM-8', name: '提示工程 CoT', category: 'LLM', level: '进阶', prerequisites: ['LLM-4'], isCore: false, description: '提示设计、思维链、少样本示例。' },
  { id: 'LLM-9', code: 'LLM-9', name: '检索增强生成 RAG', category: 'LLM', level: '前沿', prerequisites: ['LLM-4'], isCore: false, description: '检索-拼接-生成、知识注入与防幻觉。' },
  { id: 'GEN-1', code: 'GEN-1', name: '生成式模型概述', category: 'GEN', level: '进阶', prerequisites: ['DL-11'], isCore: false, description: '判别式 vs 生成式、生成模型全景。' },
  { id: 'GEN-10', code: 'GEN-10', name: 'ControlNet 与可控生成', category: 'GEN', level: '前沿', prerequisites: ['GEN-9'], isCore: false, description: '结构控制、姿态/边缘引导。' },
  { id: 'GEN-11', code: 'GEN-11', name: '扩散加速采样', category: 'GEN', level: '前沿', prerequisites: ['GEN-5'], isCore: false, description: 'DDIM、采样步数与质量权衡。' },
  { id: 'GEN-12', code: 'GEN-12', name: '视频与3D扩散', category: 'GEN', level: '前沿', prerequisites: ['GEN-9'], isCore: false, description: 'Sora 类视频生成、3D 生成概念。' },
  { id: 'GEN-13', code: 'GEN-13', name: '扩散模型应用与伦理', category: 'GEN', level: '前沿', prerequisites: ['GEN-9'], isCore: false, description: '图像编辑、超分、深伪与版权。' },
  { id: 'GEN-2', code: 'GEN-2', name: 'VAE 变分自编码器', category: 'GEN', level: '进阶', prerequisites: ['DL-11'], isCore: false, description: '隐空间、重参数化、变分下界。' },
  { id: 'GEN-3', code: 'GEN-3', name: 'GAN 深入', category: 'GEN', level: '前沿', prerequisites: ['DL-12'], isCore: false, description: '训练稳定性、模式崩溃、DCGAN。' },
  { id: 'GEN-4', code: 'GEN-4', name: '扩散模型原理（DDPM）', category: 'GEN', level: '前沿', prerequisites: ['GEN-2'], isCore: false, description: '前向加噪/反向去噪、马尔可夫链。' },
  { id: 'GEN-5', code: 'GEN-5', name: '扩散的数学基础', category: 'GEN', level: '前沿', prerequisites: ['GEN-4'], isCore: false, description: '噪声调度、变分下界、得分匹配。' },
  { id: 'GEN-6', code: 'GEN-6', name: '去噪网络与 U-Net', category: 'GEN', level: '前沿', prerequisites: ['GEN-4', 'CV-4'], isCore: false, description: '扩散中的 U-Net、时间步嵌入。' },
  { id: 'GEN-7', code: 'GEN-7', name: '条件扩散与引导', category: 'GEN', level: '前沿', prerequisites: ['GEN-4'], isCore: false, description: 'Classifier-free guidance、条件生成。' },
  { id: 'GEN-8', code: 'GEN-8', name: '潜在扩散 LDM', category: 'GEN', level: '前沿', prerequisites: ['GEN-6'], isCore: false, description: 'Stable Diffusion 原理、潜空间扩散。' },
  { id: 'GEN-9', code: 'GEN-9', name: '文生图实践（Stable Diffusion）', category: 'GEN', level: '前沿', prerequisites: ['GEN-8'], isCore: false, description: '提示词、采样器、生成流程。' },
  { id: 'AGT-1', code: 'AGT-1', name: 'AI Agent 概述', category: 'AGT', level: '进阶', prerequisites: ['LLM-8'], isCore: false, description: '什么是智能体、与传统程序的区别。' },
  { id: 'AGT-10', code: 'AGT-10', name: 'Agent 自我反思与纠错', category: 'AGT', level: '前沿', prerequisites: ['AGT-3'], isCore: false, description: 'Reflexion、自我批判、幻觉抑制。' },
  { id: 'AGT-11', code: 'AGT-11', name: 'Agent 评估与可靠性', category: 'AGT', level: '前沿', prerequisites: ['AGT-6'], isCore: false, description: '成功率、护栏、安全边界。' },
  { id: 'AGT-12', code: 'AGT-12', name: '主流 Agent 框架', category: 'AGT', level: '前沿', prerequisites: ['AGT-9'], isCore: false, description: 'LangChain/AutoGPT/AutoGen 对比。' },
  { id: 'AGT-13', code: 'AGT-13', name: 'Agent 应用与前沿', category: 'AGT', level: '前沿', prerequisites: ['AGT-8'], isCore: false, description: '具身智能、Agent 落地场景。' },
  { id: 'AGT-2', code: 'AGT-2', name: 'Agent 架构范式', category: 'AGT', level: '进阶', prerequisites: ['AGT-1'], isCore: false, description: '感知-规划-执行-记忆-监督五层。' },
  { id: 'AGT-3', code: 'AGT-3', name: 'ReAct 推理与行动', category: 'AGT', level: '前沿', prerequisites: ['AGT-1'], isCore: false, description: '思考-行动-观察循环。' },
  { id: 'AGT-4', code: 'AGT-4', name: '工具调用与函数执行', category: 'AGT', level: '前沿', prerequisites: ['AGT-1'], isCore: false, description: 'Function calling、API 工具使用。' },
  { id: 'AGT-5', code: 'AGT-5', name: 'Agent 记忆系统', category: 'AGT', level: '前沿', prerequisites: ['AGT-2'], isCore: false, description: '短期/长期/情景记忆、向量记忆。' },
  { id: 'AGT-6', code: 'AGT-6', name: '任务规划与分解', category: 'AGT', level: '前沿', prerequisites: ['AGT-3'], isCore: false, description: '目标拆解、子任务、计划-执行。' },
  { id: 'AGT-7', code: 'AGT-7', name: '检索增强 Agent（RAG Agent）', category: 'AGT', level: '前沿', prerequisites: ['AGT-4', 'LLM-9'], isCore: false, description: '知识检索型智能体。' },
  { id: 'AGT-8', code: 'AGT-8', name: '多智能体系统', category: 'AGT', level: '前沿', prerequisites: ['AGT-2'], isCore: false, description: '角色分工、通信、协作。' },
  { id: 'AGT-9', code: 'AGT-9', name: '多智能体编排（LangGraph）', category: 'AGT', level: '前沿', prerequisites: ['AGT-8'], isCore: false, description: '状态机、工作流编排、图编排。' },
  { id: 'RL-1', code: 'RL-1', name: '强化学习概述', category: 'RLX', level: '进阶', prerequisites: ['ml'], isCore: false, description: '智能体-环境、奖励、策略与价值。' },
  { id: 'RL-2', code: 'RL-2', name: '价值方法 Q-Learning', category: 'RLX', level: '前沿', prerequisites: ['RL-1'], isCore: false, description: '时序差分、Q 表、探索与利用。' },
  { id: 'RL-3', code: 'RL-3', name: '深度强化学习 DQN', category: 'RLX', level: '前沿', prerequisites: ['RL-2', 'dl'], isCore: false, description: '值函数逼近、经验回放、目标网络。' },
  { id: 'RL-4', code: 'RL-4', name: '策略梯度 Actor-Critic', category: 'RLX', level: '前沿', prerequisites: ['RL-1'], isCore: false, description: '策略梯度、优势函数、A2C/PPO。' },
  { id: 'X-1', code: 'X-1', name: '知识蒸馏', category: 'RLX', level: '前沿', prerequisites: ['dl'], isCore: false, description: '教师-学生、软标签、模型压缩。' },
  { id: 'X-2', code: 'X-2', name: '联邦学习', category: 'RLX', level: '前沿', prerequisites: ['ml'], isCore: false, description: '数据不出域、分布式协同训练。' },
  { id: 'X-3', code: 'X-3', name: '可解释性 AI', category: 'RLX', level: '前沿', prerequisites: ['dl'], isCore: false, description: '特征归因、SHAP/LIME、可视化解释。' },
  { id: 'X-4', code: 'X-4', name: 'AI 伦理与安全', category: 'RLX', level: '进阶', prerequisites: ['ml'], isCore: false, description: '偏见公平、隐私、安全与治理。' },
]

/** 板块概要（total + 层级计数），由 KS_POINTS 派生——mock 模式喂给分层视图。 */
export function deriveBoardSummaries(points: KsPoint[] = KS_POINTS) {
  return BOARD_META.map((b) => {
    const inBoard = points.filter((p) => p.category === b.code)
    const levels: Record<string, number> = { 入门: 0, 进阶: 0, 前沿: 0 }
    inBoard.forEach((p) => (levels[p.level] += 1))
    return { code: b.code, name: b.name, total: inBoard.length, levels }
  })
}

/** mock 兜底的完整体系数据（coverage 留空，联调时由后端补） */
export const KNOWLEDGE_SYSTEM_SEED: KnowledgeSystemData = {
  boards: BOARD_META.map((b) => ({ code: b.code, name: b.name })),
  points: KS_POINTS,
  coverage: [],
}

export const ksPointById = (id: string): KsPoint | undefined =>
  KS_POINTS.find((p) => p.id === id)
