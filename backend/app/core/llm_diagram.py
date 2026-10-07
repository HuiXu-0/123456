"""确定性 Mermaid 知识图解模板与通用生成器。"""
from __future__ import annotations

# Mermaid 知识图解模板（接口文档 8.5；mock 与 deepseek 兜底共用）。
# 图解丰富化：不同知识点选用贴合其内容结构的不同图型——
#   nn/ml/dl 训练回路用横向流程图（flowchart LR，含反馈边）；
#   cnn 层级管线用纵向流程图（flowchart TD）；
#   transformer 用带 subgraph 的编码器块结构图；
#   finetune 谱系用思维导图（mindmap）。
# 约束：nn/cnn/dl/ml 首行恒为 flowchart（契约测试 test_b7a / test_contract_snapshot 钉死）。
DIAGRAM_TEMPLATES: dict[str, str] = {
    "nn": (
        # 论文级标准示意：多层感知机（输入层→隐藏层→输出层，全连接）+ 前向流 + 反向传播回路。
        "flowchart LR\n"
        '  subgraph IN["输入层 x"]\n'
        "    direction TB\n"
        '    I1(("x₁"))\n'
        '    I2(("x₂"))\n'
        '    I3(("x₃"))\n'
        "  end\n"
        '  subgraph HID["隐藏层<br/>z=Σwᵢxᵢ+b, a=ReLU(z)"]\n'
        "    direction TB\n"
        '    H1(("h₁"))\n'
        '    H2(("h₂"))\n'
        '    H3(("h₃"))\n'
        '    H4(("h₄"))\n'
        "  end\n"
        '  subgraph OUT["输出层 ŷ"]\n'
        "    direction TB\n"
        '    O1(("ŷ₁"))\n'
        '    O2(("ŷ₂"))\n'
        "  end\n"
        "  I1 --> H1 & H2 & H3 & H4\n"
        "  I2 --> H1 & H2 & H3 & H4\n"
        "  I3 --> H1 & H2 & H3 & H4\n"
        "  H1 --> O1 & O2\n"
        "  H2 --> O1 & O2\n"
        "  H3 --> O1 & O2\n"
        "  H4 --> O1 & O2\n"
        '  L(["损失 L(ŷ,y)"])\n'
        "  OUT --> L\n"
        "  L -. 反向传播 ∂L/∂w 逐层回传更新权重 .-> HID\n"
        "  HID -. .-> IN\n"
    ),
    "ml": (
        "flowchart LR\n"
        '  D["数据集"] --> SP{{"划分<br/>训练/验证/测试"}}\n'
        '  SP --> F(["特征工程<br/>标准化"])\n'
        '  F --> M["模型 f_θ"]\n'
        '  M --> L(["损失 + λ正则"])\n'
        '  L --> O{{"优化器"}}\n'
        "  O -. 参数更新 .-> M\n"
        '  M --> E["验证集评估<br/>看泛化"]\n'
        "  E -. 调超参/正则 .-> SP\n"
    ),
    "dl": (
        "flowchart LR\n"
        '  X["输入"] --> FW(["前向传播"])\n'
        '  FW --> P["预测 ŷ"]\n'
        '  P --> L["损失 L"]\n'
        '  Y["标签 y"] --> L\n'
        '  L --> BP(["反向传播<br/>链式法则"])\n'
        '  BP --> G["梯度 ∂L/∂θ"]\n'
        '  G --> U{{"优化器更新<br/>θ ← θ − η·g"}}\n'
        '  N["归一化 / 残差<br/>稳住深层"] --> FW\n'
        "  U -. 迭代 .-> FW\n"
    ),
    "cnn": (
        "flowchart TD\n"
        '  I["输入图像<br/>H×W×3"] --> C1(["卷积层<br/>局部+权重共享"])\n'
        '  C1 --> A1{{"ReLU"}}\n'
        '  A1 --> P1(["池化<br/>降采样·扩感受野"])\n'
        '  P1 --> C2(["卷积 ×N<br/>浅层→深层语义"])\n'
        '  C2 --> FL["展平 Flatten"]\n'
        '  FL --> FC["全连接层"]\n'
        '  FC --> SM{{"Softmax"}}\n'
        '  SM --> O["分类输出"]\n'
    ),
    "transformer": (
        "flowchart TD\n"
        '  E["输入嵌入"] --> PE["+ 位置编码"]\n'
        "  PE --> ENC\n"
        '  subgraph ENC["编码器块 ×N"]\n'
        "    direction TB\n"
        '    MHA["多头自注意力<br/>softmax(QKᵀ/√dₖ)V"] --> AN1["Add & Norm"]\n'
        '    AN1 --> FFN["前馈网络 FFN"]\n'
        '    FFN --> AN2["Add & Norm"]\n'
        "  end\n"
        '  ENC --> O["输出表示"]\n'
    ),
    "finetune": (
        "mindmap\n"
        '  root(("大模型微调"))\n'
        "    全参微调\n"
        "      更新全部权重\n"
        "      效果上限高·最贵\n"
        "    LoRA\n"
        "      冻结原权重\n"
        "      低秩增量 BA\n"
        "      省显存·可热插拔\n"
        "    指令微调 SFT\n"
        "      指令-回答数据\n"
        "      教模型听话\n"
        "    对齐\n"
        "      RLHF\n"
        "      DPO\n"
        "      更合规无害\n"
    ),
    # —— GEN 生成式模型与扩散板块（重点亮点·多图示）：13 个知识点逐一精写论文级图解。
    # 键 = kp_id（knowledge_catalog GEN-1..GEN-13）；真实模式也**模板优先**（见
    # generate_diagram），保证板块图解质量确定性达标（教材/论文级标准结构，不抽卡）。
    "GEN-1": (  # 生成式模型概述：四大家族谱系
        "mindmap\n"
        '  root(("生成式模型谱系"))\n'
        "    显式密度\n"
        "      自回归 AR\n"
        "        逐 token 连乘概率\n"
        "        GPT 系列\n"
        "      VAE\n"
        "        变分下界 ELBO\n"
        "      Flow 流模型\n"
        "        可逆变换·精确似然\n"
        "    隐式密度\n"
        "      GAN\n"
        "        生成器-判别器博弈\n"
        "    迭代去噪\n"
        "      扩散模型\n"
        "        DDPM · Score SDE\n"
        "        当前图像生成主流\n"
    ),
    "GEN-2": (  # VAE：编码-重参数化-解码 + 双损失
        "flowchart LR\n"
        '  X["输入 x"] --> ENC(["编码器 qφ"])\n'
        '  ENC --> MU["均值 μ"]\n'
        '  ENC --> SG["方差 σ²"]\n'
        '  MU --> RP{{"重参数化<br/>z = μ + σ⊙ε, ε∼N(0,I)"}}\n'
        "  SG --> RP\n"
        '  RP --> Z(("潜变量 z"))\n'
        '  Z --> DEC(["解码器 pθ"])\n'
        '  DEC --> XH["重建 x̂"]\n'
        '  XH --> REC["重建损失 ‖x−x̂‖²"]\n'
        '  PRI["先验 N(0,I)"] -. KL 散度正则 拉近 qφ 与先验 .-> Z\n'
        "  REC -. 反向传播 联合优化 ELBO .-> ENC\n"
    ),
    "GEN-3": (  # GAN：对抗博弈回路
        "flowchart LR\n"
        '  N(("噪声 z∼N(0,I)")) --> G(["生成器 G"])\n'
        '  G --> FAKE["伪样本 G(z)"]\n'
        '  REAL["真实样本 x"] --> D{{"判别器 D<br/>输出真伪概率"}}\n'
        "  FAKE --> D\n"
        '  D --> LD["判别损失<br/>分对真假"]\n'
        '  D --> LG["生成损失<br/>骗过 D"]\n'
        "  LD -. 梯度更新 D .-> D\n"
        "  LG -. 梯度更新 G .-> G\n"
        '  LG --> EQ(["纳什均衡：G 产出以假乱真样本"])\n'
    ),
    "GEN-4": (  # DDPM：前向加噪链 + 反向去噪链
        "flowchart TD\n"
        '  subgraph FWD["前向扩散 q：逐步加高斯噪声（固定过程，无参数）"]\n'
        "    direction LR\n"
        '    X0["x₀ 清晰图像"] --> X1["x₁"] --> XM["……"] --> XT["x_T ≈ 纯噪声 N(0,I)"]\n'
        "  end\n"
        '  subgraph REV["反向去噪 pθ：网络逐步还原（学习目标）"]\n'
        "    direction LR\n"
        '    YT["x_T 采样噪声"] --> YM["……"] --> Y1["x₁"] --> Y0["x̂₀ 生成图像"]\n'
        "  end\n"
        "  XT -. 训练：εθ 预测每步所加噪声 .-> YT\n"
        '  Y0 -. 目标 L = E‖ε − εθ(xₜ,t)‖² .-> X0\n'
    ),
    "GEN-5": (  # 扩散的数学基础：核心公式推导链
        "flowchart TD\n"
        '  A["单步加噪<br/>q(xₜ|xₜ₋₁) = N(√(1−βₜ)·xₜ₋₁, βₜI)"] --> B["任意步闭式采样<br/>xₜ = √ᾱₜ·x₀ + √(1−ᾱₜ)·ε"]\n'
        '  B --> C["变分下界 ELBO<br/>分解为逐步 KL 项"]\n'
        '  C --> D["简化训练目标<br/>L_simple = E‖ε − εθ(xₜ,t)‖²"]\n'
        '  D --> E["反向采样均值<br/>由 xₜ 与 εθ 反解 xₜ₋₁"]\n'
        '  E -.-> F(["得分匹配视角<br/>εθ 等价于估计 ∇log p(xₜ)"])\n'
    ),
    "GEN-6": (  # U-Net 去噪网络：编码-瓶颈-解码 + 跳连
        "flowchart TD\n"
        '  X["含噪图 xₜ ⊕ 时间嵌入 t"] --> E1["下采样块 64×64"]\n'
        '  E1 --> E2["下采样块 32×32"]\n'
        '  E2 --> E3["下采样块 16×16"]\n'
        '  E3 --> MID{{"瓶颈层<br/>ResBlock + 自注意力"}}\n'
        '  MID --> D3["上采样块 16×16"]\n'
        '  D3 --> D2["上采样块 32×32"]\n'
        '  D2 --> D1["上采样块 64×64"]\n'
        '  D1 --> OUT["预测噪声 εθ(xₜ,t)"]\n'
        "  E3 -. 跳跃连接 拼接特征 .-> D3\n"
        "  E2 -. 跳跃连接 拼接特征 .-> D2\n"
        "  E1 -. 跳跃连接 拼接特征 .-> D1\n"
    ),
    "GEN-7": (  # 条件扩散与 CFG 引导：双路预测合成
        "flowchart TD\n"
        '  XT["当前状态 xₜ"] --> CP & UP\n'
        '  C["条件 c：文本 / 类别"] --> CP(["条件预测 εθ(xₜ,t,c)"])\n'
        '  NO["空条件 ∅（训练时随机丢弃条件）"] --> UP(["无条件预测 εθ(xₜ,t,∅)"])\n'
        '  CP --> MIX{{"CFG 合成<br/>ε̃ = εᵤ + w·(εc − εᵤ)"}}\n'
        "  UP --> MIX\n"
        '  MIX --> STEP["去噪一步 → xₜ₋₁"]\n'
        '  W["引导强度 w"] -. w 越大越贴合条件·多样性下降 .-> MIX\n'
    ),
    "GEN-8": (  # 潜在扩散 LDM：像素空间 ↔ 潜空间
        "flowchart LR\n"
        '  X["像素图像<br/>512×512×3"] --> ENC(["VAE 编码器 E"])\n'
        '  ENC --> Z["潜表示 z<br/>64×64×4（约 48× 压缩）"]\n'
        '  Z --> DIFF{{"扩散过程在潜空间进行<br/>U-Net + 交叉注意力"}}\n'
        '  COND["条件：文本 / 布局 / 图像"] -. 交叉注意力注入 .-> DIFF\n'
        '  DIFF --> ZH["去噪潜码 ẑ"]\n'
        '  ZH --> DEC(["VAE 解码器 D"])\n'
        '  DEC --> OUT["生成图像 x̂"]\n'
        "  Z -. 计算量大幅降低：高效训练与采样 .-> DIFF\n"
    ),
    "GEN-9": (  # Stable Diffusion 文生图 pipeline
        "flowchart TD\n"
        '  P["文本提示词 Prompt"] --> CLIP(["CLIP 文本编码器"])\n'
        '  CLIP --> EMB["文本嵌入序列"]\n'
        '  NZ["初始潜噪声 z_T"] --> UNET\n'
        '  subgraph LOOP["潜空间去噪循环 ×20∼50 步"]\n'
        "    direction TB\n"
        '    UNET["U-Net 预测噪声 + CFG 引导"] --> SCH{{"采样调度器<br/>DDIM / DPM-Solver"}}\n'
        "    SCH -. zₜ 迭代到 zₜ₋₁ .-> UNET\n"
        "  end\n"
        "  EMB -. 交叉注意力 注入每步 .-> UNET\n"
        '  SCH --> Z0["去噪潜码 z₀"]\n'
        '  Z0 --> VAE(["VAE 解码器"])\n'
        '  VAE --> IMG["输出图像 512×512"]\n'
    ),
    "GEN-10": (  # ControlNet：冻结主干 + 可训练副本 + 零卷积
        "flowchart TD\n"
        '  COND["控制条件图<br/>边缘 / 深度 / 姿态骨架"] --> TC\n'
        '  subgraph CN["ControlNet（可训练）"]\n'
        "    direction TB\n"
        '    TC["SD 编码器的可训练副本"] --> ZC["零卷积 zero-conv<br/>初始输出为 0"]\n'
        "  end\n"
        '  P["文本提示词"] --> SD\n'
        '  subgraph SD["Stable Diffusion U-Net（权重冻结）"]\n'
        "    direction TB\n"
        '    FE["冻结编码器块"] --> FD["冻结解码器块"]\n'
        "  end\n"
        "  ZC -. 控制残差 逐层相加 .-> FD\n"
        '  FD --> OUT["受控生成<br/>构图 / 姿态 / 结构可控"]\n'
        "  ZC -. 训练初期不干扰原模型 .-> SD\n"
    ),
    "GEN-11": (  # 扩散加速采样：三条提速路线
        "flowchart LR\n"
        '  SLOW["DDPM 原始采样<br/>1000 步马尔可夫链·分钟级"] --> WHY{{"瓶颈：步数多 = 生成慢"}}\n'
        '  WHY --> DDIM(["DDIM<br/>非马尔可夫·确定性跳步<br/>50 步"])\n'
        '  WHY --> DPM(["DPM-Solver<br/>概率流 ODE 高阶求解<br/>10∼20 步"])\n'
        '  WHY --> DIST(["蒸馏 / 一致性模型<br/>LCM · Turbo<br/>1∼4 步"])\n'
        '  DDIM --> Q["质量-速度权衡"]\n'
        "  DPM --> Q\n"
        "  DIST --> Q\n"
        "  Q -. 步数越少越快·细节保真略降 .-> WHY\n"
    ),
    "GEN-12": (  # 视频与 3D 扩散：从图像基座延伸
        "flowchart TD\n"
        '  BASE["图像扩散基座<br/>Stable Diffusion 等"] --> VID(["视频扩散<br/>时间注意力·帧间一致性"])\n'
        '  BASE --> TD3(["3D 生成<br/>SDS 蒸馏 NeRF / 3D 高斯"])\n'
        '  VID --> V1["文生视频<br/>Sora · SVD"]\n'
        '  TD3 --> D1["文生 3D<br/>DreamFusion 等"]\n'
        '  V1 --> CH{{"共性挑战"}}\n'
        "  D1 --> CH\n"
        '  CH --> C1["时序 / 多视角一致性"]\n'
        '  CH --> C2["物理合理性"]\n'
        '  CH --> C3["算力与数据成本"]\n'
    ),
    "GEN-13": (  # 应用与伦理：应用-风险-治理三支
        "mindmap\n"
        '  root(("扩散模型应用与伦理"))\n'
        "    应用价值\n"
        "      文生图 / 视频创作\n"
        "      设计与游戏素材\n"
        "      医学影像重建增强\n"
        "      科研数据增广\n"
        "    风险\n"
        "      深度伪造 Deepfake\n"
        "      版权与训练数据争议\n"
        "      偏见与刻板印象放大\n"
        "    治理\n"
        "      内容水印与溯源 C2PA\n"
        "      模型卡与使用政策\n"
        "      法规合规审查\n"
    ),
}


def generic_diagram(kp_name: str, description: str) -> str:
    """未收录知识点：按 description 内容结构动态合成 Mermaid（不同知识点产出不同图）。

    按内容选图型：含「分类/类型/组成」等 → 层次图（graph TD）；否则 → 流程图
    （flowchart LR，节点形状轮换，含反馈边）。恒以 flowchart/graph 开头，始终可渲染。
    """
    raw = description or ""
    for sep in ("、", "，", "；", "。", "/", "·", " ", "（", "）", "(", ")"):
        raw = raw.replace(sep, "\n")
    concepts = [c.strip() for c in raw.split("\n") if c.strip()][:5]
    taxonomy = any(
        k in (description or "")
        for k in ("分类", "种类", "类型", "对比", "区别", "几种", "包括", "组成", "构成")
    )
    if taxonomy and concepts:
        lines = ["graph TD", f'  ROOT["{kp_name}"]']
        for i, c in enumerate(concepts):
            lines.append(f'  ROOT --> C{i}["{c}"]')
        return "\n".join(lines) + "\n"
    # 默认流程图：输入 → 核心步骤（由概念展开，形状轮换）→ 输出，并带迭代反馈边
    shapes = (("([", "])"), ("[", "]"), ("{{", "}}"))
    steps = concepts or [f"{kp_name}核心"]
    lines = ["flowchart LR", '  IN["输入 / 前置"]']
    prev = "IN"
    for i, c in enumerate(steps):
        op, cl = shapes[i % 3]
        nid = f"S{i}"
        lines.append(f'  {prev} --> {nid}{op}"{c}"{cl}')
        prev = nid
    lines.append(f'  {prev} --> OUT["输出 / 应用"]')
    lines.append("  OUT -. 迭代优化 .-> IN")
    return "\n".join(lines) + "\n"
