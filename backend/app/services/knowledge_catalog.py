"""AI 知识体系目录服务（《AI知识体系设计-v3》78 点 / 7 板块录入 · 会话一）。

职责（**纯数据 + 幂等录入 + 收窄/推断逻辑**，不依赖任何 service，避免环依赖）：

1. 体系定义 `CATALOG`（78 点）+ `BOARDS`（7 板块）+ `CORE_ALIASES`（6 已验证基准点
   → 体系编码映射）。**只录结构与元信息，不录内容**（内容由生成引擎按需产出）。
2. `seed(db)`：幂等录入——6 核心行原地补元信息（不动 name/lesson_seq/graph_node_id/
   description，保留已生成内容与测试基准）；其余 72 点新建（is_core=False，lesson_seq
   取高位序排在 6 核心之后，graph_node_id=code 避免污染 10.1 图谱 12 节点）。
3. `core_kps` / `core_kp_ids`：既有链路（诊断微测 / 能力雷达 / 学习路径 / 掌握度覆盖率
   等）**收窄到 6 已验证基准点**的统一入口，替换原先「query(KnowledgePoint).all() 默认
   即 6 核心」的隐式假设——扩充到 78 点后这些链路不回归（路径仍 6 步、覆盖率分母仍 6）。
4. `infer_prerequisite_closure`：先修推断——某点已测得掌握 → 其先修（传递闭包）大致掌握，
   减少诊断负担（72 点不逐一出题，靠核心点 + 先修推断覆盖）。
5. `board_coverage`：板块级能力覆盖（已测 + 先修推断），供诊断产出「板块级画像」。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import KnowledgePoint

# ── 7 板块（code, 中文名）。RL/X 合并为一个板块 RLX（强化学习与前沿伦理，8 点） ──
BOARDS: list[tuple[str, str]] = [
    ("ML", "机器学习基础"),
    ("DL", "深度学习核心"),
    ("CV", "计算机视觉"),
    ("LLM", "大模型与 NLP"),
    ("GEN", "生成式模型与扩散"),
    ("AGT", "AI Agent 智能体"),
    ("RLX", "强化学习与前沿伦理"),
]
_BOARD_NAMES: dict[str, str] = dict(BOARDS)

# ── 6 已验证基准点：体系编码 → 既有 KnowledgePoint.id（保留其内容/题库/测试） ──
CORE_ALIASES: dict[str, str] = {
    "ML-1": "ml",           # 机器学习概述 ← 机器学习基础
    "DL-1": "nn",           # 神经网络基础 ← 神经网络基础
    "DL-4": "dl",           # 深度神经网络 ← 深度学习原理
    "DL-6": "cnn",          # 卷积神经网络 CNN ← CNN架构
    "LLM-2": "transformer", # Transformer 架构 ← Transformer架构
    "LLM-5": "finetune",    # 参数高效微调 LoRA/PEFT ← 大模型微调技术（含 SFT/对齐 LLM-6·7 语义）
}

# 体系目录点（非核心）lesson_seq 起始高位序：排在 6 核心（1-6）之后，稳定且不干扰
# 能力雷达（取 lesson_seq 升序前 6 = 6 核心）/ 微测（升序）等既有排序。
_CATALOG_SEQ_BASE = 1000

# ── 78 点体系（code, name, category, level, prereqs[code], desc） ──
# 先修以体系 code 书写，seed 时解析为 id（核心点解析到别名 id，如 DL-1→nn）。
# 6 核心点的 desc 仅作占位（seed 不覆盖既有 description，保留其已生成简介）。
_C = "入门"
_A = "进阶"
_F = "前沿"
CATALOG: list[tuple[str, str, str, str, list[str], str]] = [
    # ── 板块一 · 机器学习基础 ML（12） ──
    ("ML-1", "机器学习概述", "ML", _C, [], "机器学习范式全景：监督/无监督/强化，任务与评估直觉。"),
    ("ML-2", "数据与特征工程", "ML", _C, ["ML-1"], "数据清洗、特征构造与选择、标准化与编码。"),
    ("ML-3", "监督学习：回归", "ML", _C, ["ML-1"], "线性/多项式回归、损失与最小二乘、拟合与误差。"),
    ("ML-4", "监督学习：分类", "ML", _C, ["ML-3"], "逻辑回归、决策边界、分类评估指标。"),
    ("ML-5", "决策树", "ML", _A, ["ML-4"], "信息增益/基尼、树的生长与剪枝。"),
    ("ML-6", "支持向量机 SVM", "ML", _A, ["ML-4"], "最大间隔、核技巧、软间隔与正则。"),
    ("ML-7", "朴素贝叶斯与 KNN", "ML", _A, ["ML-4"], "贝叶斯分类、条件独立假设、近邻投票。"),
    ("ML-8", "无监督：聚类", "ML", _A, ["ML-2"], "K-Means、层次聚类、密度聚类与评估。"),
    ("ML-9", "降维 PCA/t-SNE", "ML", _A, ["ML-2"], "主成分分析、流形降维与可视化。"),
    ("ML-10", "集成学习", "ML", _A, ["ML-5"], "Bagging/Boosting、随机森林、GBDT/XGBoost。"),
    ("ML-11", "模型评估与调优", "ML", _A, ["ML-4"], "交叉验证、偏差方差、网格/随机搜索调参。"),
    ("ML-12", "概率图模型入门", "ML", _A, ["ML-7"], "贝叶斯网络、马尔可夫随机场、推断直觉。"),
    # ── 板块二 · 深度学习核心 DL（13） ──
    ("DL-1", "神经网络基础", "DL", _C, ["ML-3"], "神经元、前向传播、激活函数。"),
    ("DL-2", "反向传播与梯度下降", "DL", _C, ["DL-1"], "链式法则、梯度计算、参数更新。"),
    ("DL-3", "优化器（Adam等）", "DL", _A, ["DL-2"], "动量、自适应学习率、Adam/RMSprop。"),
    ("DL-4", "深度神经网络", "DL", _C, ["DL-2"], "多层感知机、深度带来的表达力与难点。"),
    ("DL-5", "正则化与训练技巧", "DL", _A, ["DL-4"], "Dropout、BatchNorm、早停与数据增强。"),
    ("DL-6", "卷积神经网络 CNN", "DL", _A, ["DL-4"], "卷积、池化、感受野与权值共享。"),
    ("DL-7", "经典 CNN 架构", "DL", _A, ["DL-6"], "LeNet/AlexNet/VGG/ResNet 演进。"),
    ("DL-8", "循环神经网络 RNN", "DL", _A, ["DL-4"], "序列建模、时间展开与梯度问题。"),
    ("DL-9", "LSTM 与 GRU", "DL", _A, ["DL-8"], "门控机制、长程依赖与记忆单元。"),
    ("DL-10", "注意力机制", "DL", _A, ["DL-8"], "对齐权重、软注意力、Query-Key-Value。"),
    ("DL-11", "自编码器 VAE", "DL", _A, ["DL-4"], "编码-解码、隐空间、重构与生成。"),
    ("DL-12", "生成对抗网络 GAN", "DL", _F, ["DL-6"], "生成器-判别器对抗、训练稳定性。"),
    ("DL-13", "图神经网络 GNN", "DL", _F, ["DL-4"], "图上消息传递、节点/图表示学习。"),
    # ── 板块三 · 计算机视觉 CV（7） ──
    ("CV-1", "图像处理基础", "CV", _C, ["DL-6"], "像素、卷积滤波、边缘与特征。"),
    ("CV-2", "图像分类", "CV", _A, ["DL-7"], "分类流水线、数据增强、经典基准。"),
    ("CV-3", "目标检测（YOLO）", "CV", _F, ["CV-2"], "边界框回归、单阶段检测、NMS。"),
    ("CV-4", "图像分割（U-Net）", "CV", _F, ["CV-2"], "语义/实例分割、编码解码与跳连。"),
    ("CV-5", "迁移学习", "CV", _A, ["CV-2"], "预训练特征复用、微调与冻结策略。"),
    ("CV-6", "视觉 Transformer ViT", "CV", _F, ["CV-2", "LLM-2"], "图像切块、位置编码、纯注意力视觉。"),
    ("CV-7", "多模态视觉语言（CLIP）", "CV", _F, ["CV-6", "LLM-2"], "图文对比学习、跨模态检索与零样本。"),
    # ── 板块四 · 大模型与 NLP LLM（12） ──
    ("LLM-1", "文本表示与词向量", "LLM", _C, ["DL-8"], "分词、Word2Vec/GloVe、语义嵌入。"),
    ("LLM-2", "Transformer 架构", "LLM", _A, ["DL-10"], "自注意力、多头注意力、位置编码。"),
    ("LLM-3", "预训练与微调范式", "LLM", _A, ["LLM-2"], "自监督预训练、下游微调、BERT/GPT 路线。"),
    ("LLM-4", "大语言模型概述", "LLM", _A, ["LLM-3"], "规模定律、涌现能力、主流大模型全景。"),
    ("LLM-5", "参数高效微调 LoRA/PEFT", "LLM", _F, ["LLM-3"], "低秩适配、Adapter、Prompt Tuning。"),
    ("LLM-6", "指令微调 SFT", "LLM", _F, ["LLM-4"], "指令-回答有监督微调、遵循人类指令。"),
    ("LLM-7", "对齐技术 RLHF/DPO", "LLM", _F, ["LLM-6"], "奖励模型、人类偏好优化、安全对齐。"),
    ("LLM-8", "提示工程 CoT", "LLM", _A, ["LLM-4"], "提示设计、思维链、少样本示例。"),
    ("LLM-9", "检索增强生成 RAG", "LLM", _F, ["LLM-4"], "检索-拼接-生成、知识注入与防幻觉。"),
    ("LLM-10", "向量数据库与嵌入", "LLM", _F, ["LLM-9"], "嵌入检索、近邻索引、向量库工程。"),
    ("LLM-11", "大模型评估", "LLM", _F, ["LLM-4"], "基准评测、人评与自动评、幻觉度量。"),
    ("LLM-12", "推理优化与部署", "LLM", _F, ["LLM-4"], "量化、KV Cache、批处理与服务化。"),
    # ── 板块五 · 生成式模型与扩散 GEN（13·重点亮点·多图示） ──
    ("GEN-1", "生成式模型概述", "GEN", _A, ["DL-11"], "判别式 vs 生成式、生成模型全景。"),
    ("GEN-2", "VAE 变分自编码器", "GEN", _A, ["DL-11"], "隐空间、重参数化、变分下界。"),
    ("GEN-3", "GAN 深入", "GEN", _F, ["DL-12"], "训练稳定性、模式崩溃、DCGAN。"),
    ("GEN-4", "扩散模型原理（DDPM）", "GEN", _F, ["GEN-2"], "前向加噪/反向去噪、马尔可夫链。"),
    ("GEN-5", "扩散的数学基础", "GEN", _F, ["GEN-4"], "噪声调度、变分下界、得分匹配。"),
    ("GEN-6", "去噪网络与 U-Net", "GEN", _F, ["GEN-4", "CV-4"], "扩散中的 U-Net、时间步嵌入。"),
    ("GEN-7", "条件扩散与引导", "GEN", _F, ["GEN-4"], "Classifier-free guidance、条件生成。"),
    ("GEN-8", "潜在扩散 LDM", "GEN", _F, ["GEN-6"], "Stable Diffusion 原理、潜空间扩散。"),
    ("GEN-9", "文生图实践（Stable Diffusion）", "GEN", _F, ["GEN-8"], "提示词、采样器、生成流程。"),
    ("GEN-10", "ControlNet 与可控生成", "GEN", _F, ["GEN-9"], "结构控制、姿态/边缘引导。"),
    ("GEN-11", "扩散加速采样", "GEN", _F, ["GEN-5"], "DDIM、采样步数与质量权衡。"),
    ("GEN-12", "视频与3D扩散", "GEN", _F, ["GEN-9"], "Sora 类视频生成、3D 生成概念。"),
    ("GEN-13", "扩散模型应用与伦理", "GEN", _F, ["GEN-9"], "图像编辑、超分、深伪与版权。"),
    # ── 板块六 · AI Agent 智能体 AGT（13·重点亮点·深化） ──
    ("AGT-1", "AI Agent 概述", "AGT", _A, ["LLM-8"], "什么是智能体、与传统程序的区别。"),
    ("AGT-2", "Agent 架构范式", "AGT", _A, ["AGT-1"], "感知-规划-执行-记忆-监督五层。"),
    ("AGT-3", "ReAct 推理与行动", "AGT", _F, ["AGT-1"], "思考-行动-观察循环。"),
    ("AGT-4", "工具调用与函数执行", "AGT", _F, ["AGT-1"], "Function calling、API 工具使用。"),
    ("AGT-5", "Agent 记忆系统", "AGT", _F, ["AGT-2"], "短期/长期/情景记忆、向量记忆。"),
    ("AGT-6", "任务规划与分解", "AGT", _F, ["AGT-3"], "目标拆解、子任务、计划-执行。"),
    ("AGT-7", "检索增强 Agent（RAG Agent）", "AGT", _F, ["AGT-4", "LLM-9"], "知识检索型智能体。"),
    ("AGT-8", "多智能体系统", "AGT", _F, ["AGT-2"], "角色分工、通信、协作。"),
    ("AGT-9", "多智能体编排（LangGraph）", "AGT", _F, ["AGT-8"], "状态机、工作流编排、图编排。"),
    ("AGT-10", "Agent 自我反思与纠错", "AGT", _F, ["AGT-3"], "Reflexion、自我批判、幻觉抑制。"),
    ("AGT-11", "Agent 评估与可靠性", "AGT", _F, ["AGT-6"], "成功率、护栏、安全边界。"),
    ("AGT-12", "主流 Agent 框架", "AGT", _F, ["AGT-9"], "LangChain/AutoGPT/AutoGen 对比。"),
    ("AGT-13", "Agent 应用与前沿", "AGT", _F, ["AGT-8"], "具身智能、Agent 落地场景。"),
    # ── 板块七 · 强化学习与前沿伦理 RLX（8） ──
    ("RL-1", "强化学习概述", "RLX", _A, ["ML-1"], "智能体-环境、奖励、策略与价值。"),
    ("RL-2", "价值方法 Q-Learning", "RLX", _F, ["RL-1"], "时序差分、Q 表、探索与利用。"),
    ("RL-3", "深度强化学习 DQN", "RLX", _F, ["RL-2", "DL-4"], "值函数逼近、经验回放、目标网络。"),
    ("RL-4", "策略梯度 Actor-Critic", "RLX", _F, ["RL-1"], "策略梯度、优势函数、A2C/PPO。"),
    ("X-1", "知识蒸馏", "RLX", _F, ["DL-4"], "教师-学生、软标签、模型压缩。"),
    ("X-2", "联邦学习", "RLX", _F, ["ML-1"], "数据不出域、分布式协同训练。"),
    ("X-3", "可解释性 AI", "RLX", _F, ["DL-4"], "特征归因、SHAP/LIME、可视化解释。"),
    ("X-4", "AI 伦理与安全", "RLX", _A, ["ML-1"], "偏见公平、隐私、安全与治理。"),
]

# 达标阈值：能力分 ≥ 此值（或 passed）视为「已测得掌握」，触发其先修推断。
_MASTERED_SCORE = 70


def _code_to_id() -> dict[str, str]:
    """体系 code → KnowledgePoint.id：核心点映射到别名 id，其余以 code 为 id。"""
    return {code: CORE_ALIASES.get(code, code) for code, *_ in CATALOG}


def seed(db: Session) -> None:
    """幂等录入 78 点体系（会话一）。可重复执行：已存在则更新元信息，不重复插入。

    - 6 核心行（别名 id 已由 init_db._seed_knowledge_points 建好）：仅补 code/category/
      level/prerequisites/is_core=True，**不动** name/lesson_seq/graph_node_id/description
      （保留已验证基准的内容与测试）。
    - 其余 72 点：新建（is_core=False，lesson_seq=高位序，graph_node_id=code）。
    """
    # 会话 autoflush=False：先 flush，使同事务内刚 add 的 6 核心行（init_db 先建）对
    # 下方 db.get 可见，否则会被误判为不存在而重复插入（主键冲突）。
    db.flush()
    code2id = _code_to_id()
    for ordinal, (code, name, category, level, prereq_codes, desc) in enumerate(CATALOG):
        kp_id = code2id[code]
        prereq_ids = [code2id[p] for p in prereq_codes if p in code2id]
        row = db.get(KnowledgePoint, kp_id)
        if row is not None:
            # 已存在（含 6 核心 + 重复执行）：补/刷体系元信息，保留业务内容。
            row.code = code
            row.category = category
            row.level = level
            row.prerequisites = prereq_ids
            if kp_id in CORE_ALIASES.values():
                row.is_core = True
            continue
        db.add(
            KnowledgePoint(
                id=kp_id,
                name=name,
                lesson_seq=_CATALOG_SEQ_BASE + ordinal,
                graph_node_id=code,
                description=desc,
                code=code,
                category=category,
                level=level,
                prerequisites=prereq_ids,
                is_core=False,
            )
        )


# ── 既有链路收窄到 6 已验证基准点（统一入口，防扩充回归） ──────────────────────

def core_kps(db: Session) -> list[KnowledgePoint]:
    """6 已验证基准知识点（is_core=True）。既有诊断/画像/路径/掌握度链路统一从此取。"""
    return db.query(KnowledgePoint).filter(KnowledgePoint.is_core.is_(True)).all()


def core_kp_ids(db: Session) -> list[str]:
    return [kp.id for kp in core_kps(db)]


# ── 体系目录读取（供知识图谱浏览 / 新端点） ────────────────────────────────────

def list_catalog(db: Session) -> list[dict[str, Any]]:
    """全体系知识点（78 点），按板块序 + code 序排列，contract camelCase。"""
    board_order = {code: i for i, (code, _) in enumerate(BOARDS)}
    rows = db.query(KnowledgePoint).all()
    rows.sort(key=lambda k: (board_order.get(k.category, 99), k.code or k.id))
    return [
        {
            "id": kp.id,
            "code": kp.code,
            "name": kp.name,
            "category": kp.category,
            "board": _BOARD_NAMES.get(kp.category, kp.category),
            "level": kp.level,
            "prerequisites": list(kp.prerequisites or []),
            "description": kp.description,
            "isCore": bool(kp.is_core),
        }
        for kp in rows
    ]


# ── 先修推断 + 板块级覆盖（诊断适配） ──────────────────────────────────────────

def _prereq_map(db: Session) -> dict[str, list[str]]:
    return {kp.id: list(kp.prerequisites or []) for kp in db.query(KnowledgePoint).all()}


def infer_prerequisite_closure(
    mastered_ids: set[str], prereq_map: dict[str, list[str]]
) -> set[str]:
    """先修推断：已测得掌握的点，其**先修传递闭包**大致掌握（减少诊断负担）。

    返回被推断掌握的先修 id 集合（不含入参 mastered_ids 本身，交由调用方并集处理）。
    纯图遍历、确定性、零网络——mock 亦可跑。
    """
    inferred: set[str] = set()
    stack = list(mastered_ids)
    while stack:
        cur = stack.pop()
        for pre in prereq_map.get(cur, []):
            if pre not in inferred and pre not in mastered_ids:
                inferred.add(pre)
                stack.append(pre)
    return inferred


def board_coverage(
    db: Session, status_map: dict[str, str], score_map: dict[str, dict]
) -> list[dict[str, Any]]:
    """板块级能力画像（诊断产出「覆盖到板块级 + 已测点」）。

    输入为掌握度/能力分（由调用方从 mastery 服务取，本模块不反向依赖 service）：
    - 已测掌握 = passed 或能力分 ≥ 阈值（`tested`）；
    - 先修推断 = 已测掌握点的先修闭包（`inferred`）；
    - 覆盖 = 已测掌握 ∪ 先修推断；coveragePct = 覆盖数 / 板块点数。
    """
    prereq_map = _prereq_map(db)
    mastered: set[str] = set()
    for kp_id, st in status_map.items():
        if st == "passed":
            mastered.add(kp_id)
    for kp_id, info in score_map.items():
        sc = info.get("score")
        if isinstance(sc, int) and sc >= _MASTERED_SCORE:
            mastered.add(kp_id)
    inferred = infer_prerequisite_closure(mastered, prereq_map)
    covered = mastered | inferred

    rows = db.query(KnowledgePoint).all()
    by_board: dict[str, list[KnowledgePoint]] = {}
    for kp in rows:
        by_board.setdefault(kp.category, []).append(kp)

    out: list[dict[str, Any]] = []
    for code, name in BOARDS:
        points = by_board.get(code, [])
        total = len(points)
        tested_n = sum(1 for kp in points if kp.id in mastered)
        inferred_n = sum(1 for kp in points if kp.id in inferred)
        covered_n = sum(1 for kp in points if kp.id in covered)
        out.append(
            {
                "board": code,
                "name": name,
                "total": total,
                "tested": tested_n,
                "inferred": inferred_n,
                "covered": covered_n,
                "coveragePct": round(covered_n / total, 2) if total else 0.0,
            }
        )
    return out
