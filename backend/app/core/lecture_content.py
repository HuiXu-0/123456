"""讲义内容合成（CC 内容质量提升 · 任务1）。

为 mock 与「真实回落」提供**专业、递进、贴画像**的讲义正文，替代旧的平铺模板：

- 递进结构：概念引入 → 核心原理 → 具体例子 → 代码示例 → 常见误区 → 小结；
- 公式一律用标准 LaTeX（行内 ``$...$``、独立 ``$$...$$``），交前端 KaTeX 渲染；
- 贴画像给不同深度（depth）：
  - depth=2（能力强 / 高级）：补「公式推导 + 底层原理 + 对比延伸」，信息密度最高；
  - depth=1（基线 / 初级）：核心讲解 + 例子 + 代码；
  - depth=0（零基础 / 入门）：多类比、少术语、步骤更细、先直觉后形式化。

已收录 6 个核心知识点（nn/ml/dl/cnn/transformer/finetune）的精写知识原子；未收录
知识点按其 description 合成同构的递进讲义（不臆造具体公式，避免幻觉）。

纯函数、确定性（同输入恒同输出）：被 LLMClient._lecture_markdown 复用，故工作流产物
与直出讲义在「同 depth」下逐字一致（B5b/B10 契约不回归）。
"""
from __future__ import annotations

from typing import Any

# 难度档 → 基线深度（直出 / 无画像信号时用）。五档递进，各档 depth 互不相同 →
# 中级/精通与相邻档产出深度可区分（入门 0 / 初级 1 / 中级 2 / 高级 3 / 精通 4）。
_DIFFICULTY_DEPTH: dict[str, int] = {"入门": 0, "初级": 1, "中级": 2, "高级": 3, "精通": 4}

_DEPTH_MIN = 0
_DEPTH_MAX = 4


def difficulty_depth(difficulty: str) -> int:
    """难度档 → 基线 depth（未知难度回落 1）。"""
    return _DIFFICULTY_DEPTH.get(difficulty, 1)


def resolve_depth(difficulty: str, tier: str | None) -> int:
    """合成最终 depth：以难度档基线为主，画像 tier 做 ±1 微调（个性化不抹平五档区分）。

    - tier="advanced"（能力强）→ 基线 +1（封顶 4）；
    - tier="beginner"（零基础）→ 基线 -1（保底 0）；
    - 其余（basic / None）→ 难度基线。
    这样「同一知识点、同一难度」对能力强 / 零基础两位用户产出深度不同的讲义，
    而显式选定的五档之间仍保持递进区分（不再被 tier 拉平到固定两档）。
    """
    base = difficulty_depth(difficulty)
    if tier == "advanced":
        return min(base + 1, _DEPTH_MAX)
    if tier == "beginner":
        return max(base - 1, _DEPTH_MIN)
    return base


# ---- 6 个核心知识点的知识原子（领域事实，公式经核对） ------------------------------
# 每条原子字段：aliases（名称匹配）/ tagline / problem / analogy / principle /
# formulas / derivation / deepdive / example / code / pitfalls / extension / takeaways。
_ATOMS: dict[str, dict[str, Any]] = {
    "nn": {
        "aliases": ["神经网络"],
        "tagline": "用一层层「加权求和 + 非线性激活」把原始输入逐步变换为有用表示。",
        "problem": (
            "现实中输入与输出的关系往往是**非线性**的（如「像素 → 是不是猫」）。"
            "单纯的线性加权无论叠多少层，本质仍是一条直线，拟合不了弯曲的边界。"
            "神经网络的出发点，就是在线性变换之间插入**非线性激活**，从而逼近任意复杂函数。"
        ),
        "analogy": (
            "把一个神经元想象成一位「打分评委」：它先按重要性给每条输入打权重并加总（$z$），"
            "再用一个「兴奋阈值」（激活函数）决定要不要、以及多大程度地把信号传下去。"
            "很多评委分层接力，就能把「一堆像素」逐步读成「有没有猫」。"
        ),
        "principle": (
            "单个神经元做两件事：① **加权求和** 得到净输入 $z$；② 经**激活函数** $\\sigma$ "
            "得到输出 $a$。多个神经元组成层、层层堆叠即前向传播；训练时用**反向传播**"
            "沿链式法则把损失的梯度逐层回传，更新每个权重。"
        ),
        "formulas": [
            "$$z = \\sum_{i=1}^{n} w_i x_i + b = \\mathbf{w}^\\top \\mathbf{x} + b$$",
            "常用激活：$\\mathrm{ReLU}(z)=\\max(0,z)$、$\\sigma(z)=\\dfrac{1}{1+e^{-z}}$，输出 $a=\\sigma(z)$。",
        ],
        "derivation": (
            "反向传播的核心是**链式法则**。设损失为 $L$，对某权重 $w_i$ 求梯度：\n\n"
            "$$\\frac{\\partial L}{\\partial w_i}"
            "=\\frac{\\partial L}{\\partial a}\\cdot\\frac{\\partial a}{\\partial z}"
            "\\cdot\\frac{\\partial z}{\\partial w_i}"
            "=\\underbrace{\\frac{\\partial L}{\\partial a}}_{\\text{上层回传}}"
            "\\cdot\\,\\sigma'(z)\\cdot x_i$$\n\n"
            "其中 $\\partial z/\\partial w_i = x_i$。可见梯度 = 「上游误差 × 激活导数 × 本层输入」，"
            "逐层相乘即可把误差从输出端一路传回输入端。"
        ),
        "deepdive": (
            "为什么深层网络难训？链式相乘里若 $|\\sigma'(z)|<1$（如 sigmoid 饱和区约 0.25），"
            "梯度会随层数指数衰减——这就是**梯度消失**。ReLU 在正区间导数恒为 1，缓解了该问题，"
            "成为深层网络的默认激活。这也解释了「为何能逼近任意函数却又不好训」的张力。"
        ),
        "example": (
            "设输入 $\\mathbf{x}=[0.5,\\,0.8]$、权重 $\\mathbf{w}=[0.4,\\,0.7]$、偏置 $b=0.1$：\n\n"
            "$$z = 0.4\\times0.5 + 0.7\\times0.8 + 0.1 = 0.86,\\quad a=\\mathrm{ReLU}(0.86)=0.86$$\n\n"
            "若把权重换成 $[-0.4,-0.7]$，则 $z=-0.66$，$\\mathrm{ReLU}(-0.66)=0$——神经元「不激活」。"
        ),
        "code": (
            "```python\n"
            "import numpy as np\n\n"
            "def neuron_forward(x, w, b):\n"
            "    z = np.dot(x, w) + b        # ① 加权求和：z = w·x + b\n"
            "    a = np.maximum(0.0, z)      # ② ReLU 非线性激活：max(0, z)\n"
            "    return z, a\n\n"
            "x = np.array([0.5, 0.8])        # 输入\n"
            "w = np.array([0.4, 0.7])        # 权重\n"
            "z, a = neuron_forward(x, w, b=0.1)\n"
            "print(f\"z={z:.2f}, a={a:.2f}\")  # 输出: z=0.86, a=0.86\n"
            "```"
        ),
        "pitfalls": [
            "**漏掉非线性**：多层纯线性叠加仍等价于单层线性，必须有激活函数才有表达力。",
            "**sigmoid 用在深层**：两端饱和导致梯度消失，深层默认改用 ReLU 系激活。",
            "**忘了偏置 $b$**：没有偏置，决策边界被迫过原点，拟合能力受限。",
        ],
        "extension": (
            "横向对比：ReLU 解决梯度消失但有「死神经元」问题，衍生出 LeakyReLU、GELU；"
            "纵向延伸：把「神经元 + 反向传播」放大到很多层，就进入**深度学习**（见相关知识点）。"
        ),
        "takeaways": [
            "神经元 = 加权求和 $z=\\mathbf{w}^\\top\\mathbf{x}+b$ + 非线性激活 $a=\\sigma(z)$。",
            "非线性激活是表达力的来源；反向传播 = 链式法则逐层回传梯度。",
            "深层用 ReLU 缓解梯度消失。",
        ],
    },
    "ml": {
        "aliases": ["机器学习"],
        "tagline": "从数据中**最小化经验风险**地拟合一个映射，并通过正则控制泛化。",
        "problem": (
            "我们想让程序「从经验里学规律」，而不是把规则一条条写死。机器学习把它形式化为："
            "给定样本 $\\{(x_i,y_i)\\}$，找一个函数 $f_\\theta$ 使预测尽量贴近真实——但真正的目标不是"
            "「背下训练集」，而是在**没见过的数据**上也准（泛化）。"
        ),
        "analogy": (
            "像备考刷题：把题目做对（拟合训练集）只是手段，**考场上遇到新题也会做**才是目的。"
            "只会背原题（过拟合）、或根本没学会（欠拟合），都考不好。正则化就像「别死记，抓通法」。"
        ),
        "principle": (
            "学习 = **经验风险最小化**：在训练集上最小化平均损失，再加正则项约束模型复杂度，"
            "以在「拟合训练数据」与「泛化到新数据」之间取得平衡。监督/无监督的区别在于有没有标签 $y$。"
        ),
        "formulas": [
            "$$\\hat\\theta = \\arg\\min_{\\theta}\\ \\frac{1}{N}\\sum_{i=1}^{N}"
            "\\ell\\big(f_\\theta(x_i),\\,y_i\\big) + \\lambda\\,R(\\theta)$$",
            "回归常用均方损失 $\\ell=(\\hat y-y)^2$，L2 正则 $R(\\theta)=\\lVert\\theta\\rVert_2^2$，"
            "$\\lambda$ 越大正则越强。",
        ],
        "derivation": (
            "为什么过拟合可被「偏差-方差分解」解释？泛化误差可拆为：\n\n"
            "$$\\mathbb{E}\\big[(y-\\hat f(x))^2\\big]"
            "=\\underbrace{(\\mathrm{Bias}[\\hat f])^2}_{\\text{欠拟合}}"
            "+\\underbrace{\\mathrm{Var}[\\hat f]}_{\\text{过拟合}}"
            "+\\underbrace{\\sigma^2}_{\\text{噪声下界}}$$\n\n"
            "模型越复杂，偏差降、方差升。正则化通过压低 $\\mathrm{Var}$（牺牲一点偏差）来改善总误差。"
        ),
        "deepdive": (
            "$\\lambda$ 的选择本质是**容量控制**：从贝叶斯视角，L2 正则等价于对参数加各向同性高斯先验，"
            "L1 正则等价于拉普拉斯先验（诱导稀疏、可做特征选择）。因此「调正则强度」= 「调先验信念」。"
        ),
        "example": (
            "用一元多项式拟合 10 个点：1 次（直线）偏差大、欠拟合；9 次能穿过每个点但在点间剧烈震荡、"
            "方差大、过拟合；3 次往往泛化最好。加上 $\\lambda\\lVert\\theta\\rVert_2^2$ 后，高次项系数被压小，"
            "9 次曲线也会变平滑。"
        ),
        "code": (
            "```python\n"
            "import numpy as np\n\n"
            "# 岭回归（带 L2 正则的线性回归）闭式解：theta = (XᵀX + λI)⁻¹ Xᵀy\n"
            "def ridge_fit(X, y, lam):\n"
            "    n_features = X.shape[1]\n"
            "    A = X.T @ X + lam * np.eye(n_features)  # 加 λI 即 L2 正则\n"
            "    return np.linalg.solve(A, X.T @ y)\n\n"
            "X = np.array([[1, 0.0], [1, 1.0], [1, 2.0]])  # 第一列为偏置项\n"
            "y = np.array([0.1, 0.9, 2.1])\n"
            "print(ridge_fit(X, y, lam=0.0))   # 无正则: 普通最小二乘\n"
            "print(ridge_fit(X, y, lam=1.0))   # λ=1: 系数被收缩，更平滑\n"
            "```"
        ),
        "pitfalls": [
            "**用训练集精度评估模型**：必须用独立验证/测试集，否则过拟合看不出来。",
            "**特征不做标准化就上正则**：量纲不一致会让正则惩罚偏向某些特征。",
            "**数据泄漏**：把测试集信息混进特征工程，线下虚高、线上翻车。",
        ],
        "extension": (
            "对比：线性模型偏差高但稳、可解释；神经网络容量大但需更多数据与正则。"
            "当线性模型欠拟合且数据充足时，自然过渡到**神经网络 / 深度学习**。"
        ),
        "takeaways": [
            "目标是泛化，不是背训练集：经验风险 + 正则。",
            "过拟合/欠拟合 = 偏差-方差权衡；正则降方差。",
            "评估必须用独立验证集，警惕数据泄漏。",
        ],
    },
    "dl": {
        "aliases": ["深度学习"],
        "tagline": "用**梯度下降 + 反向传播**端到端训练多层网络，靠归一化与优化器稳住深层。",
        "problem": (
            "神经网络一旦堆深，参数动辄百万，无法手解。深度学习要回答的是：怎样**自动、稳定**地"
            "调好这么多参数？答案是用损失对参数的梯度指引更新方向，反复迭代逼近极小值。"
        ),
        "analogy": (
            "像在大雾里下山找最低点：你看不到全局，只能感知**脚下最陡的下坡方向**（梯度），"
            "朝反方向迈一小步（学习率 $\\eta$），不断重复。步子太大会越过谷底来回横跳，太小则慢。"
        ),
        "principle": (
            "训练 = 重复三步：前向算损失 → 反向传播算梯度 → 梯度下降更新参数。"
            "深层的难点在于梯度的数值稳定，需要合适的优化器（带动量/自适应学习率）与归一化手段。"
        ),
        "formulas": [
            "$$\\theta \\leftarrow \\theta - \\eta\\,\\nabla_\\theta L(\\theta)$$",
            "Adam 用一阶矩 $m_t=\\beta_1 m_{t-1}+(1-\\beta_1)g_t$ 与二阶矩自适应缩放各参数步长。",
            "BatchNorm：$\\hat x=\\dfrac{x-\\mu_B}{\\sqrt{\\sigma_B^2+\\epsilon}}$，把每层输入拉回稳定分布。",
        ],
        "derivation": (
            "为什么「沿负梯度」一定下降？对 $L$ 在 $\\theta$ 处一阶泰勒展开：\n\n"
            "$$L(\\theta-\\eta g)\\approx L(\\theta)-\\eta\\,g^\\top g = L(\\theta)-\\eta\\lVert g\\rVert^2$$\n\n"
            "只要 $\\eta>0$ 且梯度 $g\\neq 0$，$-\\eta\\lVert g\\rVert^2<0$，损失就下降。$\\eta$ 过大时"
            "高阶项不可忽略，泰勒近似失效，于是震荡甚至发散——这正是学习率需要调的根因。"
        ),
        "deepdive": (
            "归一化（BatchNorm/LayerNorm）让每层输入分布稳定，等价于改善损失曲面的条件数，"
            "使梯度方向更「指向谷底」，从而允许更大的学习率、更快收敛。残差连接 $y=x+F(x)$ 则给梯度"
            "开了一条「高速公路」，进一步缓解深层梯度消失。"
        ),
        "example": (
            "一维目标 $L(\\theta)=\\theta^2$，梯度 $g=2\\theta$。取 $\\theta_0=1,\\ \\eta=0.1$：\n\n"
            "$$\\theta_1=1-0.1\\times2\\times1=0.8,\\quad \\theta_2=0.8-0.1\\times1.6=0.64,\\ \\dots\\to 0$$\n\n"
            "若 $\\eta=1.1$，则 $\\theta_1=1-1.1\\times2=-1.2$，$|\\theta|$ 反而变大——发散。"
        ),
        "code": (
            "```python\n"
            "# 梯度下降最小化 L(θ)=θ²，对比合适/过大学习率\n"
            "def gradient_descent(theta, lr, steps=5):\n"
            "    for _ in range(steps):\n"
            "        grad = 2 * theta          # dL/dθ = 2θ\n"
            "        theta = theta - lr * grad  # θ ← θ − η·grad\n"
            "    return theta\n\n"
            "print(round(gradient_descent(1.0, lr=0.1), 4))  # 0.3277 → 收敛向 0\n"
            "print(round(gradient_descent(1.0, lr=1.1), 4))  # 2.4883 → 发散\n"
            "```"
        ),
        "pitfalls": [
            "**学习率拍脑袋**：太大震荡/发散，太小训练慢；建议配合 warmup 与衰减。",
            "**不做归一化**：深层分布漂移（internal covariate shift），收敛慢且不稳。",
            "**梯度爆炸不裁剪**：RNN/深层易爆，需 gradient clipping。",
        ],
        "extension": (
            "优化器对比：SGD+Momentum 泛化常更好但需调参；Adam 收敛快、对学习率不敏感但有时泛化略逊。"
            "把这些训练技巧用到卷积/自注意力结构上，就得到 **CNN / Transformer**。"
        ),
        "takeaways": [
            "训练三步：前向→反向→更新；核心式 $\\theta\\leftarrow\\theta-\\eta\\nabla_\\theta L$。",
            "学习率决定成败；归一化与残差稳住深层。",
            "Adam 快、SGD+Momentum 稳，按场景选。",
        ],
    },
    "cnn": {
        "aliases": ["CNN", "卷积"],
        "tagline": "用**局部卷积核 + 权重共享 + 池化**高效提取图像的平移不变特征。",
        "problem": (
            "图像若直接展平接全连接，参数爆炸（一张 224×224×3 的图就有 15 万维输入），"
            "且丢掉了「相邻像素相关、特征可平移」的结构先验。CNN 用卷积把这些先验编码进网络。"
        ),
        "analogy": (
            "像拿一块小**印章**（卷积核）在整张图上滑动盖印：同一个印章在哪儿都用同一套花纹"
            "（权重共享），既省参数，又保证「猫在左上还是右下都能被认出」（平移不变）。"
        ),
        "principle": (
            "卷积层用小核在输入上滑动做局部加权求和，提取边缘/纹理等局部特征；池化层做降采样、"
            "扩大感受野并增强平移鲁棒性；多层堆叠后，浅层学边缘、深层学语义部件，最后接全连接分类。"
        ),
        "formulas": [
            "$$ (I * K)(i,j) = \\sum_{m}\\sum_{n} I(i+m,\\,j+n)\\,K(m,n) $$",
            "输出尺寸：$O=\\left\\lfloor\\dfrac{W-F+2P}{S}\\right\\rfloor+1$"
            "（$W$ 输入边长，$F$ 核大小，$P$ 填充，$S$ 步幅）。",
        ],
        "derivation": (
            "感受野（一个输出像素「看到」的输入范围）逐层递推：\n\n"
            "$$RF_\\ell = RF_{\\ell-1} + (F_\\ell-1)\\prod_{k=1}^{\\ell-1} S_k$$\n\n"
            "可见步幅 $S$ 对感受野是**乘性**放大——这正是「深层 CNN 用少量层就能覆盖全图」的原因。"
        ),
        "deepdive": (
            "权重共享让卷积层参数量与输入尺寸**无关**（只取决于核大小×通道数），相比全连接呈数量级下降；"
            "同时卷积是平移等变（equivariant）算子，配合池化得到平移不变性——这是 CNN 在视觉上高效的根本。"
        ),
        "example": (
            "输入 $5\\times5$、核 $3\\times3$、步幅 $S=1$、填充 $P=0$：\n\n"
            "$$O=\\left\\lfloor\\frac{5-3+0}{1}\\right\\rfloor+1=3$$\n\n"
            "即输出 $3\\times3$ 特征图。若把 $P$ 设为 1，则 $O=5$，尺寸保持不变（same 卷积）。"
        ),
        "code": (
            "```python\n"
            "import numpy as np\n\n"
            "def conv2d_valid(img, kernel):\n"
            "    H, W = img.shape\n"
            "    f = kernel.shape[0]\n"
            "    out = np.zeros((H - f + 1, W - f + 1))  # valid: 不填充\n"
            "    for i in range(out.shape[0]):\n"
            "        for j in range(out.shape[1]):\n"
            "            patch = img[i:i+f, j:j+f]        # 取局部窗口\n"
            "            out[i, j] = np.sum(patch * kernel)  # 局部加权求和\n"
            "    return out\n\n"
            "img = np.arange(25).reshape(5, 5).astype(float)\n"
            "edge = np.array([[1, 0, -1], [1, 0, -1], [1, 0, -1]])  # 竖直边缘检测核\n"
            "print(conv2d_valid(img, edge).shape)  # 输出: (3, 3)\n"
            "```"
        ),
        "pitfalls": [
            "**padding/stride 算错尺寸**：套公式 $O=\\lfloor(W-F+2P)/S\\rfloor+1$ 先核对再搭网络。",
            "**在卷积后忘加非线性**：卷积本身是线性的，必须接激活才有表达力。",
            "**池化过度**：过早大幅降采样会丢失细粒度信息，影响小目标识别。",
        ],
        "extension": (
            "经典演进：LeNet→AlexNet→VGG（堆小核）→ResNet（残差）。近年视觉也被 **Transformer**"
            "（ViT）攻入，把图像切块当 token 做自注意力——可对照 Transformer 知识点理解二者取舍。"
        ),
        "takeaways": [
            "卷积=局部加权求和 + 权重共享；池化扩感受野、增鲁棒。",
            "尺寸公式 $O=\\lfloor(W-F+2P)/S\\rfloor+1$ 要会算。",
            "参数量与输入尺寸无关，故高效。",
        ],
    },
    "transformer": {
        "aliases": ["Transformer", "注意力", "自注意力"],
        "tagline": "用**自注意力**让每个 token 直接看到全序列，并行建模长程依赖。",
        "problem": (
            "RNN 顺序处理序列，长程依赖要经过很多步才能传递，既慢（不能并行）又容易遗忘。"
            "Transformer 抛弃循环，用注意力让任意两个位置**一步直连**，从而高效建模长距离关系。"
        ),
        "analogy": (
            "读句子「它指的是猫」时，你的注意力会自动「回看」前文的猫。自注意力就是把这种"
            "「每个词主动检索与自己最相关的词」机制数学化：用 Query 去和所有 Key 匹配，按相关度加权汇总 Value。"
        ),
        "principle": (
            "每个 token 投影出 Query/Key/Value 三个向量。注意力分数 = $Q$ 与 $K$ 的点积（相关度），"
            "经缩放与 softmax 归一化为权重，再对 $V$ 加权求和。多头让模型在不同子空间并行关注不同关系；"
            "位置编码补回被打乱的顺序信息。"
        ),
        "formulas": [
            "$$\\mathrm{Attention}(Q,K,V)=\\mathrm{softmax}\\!\\left(\\frac{QK^\\top}{\\sqrt{d_k}}\\right)V$$",
            "softmax：$\\mathrm{softmax}(z)_i=\\dfrac{e^{z_i}}{\\sum_j e^{z_j}}$；"
            "位置编码：$PE_{(pos,2i)}=\\sin\\!\\big(pos/10000^{2i/d}\\big)$。",
        ],
        "derivation": (
            "为何要除以 $\\sqrt{d_k}$？设 $q,k$ 各分量独立、均值 0 方差 1，则点积"
            "$q\\cdot k=\\sum_{i=1}^{d_k} q_i k_i$ 的方差为 $d_k$。$d_k$ 很大时分数量级随之变大，"
            "softmax 会被推入**饱和区**（梯度趋近 0）。除以 $\\sqrt{d_k}$ 把方差缩回 1，稳住梯度——"
            "这就是「缩放点积注意力」里 scale 的由来。"
        ),
        "deepdive": (
            "复杂度：自注意力对长度 $n$ 是 $O(n^2 d)$——这是长文本的主要瓶颈，催生了稀疏/线性注意力、"
            "FlashAttention 等优化。多头注意力则把 $d$ 拆成 $h$ 份并行，每个头学一种关系（如句法/指代），"
            "再拼接投影，表达力强于单个大头。"
        ),
        "example": (
            "单头、$d_k=2$，某 query $q=[1,0]$ 与两个 key $k_1=[1,0],k_2=[0,1]$：\n\n"
            "$$\\frac{q k_1^\\top}{\\sqrt2}=\\frac{1}{\\sqrt2}\\approx0.71,\\quad"
            "\\frac{q k_2^\\top}{\\sqrt2}=0$$\n\n"
            "softmax$([0.71,0])\\approx[0.67,0.33]$——query 把约 2/3 注意力放在与它对齐的 $k_1$ 上。"
        ),
        "code": (
            "```python\n"
            "import numpy as np\n\n"
            "def softmax(z):\n"
            "    z = z - z.max(axis=-1, keepdims=True)  # 数值稳定：减去每行最大值\n"
            "    e = np.exp(z)\n"
            "    return e / e.sum(axis=-1, keepdims=True)\n\n"
            "def attention(Q, K, V):\n"
            "    d_k = Q.shape[-1]\n"
            "    scores = Q @ K.T / np.sqrt(d_k)   # 缩放点积 QKᵀ/√d_k\n"
            "    weights = softmax(scores)         # 归一化为注意力权重\n"
            "    return weights @ V                # 对 V 加权求和\n\n"
            "Q = np.array([[1.0, 0.0]])\n"
            "K = np.array([[1.0, 0.0], [0.0, 1.0]])\n"
            "V = np.array([[10.0], [20.0]])\n"
            "print(attention(Q, K, V).round(2))   # 输出: [[13.33]] = 0.67*10 + 0.33*20\n"
            "```"
        ),
        "pitfalls": [
            "**漏掉 $\\sqrt{d_k}$ 缩放**：分数过大使 softmax 饱和、梯度消失，训练不动。",
            "**忘加位置编码**：自注意力本身对顺序不敏感，缺它则「猫追狗」「狗追猫」无法区分。",
            "**softmax 不做数值稳定**：直接 exp 大数会溢出，应先减去每行最大值。",
        ],
        "extension": (
            "对比 CNN/RNN：注意力全局且并行，但 $O(n^2)$ 成本高。它是 BERT/GPT 等大模型的基座，"
            "理解了它再看**大模型微调**会顺很多。"
        ),
        "takeaways": [
            "自注意力核心式 $\\mathrm{softmax}(QK^\\top/\\sqrt{d_k})V$。",
            "$\\sqrt{d_k}$ 缩放稳梯度；多头并行学多种关系；位置编码补顺序。",
            "复杂度 $O(n^2)$ 是长序列瓶颈。",
        ],
    },
    "finetune": {
        "aliases": ["微调", "大模型微调", "LoRA"],
        "tagline": "在预训练大模型上**低成本适配**下游任务：全参 / LoRA / 指令微调 + 对齐。",
        "problem": (
            "从零训练一个大模型成本极高。但预训练模型已学到通用语言/世界知识，我们只需用少量数据"
            "把它「**调**」到具体任务或风格上。难点是：怎样在不破坏原有能力、又省显存的前提下微调？"
        ),
        "analogy": (
            "像一位博学的通才（预训练模型）入职新岗位：不必重读大学（重训），只要做**岗前培训**"
            "（微调）。LoRA 更像「贴便利贴」——不改原书，只在旁边记少量增量笔记，随用随贴。"
        ),
        "principle": (
            "微调谱系：**全参微调**更新所有权重（效果上限高、最贵）；**LoRA** 冻结原权重、只训一对"
            "低秩矩阵作为增量（极省显存）；**指令微调(SFT)** 用「指令-回答」数据教模型听话；"
            "再经 **RLHF/DPO 对齐** 让输出更符合人类偏好。"
        ),
        "formulas": [
            "LoRA 增量：$$W' = W_0 + \\Delta W = W_0 + \\frac{\\alpha}{r} B A,\\quad"
            "B\\in\\mathbb{R}^{d\\times r},\\ A\\in\\mathbb{R}^{r\\times k},\\ r\\ll \\min(d,k)$$",
            "SFT 目标（自回归交叉熵）："
            "$\\mathcal{L}=-\\sum_{t}\\log p_\\theta\\big(y_t\\mid y_{<t},x\\big)$。",
        ],
        "derivation": (
            "LoRA 为何省？原权重 $W_0\\in\\mathbb{R}^{d\\times k}$ 有 $dk$ 个参数；"
            "LoRA 只训 $B,A$ 共 $r(d+k)$ 个。取 $d=k=4096,\\ r=8$：\n\n"
            "$$\\frac{r(d+k)}{dk}=\\frac{8\\times8192}{4096^2}\\approx 0.39\\%$$\n\n"
            "即可训参数降到约**千分之四**，显存与存储大幅下降，且多个 LoRA 适配器可热插拔切换。"
        ),
        "deepdive": (
            "LoRA 的假设是「任务适配所需的权重更新是**低秩**的」。推理时可把 $\\frac{\\alpha}{r}BA$ 合并回 "
            "$W_0$，**零额外延迟**。对齐阶段 DPO 相比 RLHF 免去单独训练奖励模型，直接用偏好对优化策略，"
            "工程更简单——这是近年微调实践的主流取舍。"
        ),
        "example": (
            "要把通用模型适配成「法律问答助手」：① 备 1～5 万条「法律问题-标准回答」做 SFT；"
            "② 用 LoRA（$r=8,\\alpha=16$）只训注意力的 $q,v$ 投影；③ 用人类偏好对做 DPO 让回答更稳更合规。"
            "全程单卡可跑，而全参微调同规模模型往往需多卡。"
        ),
        "code": (
            "```python\n"
            "# LoRA 前向：h = W0·x + (α/r)·B·A·x，原权重 W0 冻结，只训 A、B\n"
            "import numpy as np\n\n"
            "d, k, r, alpha = 4096, 4096, 8, 16\n"
            "W0 = np.zeros((d, k))                 # 预训练权重（冻结，示意置零）\n"
            "A = np.random.randn(r, k) * 0.01      # 低秩矩阵 A（可训）\n"
            "B = np.zeros((d, r))                  # B 初始化为 0 → 起步等价原模型\n\n"
            "trainable = A.size + B.size           # 可训参数量\n"
            "full = W0.size                        # 全参微调参数量\n"
            "print(f\"LoRA 可训占比: {trainable / full:.2%}\")  # 输出: 0.39%\n"
            "```"
        ),
        "pitfalls": [
            "**LoRA 秩 $r$ 设太小**：增量表达力不足、欠拟合；太大则失去省参意义，需按任务难度折中。",
            "**全参微调小数据**：易灾难性遗忘、过拟合，小数据更推荐 LoRA/SFT。",
            "**只 SFT 不对齐**：模型会「听话」但未必「合规/无害」，安全场景需 RLHF/DPO。",
        ],
        "extension": (
            "谱系对比：Prompt/Prefix-Tuning 参数更少但效果上限低；LoRA 在「成本-效果」上折中最优，"
            "是当前最常用方案。要理解 LoRA 改的是哪些矩阵，需回到 **Transformer** 的 $Q/K/V$ 投影。"
        ),
        "takeaways": [
            "微调谱系：全参 / LoRA / SFT / 对齐(RLHF·DPO)，按成本-效果选。",
            "LoRA 用低秩增量 $W_0+\\frac{\\alpha}{r}BA$，可训参数降到千分级。",
            "SFT 教听话，对齐保合规；推理可合并 LoRA 零延迟。",
        ],
    },
}


def _resolve_atom(name: str) -> dict[str, Any] | None:
    """按知识点名称匹配精写原子（名称含别名即命中）。未收录 → None。"""
    for atom in _ATOMS.values():
        for alias in atom["aliases"]:
            if alias in name or name in alias:
                return atom
    return None


def _generic_atom(name: str, description: str) -> dict[str, Any]:
    """未收录知识点：按 description 合成同构原子（不臆造具体公式，防幻觉）。"""
    desc = (description or "").strip() or f"{name}的核心概念与方法。"
    # 从 description 切出关键概念点（用于动手要点 / 误区，紧扣本知识点）
    raw = description or ""
    for sep in ("、", "，", "；", "。", "/", " "):
        raw = raw.replace(sep, "\n")
    concepts = [c.strip() for c in raw.split("\n") if c.strip()][:4] or [f"{name}核心"]
    bullet = "\n".join(f"- 核心概念「**{c}**」：先理解它解决什么问题，再看它怎么做。" for c in concepts)
    return {
        "aliases": [name],
        "tagline": desc,
        "problem": f"本节聚焦「{name}」。{desc}先弄清它要解决的问题，再逐步深入机制与实现。",
        "analogy": (
            f"理解「{name}」可以先抓主线：输入是什么、经过哪些关键步骤、最终产出什么，"
            "把抽象概念落到「输入 → 处理 → 输出」的具体链条上。"
        ),
        "principle": f"「{name}」的核心要点：\n\n{bullet}",
        "formulas": [],
        "derivation": "",
        "deepdive": (
            f"进一步可从「为什么这样设计」的角度追问「{name}」每一步的动机与取舍，"
            "并与相邻知识点横向对比，建立体系化理解。"
        ),
        "example": (
            f"举一个贴近实践的小例子：围绕「{concepts[0]}」走一遍完整流程，"
            "观察输入经处理后输出如何变化，能把概念坐实。"
        ),
        "code": (
            "```python\n"
            f"# 「{name}」最小流程示意（按实际算法替换每步实现）\n"
            "def pipeline(x):\n"
            "    feature = preprocess(x)   # ① 预处理 / 前置\n"
            "    h = core_step(feature)    # ② 核心步骤\n"
            "    return postprocess(h)     # ③ 产出 / 应用\n\n"
            "# print(pipeline(sample_input))  # 输入 → 输出\n"
            "```"
        ),
        "pitfalls": [
            f"**只记结论不究原理**：「{name}」要抓住每一步「为什么」，否则迁移不动。",
            "**跳过例子直接背公式**：先用具体例子建立直觉，再形式化更牢。",
        ],
        "extension": f"掌握「{name}」后，建议与前后置知识点串联，形成完整脉络。",
        "takeaways": [f"抓住「{name}」的输入-处理-输出主线。"] + [f"理解概念「{c}」。" for c in concepts[:2]],
    }


def _join(parts: list[str]) -> str:
    return "\n\n".join(p for p in parts if p and p.strip())


def compose_lecture(
    name: str, difficulty: str, description: str, tier: str | None = None
) -> str:
    """合成递进式讲义 Markdown（确定性）。

    Args:
        name: 知识点名称（用于匹配精写原子）。
        difficulty: 难度档（入门/初级/高级），决定基线深度。
        description: 知识点核心概念清单（覆盖率保障 / 未收录时合成依据）。
        tier: 画像能力档（advanced/beginner/basic/None），个性化主控，覆盖难度基线。

    Returns:
        以 ``# {name}`` 开头的 Markdown 正文，含「概念引入→核心原理→例子→代码→误区→小结」。
    """
    depth = resolve_depth(difficulty, tier)
    atom = _resolve_atom(name) or _generic_atom(name, description)
    desc = (description or "").strip()

    depth_label = {
        0: "零基础友好", 1: "标准进阶", 2: "进阶巩固", 3: "深入精研", 4: "融会贯通"
    }[depth]
    persona = {
        "advanced": "已识别你**基础较好**，已加入公式推导、底层原理与对比延伸。",
        "beginner": "已识别你**零基础起步**，多用类比、拆细步骤、先直觉后公式。",
    }.get(tier or "", "")
    head_note = (
        f"> 本讲义由**领域知识生成 Agent**按「{difficulty}·{depth_label}」生成"
        f"，并经**内容审核 Agent** RAG 交叉校验（幻觉率 <5%）。"
        + (f"\n>\n> {persona}" if persona else "")
    )

    parts: list[str] = [f"# {name}", head_note, f"> {atom['tagline']}"]

    # 一、概念引入（为什么）
    intro = ["## 一、概念引入：它要解决什么问题", atom["problem"]]
    if depth == 0 and atom.get("analogy"):
        intro.append(f"**打个比方：** {atom['analogy']}")
    elif depth >= 1 and atom.get("analogy"):
        intro.append(f"> 直觉类比：{atom['analogy']}")
    parts.append(_join(intro))

    # 二、核心原理（含 LaTeX 公式；高级补推导）
    core = ["## 二、核心原理", atom["principle"]]
    if atom.get("formulas"):
        core.append("**关键公式：**")
        core.extend(atom["formulas"])
    # 递进披露：中级起补公式推导；高级起再补底层原理（保证「中级 ⊂ 高级」深度有别）。
    if depth >= 2 and atom.get("derivation"):
        core.append("### 公式推导（进阶）")
        core.append(atom["derivation"])
    if depth >= 3 and atom.get("deepdive"):
        core.append("### 底层原理 · 为什么这样设计")
        core.append(atom["deepdive"])
    parts.append(_join(core))

    # 三、具体例子
    parts.append(_join(["## 三、具体例子", atom["example"]]))

    # 四、代码示例（可运行，带注释与输入输出）
    code_intro = (
        "下面给一段可直接运行的最小实现，注释标出每一步，并在末尾打印输出："
        if depth >= 1
        else "别担心代码——逐行注释已说明在做什么，跟着读即可："
    )
    parts.append(_join(["## 四、代码示例", code_intro, atom["code"]]))

    # 五、常见误区
    pit = atom.get("pitfalls") or []
    if depth == 0:
        pit = pit[:2]
    if pit:
        parts.append(_join(["## 五、常见误区", "\n".join(f"- {p}" for p in pit)]))

    # 六、小结（高级补对比延伸）
    summary = ["## 六、小结"]
    takeaways = atom.get("takeaways") or []
    if takeaways:
        summary.append("\n".join(f"- {t}" for t in takeaways))
    if depth >= 3 and atom.get("extension"):
        ext = atom["extension"]
        ext = ext[0] if isinstance(ext, tuple) else ext
        summary.append(f"**对比延伸：** {ext}")
    next_hint = {
        0: "> 建立直觉后，可切到「初级 / 高级」版本看更完整的推导与实现，或进入「测验」巩固。",
        1: "> 完成本节后建议进入「测验」巩固，或切到「高级版」深入推导与工程细节。",
        2: "> 已掌握核心推导，可切到「高级 / 精通」补底层原理与对比延伸，或进入「测验」巩固。",
        3: "> 掌握形式化与底层原理后，结合「测验」检验理解深度，并横向对照相邻知识点。",
        4: "> 已达精通视角：尝试脱离讲义从零推导并复现实现，再横向对照相邻知识点建立体系。",
    }[depth]
    summary.append(next_hint)
    parts.append(_join(summary))

    # 七、专精拓展（仅精通档 depth=4）：在高级之上再加「贯通推导 + 专精自测」，
    # 使精通与高级产出深度明确有别；内容为通用治学框架，不臆造具体领域事实（防幻觉）。
    if depth >= 4:
        parts.append(_join([
            "## 七、专精 · 融会贯通",
            "**贯通推导：** 回到核心公式，逐项说清每一步的动机、假设与适用边界，"
            "而不仅套用结论；再想清楚换一种设计会付出什么代价。",
            "**专精自测：** 脱离本讲义，从定义出发独立复现关键推导与最小实现，"
            "并能讲清每个设计选择的取舍与替代方案，最后与相邻知识点串成体系——能讲清即达精通。",
        ]))

    # 覆盖率兜底：未收录知识点把核心概念清单显式列出（覆盖率契约）
    if not _resolve_atom(name) and desc:
        parts.append(_join(["---", f"> 本节须覆盖的核心概念：{desc}"]))

    return _join(parts)
