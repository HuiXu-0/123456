"""数据库初始化与种子（B1）。

可重复执行（幂等）：
- create_all 本身幂等；
- 种子按主键存在性判断，已存在则跳过，绝不重复插入或覆盖。

种子内容（B1 验收要求）：
- 2 个账号：admin/admin123（role=admin）、learner_001/123456（role=learner）；
- 6 个核心知识点（接口文档 2.1）；
- 6 课学习路径 sequence 1-6（接口文档 2.3）；
- 两个用户的初始 Journey（hasDiagnosed=False / hasGeneratedPath=False）。

B6 追加种子：
- JobSnapshot：从 frontend/public/data/job-market/*.json 导入 4 岗位（接口文档 2.4/15.5）；
- ExternalResource：6 核心知识点 × 3-4 条精选外部资源（接口文档 8.6）。

运行方式：
- 应用启动时由 main.py lifespan 调用；
- 亦可独立执行：`python -m app.core.init_db`。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.core.migrations import upgrade
from app.core.security import hash_password
from app.models.entities import (  # noqa: F401  确保所有表在 create_all 前注册
    Document,
    ExternalResource,
    GenerationLog,
    JobSnapshot,
    Journey,
    KnowledgeDocument,
    KnowledgePoint,
    Lesson,
    PromptTemplate,
    QuizQuestion,
    StudentPortrait,
    User,
)

# 6 核心知识点（接口文档 2.1）：(id, name, lessonSeq, graphNodeId, description)
_KNOWLEDGE_POINTS = [
    ("ml", "机器学习基础", 1, "ml", "监督/无监督学习、特征与损失、过拟合与正则。"),
    ("nn", "神经网络基础", 2, "nn", "神经元模型、前向传播、激活函数与反向传播。"),
    ("dl", "深度学习原理", 3, "dl", "反向传播、梯度下降与优化器、正则与归一化。"),
    ("cnn", "CNN架构", 4, "cnn", "卷积、池化、感受野与经典卷积网络。"),
    ("transformer", "Transformer架构", 5, "transformer", "自注意力、多头注意力与位置编码。"),
    ("finetune", "大模型微调技术", 6, "finetune", "全参/LoRA/指令微调与对齐。"),
]

# 6 课学习路径（接口文档 2.3）：(sequence, topic, difficulty, status, progress, description)
_LESSONS = [
    (1, "机器学习基础", "入门", "completed", 100, "监督学习、损失函数与过拟合的直觉建立。"),
    (2, "神经网络基础", "初级", "completed", 100, "神经元、前向传播与激活函数。"),
    (3, "深度学习原理", "中级", "in_progress", 65, "反向传播、梯度下降与优化器。"),
    (4, "CNN架构", "中级", "pending", 0, "卷积、池化与经典卷积网络结构。"),
    (5, "Transformer架构", "高级", "pending", 0, "自注意力机制与多头注意力。"),
    (6, "大模型微调技术", "精通", "pending", 0, "LoRA 与指令微调实战。"),
]

# 种子测验题（接口文档 2.5 / 9.1，B2-b；C-fix 批2 扩到每 KP 10 题）：含
# single/multiple/boolean 客观题 + 末尾 1 道 short_answer 简答题（options 为空、
# correct_answer 存参考要点列表、explanation 存参考答案，AI 评分见 services.quiz）。
# nn_q1-q3 与前端 LearningResource.tsx 的 quizQuestions 逐字对齐；其余 KP 为领域题。
# 结构：kp_id -> [(question_id, type, text, options[(id,text)], correct_answer, explanation)]
_QUIZ_QUESTIONS: dict[str, list] = {
    "nn": [
        ("nn_q1", "single", "一个神经元的运算顺序是？",
         [("a", "激活函数 → 加权求和 → 加偏置"), ("b", "加权求和 → 加偏置 → 激活函数"),
          ("c", "加偏置 → 激活函数 → 加权求和")], "b",
         "神经元先对输入加权求和，再加上偏置，最后通过激活函数得到输出。"),
        ("nn_q2", "multiple", "以下哪些是常见的激活函数？（多选）",
         [("a", "ReLU"), ("b", "Sigmoid"), ("c", "Gradient"), ("d", "Tanh")], ["a", "b", "d"],
         "ReLU、Sigmoid、Tanh 都是常见激活函数；Gradient（梯度）是反向传播中的概念，不是激活函数。"),
        ("nn_q3", "boolean", "ReLU 激活函数有助于缓解梯度消失问题。",
         [("true", "正确"), ("false", "错误")], "true",
         "ReLU 在正区间梯度恒为 1，相比 Sigmoid 能有效缓解深层网络的梯度消失问题。"),
        ("nn_q4", "single", "在前向传播中，神经网络的作用是？",
         [("a", "根据当前参数由输入计算出预测输出"), ("b", "根据误差更新权重参数"),
          ("c", "计算损失函数对权重的梯度"), ("d", "对训练数据进行随机打乱")], "a",
         "前向传播是指由输入逐层计算到输出的过程；更新权重和计算梯度属于反向传播阶段。"),
        ("nn_q5", "single", "引入激活函数最主要的目的是？",
         [("a", "加快矩阵乘法的运算速度"), ("b", "为网络引入非线性表达能力"),
          ("c", "减少网络的参数数量"), ("d", "防止输入数据出现缺失值")], "b",
         "若没有非线性激活函数，多层线性变换的叠加仍等价于单层线性变换，网络无法拟合复杂的非线性关系。"),
        ("nn_q6", "boolean", "如果所有隐藏层都不使用激活函数（即仅做线性变换），那么无论叠加多少层，整个网络都等价于一个线性模型。",
         [("true", "正确"), ("false", "错误")], "true",
         "多个线性变换的复合仍然是线性变换，因此缺少非线性激活的多层网络等价于单层线性模型。"),
        ("nn_q7", "multiple", "关于梯度下降法，以下说法正确的是？（多选）",
         [("a", "沿损失函数负梯度方向更新参数"), ("b", "学习率控制每次参数更新的步长"),
          ("c", "学习率过大可能导致损失震荡甚至发散"), ("d", "梯度下降一定能找到全局最优解")], ["a", "b", "c"],
         "梯度下降沿负梯度方向更新参数，学习率决定步长且过大易震荡发散；但对于非凸损失，梯度下降只能保证收敛到局部最优或鞍点，不保证全局最优。"),
        ("nn_q8", "single", "偏置（bias）在神经元中的主要作用是？",
         [("a", "对输入特征进行归一化"), ("b", "为激活函数提供可平移的截距项"),
          ("c", "随机丢弃部分神经元"), ("d", "对权重施加 L2 惩罚")], "b",
         "偏置相当于线性函数中的截距，使决策边界可以平移，而不必强制通过原点，增强了模型的表达能力。"),
        ("nn_q9", "boolean", "将一个神经网络的所有权重都初始化为相同的常数（如全 0）是一种好的做法。",
         [("true", "正确"), ("false", "错误")], "false",
         "权重全部初始化为相同值会导致同一层神经元在前向和反向传播中完全对称、更新一致，无法学到不同特征，因此需要随机初始化打破对称性。"),
        ("nn_q10", "short_answer", "请简述神经网络中前向传播与反向传播的含义及二者的关系。",
         [], ["前向传播：由输入逐层计算得到预测输出", "反向传播：由输出端的损失反向逐层计算梯度", "反向传播依赖链式法则", "前向结果用于计算损失，反向梯度用于更新参数", "二者交替迭代完成训练"],
         "前向传播是指给定输入后，依据当前网络参数从输入层经隐藏层逐层计算，最终得到预测输出，并据此计算损失函数值。反向传播则是从输出端的损失出发，利用链式法则反向逐层计算损失对各层权重和偏置的梯度。二者紧密关联：前向传播提供了计算损失所需的中间结果（各层激活值），反向传播据此求得梯度，再由梯度下降等优化器更新参数。训练过程就是前向传播与反向传播不断交替迭代，直至损失收敛。"),
    ],
    "ml": [
        ("ml_q1", "single", "下列哪一项属于典型的无监督学习任务？",
         [("a", "根据房屋特征预测房价"), ("b", "对用户按行为相似度进行聚类"),
          ("c", "判断邮件是否为垃圾邮件"), ("d", "识别图片中的手写数字类别")], "b",
         "聚类不依赖标签，属于无监督学习；房价预测是回归、垃圾邮件判别和手写数字识别都属于有监督的分类/回归任务。"),
        ("ml_q2", "multiple", "以下哪些做法有助于缓解模型过拟合？（多选）",
         [("a", "增加训练数据量"), ("b", "加入 L1/L2 正则化"),
          ("c", "使用 Dropout"), ("d", "无限制地增加模型参数量")], ["a", "b", "c"],
         "增大数据量、正则化、Dropout 都能降低过拟合；而无限制增大模型容量通常会加剧过拟合。"),
        ("ml_q3", "boolean", "模型在训练集上表现很好但在测试集上表现明显变差，这通常是过拟合的表现。",
         [("true", "正确"), ("false", "错误")], "true",
         "训练误差低而泛化（测试）误差高，是过拟合的典型特征，说明模型记住了训练数据的噪声而非学到普适规律。"),
        ("ml_q4", "single", "将数据集划分为训练集、验证集和测试集，其中验证集的主要作用是？",
         [("a", "用于最终评估模型的泛化性能"), ("b", "用于训练时更新模型权重"),
          ("c", "用于调节超参数和模型选择"), ("d", "用于扩充训练样本数量")], "c",
         "验证集用于超参数调优与模型选择；训练集用于更新权重；测试集仅在最后用于无偏地评估泛化性能。"),
        ("ml_q5", "single", "关于偏差（bias）与方差（variance），下列说法正确的是？",
         [("a", "高偏差通常对应模型欠拟合"), ("b", "高方差通常对应模型欠拟合"),
          ("c", "增大模型复杂度一定同时降低偏差和方差"), ("d", "偏差和方差与模型复杂度无关")], "a",
         "高偏差意味着模型过于简单、欠拟合；高方差意味着模型对训练数据过于敏感、过拟合。增大复杂度通常降低偏差但升高方差，二者存在权衡。"),
        ("ml_q6", "multiple", "关于损失函数，以下说法正确的是？（多选）",
         [("a", "回归任务常用均方误差（MSE）作为损失"), ("b", "分类任务常用交叉熵作为损失"),
          ("c", "损失函数衡量预测值与真实值之间的差距"), ("d", "损失函数的值越大代表模型拟合越好")], ["a", "b", "c"],
         "MSE 常用于回归、交叉熵常用于分类，损失函数度量预测与真实标签的差距；训练目标是最小化损失，损失越小通常拟合越好。"),
        ("ml_q7", "boolean", "正则化通过在损失函数中加入对模型参数的惩罚项来限制模型复杂度，从而提升泛化能力。",
         [("true", "正确"), ("false", "错误")], "true",
         "L1/L2 正则化在原损失基础上增加参数范数惩罚，抑制权重过大，降低模型复杂度，有助于改善泛化、缓解过拟合。"),
        ("ml_q8", "single", "特征工程（feature engineering）的主要目的是？",
         [("a", "构造和选择对预测更有效的特征表示"), ("b", "增加测试集的样本数量"),
          ("c", "替代模型的训练过程"), ("d", "随机删除部分训练标签")], "a",
         "特征工程通过特征构造、变换、选择等手段，得到更有判别力的输入表示，从而提升模型效果，它服务于训练而非替代训练。"),
        ("ml_q9", "boolean", "测试集可以在训练或调参过程中反复使用，以便不断提升模型在测试集上的表现。",
         [("true", "正确"), ("false", "错误")], "false",
         "测试集应当只在最终评估时使用一次，若反复用于调参会造成信息泄露，使评估结果过于乐观，无法反映真实泛化能力。"),
        ("ml_q10", "short_answer", "请简述过拟合的概念，并列举至少三种缓解过拟合的常用方法。",
         [], ["过拟合：训练集表现好而测试集/泛化表现差", "原因是模型学到了噪声而非普遍规律", "方法一：增加训练数据或数据增强", "方法二：使用 L1/L2 正则化或 Dropout", "方法三：降低模型复杂度或早停"],
         "过拟合是指模型在训练集上拟合得很好（训练误差很低），但在未见过的测试数据上表现明显变差（泛化误差高）的现象，其本质是模型过度学习了训练数据中的噪声和偶然特征，而没有学到具有普遍性的规律。常用的缓解方法包括：（1）增加训练数据量或使用数据增强，让模型见到更多样的样本；（2）引入正则化手段，如 L1/L2 正则化、Dropout，抑制参数过大或神经元过度依赖；（3）适当降低模型复杂度，或使用早停（early stopping）在验证误差回升前结束训练；此外交叉验证、集成学习等也有一定帮助。"),
    ],
    "dl": [
        ("dl_q1", "single", "反向传播算法的核心数学工具是？",
         [("a", "贝叶斯定理"), ("b", "链式法则"),
          ("c", "拉格朗日乘子法"), ("d", "傅里叶变换")], "b",
         "反向传播本质上是利用微积分中的链式法则，将输出端损失对参数的梯度逐层向前分解计算。"),
        ("dl_q2", "multiple", "以下哪些是常见的优化器？（多选）",
         [("a", "SGD"), ("b", "Adam"), ("c", "RMSprop"), ("d", "Sigmoid")], ["a", "b", "c"],
         "SGD、Adam、RMSprop 都是常见的优化器；Sigmoid 是激活函数，不是优化器。"),
        ("dl_q3", "boolean", "Adam 优化器结合了动量（Momentum）和自适应学习率（类似 RMSprop）的思想。",
         [("true", "正确"), ("false", "错误")], "true",
         "Adam 同时维护梯度的一阶矩（动量）和二阶矩（自适应学习率）估计，因而兼具二者优点，收敛通常较快且稳定。"),
        ("dl_q4", "single", "Dropout 在训练过程中的主要作用是？",
         [("a", "按一定概率随机丢弃部分神经元以缓解过拟合"), ("b", "对每层输入做标准化处理"),
          ("c", "自适应地调整每个参数的学习率"), ("d", "增大网络的卷积核尺寸")], "a",
         "Dropout 在训练时以一定概率随机将部分神经元置零，迫使网络不过度依赖个别神经元，起到正则化、缓解过拟合的作用。"),
        ("dl_q5", "single", "Batch Normalization（批归一化）的主要作用不包括以下哪一项？",
         [("a", "缓解内部协变量偏移，稳定各层输入分布"), ("b", "允许使用更大的学习率，加速训练收敛"),
          ("c", "在一定程度上起到正则化效果"), ("d", "彻底消除网络对激活函数的需求")], "d",
         "BatchNorm 能稳定输入分布、允许更大学习率并带有轻微正则化效果，但它并不能替代激活函数，网络仍需非线性激活来获得表达能力。"),
        ("dl_q6", "boolean", "在很深的网络中，梯度在反向传播时连乘可能导致梯度消失或梯度爆炸。",
         [("true", "正确"), ("false", "错误")], "true",
         "反向传播中梯度按层连乘，当各层梯度因子持续小于 1 时会逐层衰减导致梯度消失，持续大于 1 时会逐层放大导致梯度爆炸。"),
        ("dl_q7", "multiple", "以下哪些方法有助于缓解深层网络的梯度消失/爆炸问题？（多选）",
         [("a", "使用 ReLU 等非饱和激活函数"), ("b", "使用残差连接（skip connection）"),
          ("c", "使用 Batch Normalization"), ("d", "盲目无限增加网络层数")], ["a", "b", "c"],
         "ReLU 避免饱和区梯度趋零、残差连接提供梯度捷径、BatchNorm 稳定分布，都有助于缓解梯度问题；而盲目加深网络反而会加剧梯度消失/爆炸。"),
        ("dl_q8", "single", "关于学习率（learning rate）的描述，下列哪项正确？",
         [("a", "学习率越大训练一定越好"), ("b", "学习率过大可能导致损失震荡或发散"),
          ("c", "学习率越小一定能更快收敛"), ("d", "学习率对训练过程没有影响")], "b",
         "学习率过大会使参数更新步长过大、损失震荡甚至发散；学习率过小则收敛缓慢、易陷入局部区域，故需要合理选择并常配合学习率调度。"),
        ("dl_q9", "boolean", "随机梯度下降（SGD）每次只用一个或一小批样本估计梯度并更新参数。",
         [("true", "正确"), ("false", "错误")], "true",
         "SGD（及小批量 SGD）每次使用单个或一小批样本估计梯度进行更新，相比全量梯度下降计算更高效，且引入的噪声有助于跳出部分局部极小。"),
        ("dl_q10", "short_answer", "请简述深层神经网络中梯度消失问题的成因，并给出至少两种缓解该问题的方法。",
         [], ["成因：反向传播中梯度按层连乘", "饱和激活函数（如 Sigmoid）导数小于 1 导致连乘衰减", "方法一：改用 ReLU 等非饱和激活函数", "方法二：使用残差连接提供梯度捷径", "方法三：使用 BatchNorm 或合理的权重初始化"],
         "梯度消失问题是指在深层网络的反向传播过程中，损失对浅层参数的梯度变得极小，导致浅层几乎无法更新。其成因在于：根据链式法则，梯度是各层局部导数的连乘，当使用 Sigmoid、Tanh 等饱和激活函数时，其导数在饱和区接近 0（普遍小于 1），多层连乘后梯度迅速衰减趋于零。常见缓解方法包括：（1）改用 ReLU 等非饱和激活函数，使正区间导数恒为 1，避免连乘衰减；（2）引入残差连接（skip connection），为梯度提供直达浅层的捷径；（3）使用 Batch Normalization 稳定各层输入分布，并配合合理的权重初始化（如 He 初始化），从而保持梯度量级稳定。"),
    ],
    "cnn": [
        ("cnn_q1", "single", "卷积神经网络中“权值共享”指的是？",
         [("a", "同一个卷积核在整张特征图的不同位置使用相同的参数"), ("b", "所有卷积核共用同一个偏置"),
          ("c", "不同层之间共用同一组权重"), ("d", "训练和测试阶段共用同一批数据")], "a",
         "权值共享是指同一个卷积核以相同的参数在输入的各个空间位置滑动计算，这大幅减少了参数量并赋予模型平移不变性。"),
        ("cnn_q2", "multiple", "以下关于池化（pooling）的说法，正确的有？（多选）",
         [("a", "池化可以降低特征图的空间尺寸"), ("b", "最大池化取窗口内的最大值"),
          ("c", "池化有助于增强一定的平移不变性"), ("d", "池化层包含大量可学习的权重参数")], ["a", "b", "c"],
         "池化能下采样减小特征图尺寸、最大池化取窗口最大值、并增强平移不变性；但常见的最大/平均池化没有可学习参数。"),
        ("cnn_q3", "boolean", "残差网络（ResNet）通过引入跳跃连接（shortcut）使得训练更深的网络成为可能。",
         [("true", "正确"), ("false", "错误")], "true",
         "ResNet 的残差/跳跃连接让网络学习残差映射并为梯度提供捷径，有效缓解了深层网络的退化与梯度消失问题，使训练上百层网络成为可能。"),
        ("cnn_q4", "single", "相比全连接层，卷积层处理图像的主要优势是？",
         [("a", "利用局部连接与权值共享大幅减少参数量"), ("b", "完全不需要任何激活函数"),
          ("c", "天然不需要反向传播即可训练"), ("d", "输出维度一定大于输入维度")], "a",
         "卷积层通过局部感受野（局部连接）和权值共享，在保留空间结构信息的同时显著减少参数量，更适合处理图像这类高维数据。"),
        ("cnn_q5", "single", "在卷积操作中，关于步幅（stride）的描述，正确的是？",
         [("a", "步幅增大通常会使输出特征图变小"), ("b", "步幅只能取 1"),
          ("c", "步幅决定卷积核的通道数"), ("d", "步幅越大输出特征图一定越大")], "a",
         "步幅是卷积核每次滑动的像素间隔，步幅增大意味着采样更稀疏，输出特征图的空间尺寸随之减小。"),
        ("cnn_q6", "boolean", "在卷积前对输入进行 padding（填充），可以用来控制输出特征图的尺寸，例如保持其与输入相同。",
         [("true", "正确"), ("false", "错误")], "true",
         "通过在输入边缘填充（如零填充），可以补偿卷积带来的边缘收缩，从而控制输出尺寸，常用的 same padding 即可使输出与输入空间尺寸保持一致。"),
        ("cnn_q7", "multiple", "关于感受野（receptive field），以下说法正确的是？（多选）",
         [("a", "感受野是输出某个位置所对应的输入区域范围"), ("b", "网络越深，神经元的感受野通常越大"),
          ("c", "增大卷积核或加入池化都可以扩大感受野"), ("d", "感受野与网络深度无关")], ["a", "b", "c"],
         "感受野指输出特征中某个位置受输入哪块区域影响；随着层数加深、卷积核增大或加入池化/下采样，感受野会逐渐扩大，因此与网络深度密切相关。"),
        ("cnn_q8", "single", "卷积层输出的特征图（feature map）通常表示？",
         [("a", "卷积核在输入上提取到的某种特征的空间响应"), ("b", "训练集的原始标签分布"),
          ("c", "优化器的学习率变化曲线"), ("d", "损失函数随迭代的下降曲线")], "a",
         "每个卷积核在输入上滑动卷积后得到一张特征图，反映该卷积核所检测特征（如边缘、纹理）在不同空间位置上的激活强度。"),
        ("cnn_q9", "boolean", "卷积层与池化层一样，都没有可学习的参数。",
         [("true", "正确"), ("false", "错误")], "false",
         "卷积层的卷积核权重和偏置是可学习参数，需要通过训练更新；而最大池化、平均池化等常见池化操作通常没有可学习参数。"),
        ("cnn_q10", "short_answer", "请说明卷积神经网络相比全连接网络在处理图像时的主要优势，并简述残差连接（ResNet）的作用。",
         [], ["局部连接：每个神经元只连接局部感受野", "权值共享：同一卷积核共享参数，减少参数量", "保留图像空间结构并具平移不变性", "残差连接为梯度提供捷径，缓解梯度消失/退化", "使训练更深的网络成为可能"],
         "卷积神经网络在处理图像时相比全连接网络具有显著优势：其一是局部连接，每个神经元只与输入的一个局部感受野相连，符合图像的局部相关性；其二是权值共享，同一个卷积核以相同参数在整张图上滑动，既大幅减少了参数量，又带来了平移不变性；同时卷积保留了图像的二维空间结构信息。残差连接（ResNet）则通过引入跳跃连接，让网络学习输入与输出之间的残差映射，并为反向传播的梯度提供一条直达浅层的捷径，从而有效缓解了深层网络的梯度消失与退化问题，使得训练数十甚至上百层的深层网络成为可能。"),
    ],
    "transformer": [
        ("transformer_q1", "single", "自注意力机制（self-attention）中的 Q、K、V 分别指？",
         [("a", "查询（Query）、键（Key）、值（Value）"), ("b", "权重、偏置、梯度"),
          ("c", "卷积、池化、归一化"), ("d", "编码器、解码器、分类器")], "a",
         "自注意力中 Q（Query）、K（Key）、V（Value）均由输入线性变换得到，通过 Q 与 K 的相似度计算注意力权重，再对 V 加权求和。"),
        ("transformer_q2", "multiple", "以下哪些是 Transformer 编码器层的组成部分？（多选）",
         [("a", "多头自注意力"), ("b", "前馈神经网络（FFN）"),
          ("c", "残差连接与层归一化（LayerNorm）"), ("d", "最大池化层")], ["a", "b", "c"],
         "Transformer 编码器层由多头自注意力、前馈网络（FFN）以及每个子层后的残差连接与 LayerNorm 组成；它不使用最大池化层。"),
        ("transformer_q3", "boolean", "由于自注意力本身不包含位置信息，Transformer 需要引入位置编码（positional encoding）来表示序列顺序。",
         [("true", "正确"), ("false", "错误")], "true",
         "自注意力对输入是置换等变的、本身不感知顺序，因此需通过位置编码（如正弦位置编码或可学习位置嵌入）为模型注入序列的位置信息。"),
        ("transformer_q4", "single", "缩放点积注意力中，除以 √dk（键向量维度的平方根）进行缩放的主要原因是？",
         [("a", "防止点积结果过大导致 softmax 梯度过小"), ("b", "为了减少注意力头的数量"),
          ("c", "为了让注意力权重之和不为 1"), ("d", "为了增加模型参数量")], "a",
         "当维度 dk 较大时，Q·K 点积的数值方差会变大，使 softmax 进入饱和区、梯度极小；除以 √dk 进行缩放可稳定数值、保持梯度，便于训练。"),
        ("transformer_q5", "single", "多头注意力（multi-head attention）的主要作用是？",
         [("a", "让模型在多个不同的表示子空间中并行关注信息"), ("b", "减少模型的总参数量到原来的一半"),
          ("c", "替代位置编码的功能"), ("d", "使模型只能关注序列中的单个位置")], "a",
         "多头注意力将表示拆分到多个子空间，各头并行学习不同的注意力模式（如不同的依赖关系），再拼接融合，从而增强模型的表达能力。"),
        ("transformer_q6", "boolean", "在标准 Transformer 中，每个子层（自注意力或前馈网络）的输出都会经过残差连接再做层归一化。",
         [("true", "正确"), ("false", "错误")], "true",
         "标准 Transformer 对每个子层采用 “残差连接 + LayerNorm” 的结构（如 LayerNorm(x + Sublayer(x))），有助于稳定训练并缓解深层梯度问题。"),
        ("transformer_q7", "multiple", "关于自注意力机制相对于 RNN 的优势，以下说法正确的是？（多选）",
         [("a", "可以并行计算整个序列，而非按时间步串行"), ("b", "更容易建模序列中的长距离依赖"),
          ("c", "任意两个位置之间的交互路径长度为常数"), ("d", "完全不需要任何形式的位置信息")], ["a", "b", "c"],
         "自注意力可对序列并行计算、任意两位置直接交互（路径长度为常数），更易捕捉长距离依赖；但它本身不含位置信息，仍需位置编码，故 (d) 错误。"),
        ("transformer_q8", "single", "Transformer 中的前馈网络（FFN）子层通常是？",
         [("a", "对每个位置独立作用的两层全连接网络（含非线性激活）"), ("b", "一个循环神经网络"),
          ("c", "一个二维卷积网络"), ("d", "一个图神经网络")], "a",
         "FFN 是对序列中每个位置独立、且参数共享地作用的两层全连接网络（中间含 ReLU/GELU 等非线性），用于对注意力输出做逐位置的非线性变换。"),
        ("transformer_q9", "boolean", "自注意力计算时，每个位置只能关注到它前面的位置，而无法关注其后的位置。",
         [("true", "正确"), ("false", "错误")], "false",
         "在编码器的双向自注意力中，每个位置可以关注序列中所有位置；只有在解码器的掩码（masked）自注意力中，才会限制每个位置只能关注其自身及之前的位置。"),
        ("transformer_q10", "short_answer", "请简述自注意力（self-attention）的基本计算过程，并说明位置编码的作用。",
         [], ["由输入线性变换得到 Q、K、V", "计算 Q 与 K 的点积相似度并缩放（除以 √dk）", "经 softmax 得到注意力权重", "用注意力权重对 V 加权求和得到输出", "位置编码为模型注入序列顺序信息"],
         "自注意力的基本计算过程为：首先由输入序列经过三组不同的线性变换分别得到查询 Q、键 K 和值 V；然后计算每个查询与所有键的点积相似度，并除以 √dk 进行缩放以稳定数值；接着对缩放后的相似度按行做 softmax，得到注意力权重（表示各位置之间的关注程度）；最后用这些权重对 V 加权求和，得到每个位置的输出表示。由于自注意力对输入是置换等变的、本身不感知元素的先后顺序，因此需要引入位置编码（如正弦位置编码或可学习的位置嵌入），将序列的位置信息加入到输入表示中，使模型能够区分不同位置、建模顺序关系。"),
    ],
    "finetune": [
        ("finetune_q1", "single", "LoRA 微调方法的核心思想是？",
         [("a", "冻结预训练权重，只训练注入的低秩增量矩阵"), ("b", "对模型所有参数进行全量更新"),
          ("c", "重新从零开始训练整个模型"), ("d", "仅修改模型的输入分词器")], "a",
         "LoRA 在冻结原始预训练权重的同时，为权重矩阵注入一对低秩矩阵（A、B）并只训练它们，用低秩增量近似全量更新，从而大幅减少可训练参数。"),
        ("finetune_q2", "multiple", "以下哪些属于参数高效微调（PEFT）方法？（多选）",
         [("a", "LoRA"), ("b", "Adapter"), ("c", "Prompt Tuning"), ("d", "全参数微调（Full Fine-tuning）")], ["a", "b", "c"],
         "LoRA、Adapter、Prompt Tuning 都只训练少量参数，属于参数高效微调（PEFT）；全参数微调更新模型全部参数，不属于 PEFT。"),
        ("finetune_q3", "boolean", "相比全参数微调，参数高效微调（PEFT）通常只更新少量参数，显存占用和存储成本更低。",
         [("true", "正确"), ("false", "错误")], "true",
         "PEFT 方法（如 LoRA、Adapter）冻结大部分预训练权重，只训练极少量新增参数，因而显著降低了显存占用、训练成本和模型存储开销。"),
        ("finetune_q4", "single", "QLoRA 相比标准 LoRA 的主要改进是？",
         [("a", "在量化（如 4-bit）的基础模型上做 LoRA，进一步降低显存占用"), ("b", "放弃低秩假设，改为全参数训练"),
          ("c", "去掉冻结权重，训练时更新全部参数"), ("d", "仅适用于卷积神经网络")], "a",
         "QLoRA 将基础模型量化到低比特（如 4-bit NF4）后再叠加 LoRA 进行微调，在几乎不损失效果的前提下进一步大幅降低显存占用，使大模型可在单卡上微调。"),
        ("finetune_q5", "single", "指令微调（instruction tuning / SFT）的主要目的是？",
         [("a", "用带指令-回答的有监督数据让模型学会遵循人类指令"), ("b", "对模型进行无监督的预训练"),
          ("c", "压缩模型体积以便部署"), ("d", "替换模型的位置编码方式")], "a",
         "指令微调（SFT）使用大量“指令-期望回答”的有监督样本对预训练模型进行微调，使其更好地理解并遵循人类指令，提升对话与任务执行能力。"),
        ("finetune_q6", "boolean", "RLHF（基于人类反馈的强化学习）常用于让大模型的输出更符合人类偏好和价值观。",
         [("true", "正确"), ("false", "错误")], "true",
         "RLHF 通过人类偏好数据训练奖励模型，再用强化学习优化策略，使模型输出更符合人类期望，是大模型对齐（alignment）的常用手段。"),
        ("finetune_q7", "multiple", "以下关于大模型对齐与偏好优化的说法，正确的有？（多选）",
         [("a", "RLHF 通常包含训练奖励模型并用 RL 优化策略两个环节"), ("b", "DPO 是一种无需显式奖励模型的偏好优化方法"),
          ("c", "对齐的目标之一是让模型输出更安全、更符合人类偏好"), ("d", "对齐只能通过修改模型结构来实现")], ["a", "b", "c"],
         "RLHF 一般先训奖励模型再用 RL 优化策略；DPO 直接基于偏好数据优化、无需显式奖励模型；对齐旨在提升输出的安全性与人类偏好契合度。对齐主要靠数据与训练目标实现，并不要求修改模型结构，故 (d) 错误。"),
        ("finetune_q8", "single", "在 LoRA 微调中，预训练的原始权重通常处于什么状态？",
         [("a", "被冻结，不参与梯度更新"), ("b", "被随机重新初始化"),
          ("c", "每步都被全量更新"), ("d", "被直接删除")], "a",
         "LoRA 微调时冻结预训练原始权重（不计算其梯度、不更新），只训练新增的低秩矩阵，从而在保留预训练知识的同时实现高效适配。"),
        ("finetune_q9", "boolean", "全参数微调在数据量充足时往往效果上限更高，但其显存和存储成本也远高于 PEFT 方法。",
         [("true", "正确"), ("false", "错误")], "true",
         "全参数微调更新模型全部参数，在数据充足、算力允许时往往能达到更高的效果上限，但同时带来更高的显存占用、训练成本以及为每个任务保存完整权重的存储开销。"),
        ("finetune_q10", "short_answer", "请简述 LoRA 的基本原理，并说明它相比全参数微调的优势。",
         [], ["冻结预训练原始权重", "为权重矩阵注入低秩矩阵 A、B 并只训练它们", "用低秩增量近似全量权重更新", "可训练参数大幅减少，显存与存储成本低", "便于为不同任务保存和切换小型 LoRA 权重"],
         "LoRA（Low-Rank Adaptation）的基本原理是：在微调时冻结预训练模型的原始权重，不对其计算梯度，而是在需要适配的权重矩阵旁注入一对低秩矩阵 A 和 B（其乘积 BA 作为权重增量 ΔW），仅训练这两个低秩矩阵，从而用低秩近似来逼近全量参数更新的效果。相比全参数微调，LoRA 的优势在于：可训练参数量大幅减少（通常只占原模型的极小比例），显著降低了训练时的显存占用和计算成本；每个下游任务只需保存一份很小的 LoRA 权重而非整份模型副本，便于存储与多任务切换；同时由于冻结了原始权重，较好地保留了预训练知识、降低了灾难性遗忘的风险。"),
    ],
}

# 热门岗位固定顺序（接口文档 5.1 契约示例序，与前端 HOT_JOBS 一致）
_HOT_JOB_ORDER = ["llm-app", "algo-engineer", "ml-engineer", "data-analyst"]

# 精选外部资源种子（接口文档 8.6）：kp_id -> [(序号, type, title, source, url,
# embed, duration, relevance, credibility, reason)]。nn 四条与前端
# ResourceAggregator.tsx 演示数据对齐；其余 KP 为同口径人工精选。
# 生产路径：聚合 Agent 接搜索 API 检索 + critic 评分后定期刷新本表（见 entities.ExternalResource 注释）。
_EXTERNAL_RESOURCES: dict[str, list[tuple]] = {
    "ml": [
        (1, "视频", "李宏毅《机器学习》系列课程", "B站 · 李宏毅", "https://www.bilibili.com/video/BV1Wv411h7kN", "https://player.bilibili.com/player.html?bvid=BV1Wv411h7kN", "系列", 96, 94, "中文领域最权威的机器学习入门课，从回归到深度学习循序渐进。"),
        (2, "课程", "Machine Learning Specialization（Andrew Ng）", "Coursera · DeepLearning.AI", "https://www.coursera.org/specializations/machine-learning-introduction", None, "系列", 94, 97, "吴恩达经典课程重制版，监督学习与正则化讲解清晰，配套练习完善。"),
        (3, "课程", "Google 机器学习速成课程", "developers.google.com", "https://developers.google.com/machine-learning/crash-course", None, "15 小时", 90, 93, "短平快的工程视角入门，交互式可视化帮助建立损失与过拟合直觉。"),
        (4, "文档", "scikit-learn 用户指南", "scikit-learn.org", "https://scikit-learn.org/stable/user_guide.html", None, "实操", 88, 95, "官方文档配可运行示例，把监督/无监督算法落到代码实践。"),
    ],
    "nn": [
        (1, "视频", "3Blue1Brown：神经网络是什么？", "YouTube · 3Blue1Brown", "https://www.youtube.com/watch?v=aircAruvnKk", "https://www.youtube.com/embed/aircAruvnKk", "19:13", 98, 97, "可视化讲解神经元与权重，直观契合你当前「神经网络基础」知识点。"),
        (2, "课程", "CS231n：卷积神经网络（Stanford）", "Stanford 公开课", "https://cs231n.github.io/", None, "系列", 92, 96, "权威课程，从神经网络基础平滑过渡到 CNN，匹配你的下一步学习路径。"),
        (3, "论文", "Attention Is All You Need", "arXiv:1706.03762", "https://arxiv.org/abs/1706.03762", None, "15 页", 78, 99, "Transformer 奠基论文，作为你「待提升领域」的进阶拓展读物。"),
        (4, "文档", "PyTorch 官方教程 · 构建神经网络", "pytorch.org", "https://pytorch.org/tutorials/beginner/basics/buildmodel_tutorial.html", None, "实操", 90, 95, "官方动手教程，把讲义中的前向传播落到 nn.Module 代码实践。"),
    ],
    "dl": [
        (1, "视频", "3Blue1Brown：梯度下降是如何学习的", "YouTube · 3Blue1Brown", "https://www.youtube.com/watch?v=IHZwWFHWa-w", "https://www.youtube.com/embed/IHZwWFHWa-w", "21:01", 95, 96, "用可视化把梯度下降与损失曲面讲透，是反向传播的最佳直觉铺垫。"),
        (2, "文档", "《深度学习》（花书）在线版", "deeplearningbook.org", "https://www.deeplearningbook.org/", None, "教材", 92, 98, "Goodfellow 等人的领域奠基教材，优化与正则化章节与本知识点强相关。"),
        (3, "课程", "Deep Learning Specialization（吴恩达）", "Coursera · DeepLearning.AI", "https://www.coursera.org/specializations/deep-learning", None, "系列", 93, 96, "系统覆盖反向传播、优化器与调参实践，作业可逐步实现训练循环。"),
        (4, "文档", "PyTorch 官方教程 · 优化模型参数", "pytorch.org", "https://pytorch.org/tutorials/beginner/basics/optimization_tutorial.html", None, "实操", 88, 95, "把损失函数、优化器与训练循环落到可运行代码，巩固梯度下降理解。"),
    ],
    "cnn": [
        (1, "课程", "CS231n 笔记 · 卷积网络", "Stanford · cs231n.github.io", "https://cs231n.github.io/convolutional-networks/", None, "长文", 96, 96, "卷积/池化/感受野的权威讲义，配大量图示，是 CNN 架构的标准参考。"),
        (2, "文档", "CNN Explainer 交互可视化", "Georgia Tech · poloclub", "https://poloclub.github.io/cnn-explainer/", None, "交互", 93, 90, "在浏览器里逐层拖动观察卷积运算，把抽象的特征图变得可见。"),
        (3, "论文", "Deep Residual Learning（ResNet）", "arXiv:1512.03385", "https://arxiv.org/abs/1512.03385", None, "12 页", 86, 98, "经典卷积网络的里程碑，理解残差连接如何让网络更深。"),
        (4, "视频", "3Blue1Brown：什么是卷积？", "YouTube · 3Blue1Brown", "https://www.youtube.com/watch?v=KuXjwB4LzSA", "https://www.youtube.com/embed/KuXjwB4LzSA", "23:01", 94, 97, "可视化拆解卷积运算的滑动窗口本质，是理解卷积层的最佳直觉入口。"),
    ],
    "transformer": [
        (1, "论文", "Attention Is All You Need", "arXiv:1706.03762", "https://arxiv.org/abs/1706.03762", None, "15 页", 97, 99, "Transformer 奠基论文，自注意力与多头注意力的第一手定义。"),
        (2, "文档", "The Illustrated Transformer", "jalammar.github.io", "https://jalammar.github.io/illustrated-transformer/", None, "图解", 96, 92, "最广为引用的图解教程，把 Q/K/V 与多头注意力拆到每一步矩阵运算。"),
        (3, "视频", "李宏毅：Transformer 详解", "YouTube · Hung-yi Lee", "https://www.youtube.com/watch?v=ugWDIIOHtPA", "https://www.youtube.com/embed/ugWDIIOHtPA", None, 94, 95, "中文系统讲解自注意力机制与位置编码，配课件推导细致。"),
        (4, "文档", "The Annotated Transformer", "Harvard NLP", "https://nlp.seas.harvard.edu/annotated-transformer/", None, "实操", 90, 94, "逐行代码复现原论文，读完即可亲手实现一个 Transformer。"),
    ],
    "finetune": [
        (1, "论文", "LoRA: Low-Rank Adaptation of LLMs", "arXiv:2106.09685", "https://arxiv.org/abs/2106.09685", None, "13 页", 96, 98, "低秩适配微调的奠基论文，理解「冻结原权重 + 低秩增量」核心思想。"),
        (2, "文档", "Hugging Face PEFT 官方文档", "huggingface.co", "https://huggingface.co/docs/peft", None, "实操", 94, 95, "参数高效微调的事实标准库，LoRA/Adapter/Prompt Tuning 即插即用。"),
        (3, "课程", "Hugging Face LLM Course · 微调章节", "huggingface.co/learn", "https://huggingface.co/learn/llm-course/chapter3/1", None, "系列", 91, 94, "手把手带你完成一次完整的预训练模型微调流程。"),
        (4, "论文", "QLoRA: Efficient Finetuning of Quantized LLMs", "arXiv:2305.14314", "https://arxiv.org/abs/2305.14314", None, "23 页", 88, 97, "量化 + LoRA 的进阶组合，单卡微调大模型的代表性工作。"),
    ],
}

# 种子账号：(id, username, displayName, password, role)
_USERS = [
    ("u_10000", "admin", "管理员", "admin123", "admin"),
    ("u_10001", "learner_001", "learner_001", "123456", "learner"),
]


def _seed_users(db: Session) -> None:
    for uid, username, display, pwd, role in _USERS:
        if db.get(User, uid) is not None:
            continue
        db.add(
            User(
                id=uid,
                username=username,
                display_name=display,
                password_hash=hash_password(pwd),
                role=role,
            )
        )
        # 每个用户初始旅程：未诊断、未生成路径
        db.add(Journey(user_id=uid, has_diagnosed=False, has_generated_path=False))


def _seed_knowledge_points(db: Session) -> None:
    for kp_id, name, seq, node, desc in _KNOWLEDGE_POINTS:
        if db.get(KnowledgePoint, kp_id) is not None:
            continue
        db.add(
            KnowledgePoint(
                id=kp_id, name=name, lesson_seq=seq, graph_node_id=node, description=desc
            )
        )


def _seed_catalog(db: Session) -> None:
    """录入《AI知识体系设计-v3》78 点体系（幂等）。

    须在 _seed_knowledge_points 之后：先建好 6 核心别名行，再由 catalog.seed 补其体系
    元信息并新增其余 72 点（见 knowledge_catalog.seed）。**只录结构与元信息、不录内容。**
    """
    from app.services import knowledge_catalog

    knowledge_catalog.seed(db)


def _seed_lessons(db: Session) -> None:
    for seq, topic, diff, status, progress, desc in _LESSONS:
        if db.get(Lesson, seq) is not None:
            continue
        db.add(
            Lesson(
                sequence=seq,
                topic=topic,
                difficulty=diff,
                status=status,
                progress=progress,
                description=desc,
            )
        )


def _seed_quiz_questions(db: Session) -> None:
    for kp_id, questions in _QUIZ_QUESTIONS.items():
        for qid, qtype, text, options, correct, explanation in questions:
            if db.get(QuizQuestion, qid) is not None:
                continue
            db.add(
                QuizQuestion(
                    question_id=qid,
                    kp_id=kp_id,
                    question_type=qtype,
                    question_text=text,
                    options=[{"option_id": oid, "option_text": otext} for oid, otext in options],
                    correct_answer=correct,
                    explanation=explanation,
                )
            )


def _seed_job_snapshots(db: Session) -> None:
    """从 frontend/public/data/job-market/*.json 导入岗位快照（接口文档 2.4/15.5）。

    目录缺失/单文件损坏不致命（跳过，可重复执行补齐）；fetched_at 取 payload
    的 fetchedAt（响应中 fetchedAt 仍以 payload 为准，前端 timeAgo 渲染）。
    """
    source_dir = Path(settings.job_market_dir)
    for path in sorted(source_dir.glob("*.json")) if source_dir.is_dir() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        job_id = payload.get("id")
        if not job_id or db.get(JobSnapshot, job_id) is not None:
            continue
        try:
            fetched_at = datetime.fromisoformat(payload.get("fetchedAt", ""))
        except ValueError:
            fetched_at = None
        order = (
            _HOT_JOB_ORDER.index(job_id) + 1 if job_id in _HOT_JOB_ORDER else 99
        )
        db.add(
            JobSnapshot(
                id=job_id,
                name=payload.get("name", job_id),
                payload=payload,
                sort_order=order,
                **({"fetched_at": fetched_at} if fetched_at else {}),
            )
        )


def _seed_external_resources(db: Session) -> None:
    for kp_id, rows in _EXTERNAL_RESOURCES.items():
        for seq, rtype, title, source, url, embed, duration, rel, cred, reason in rows:
            res_id = f"{kp_id}-r{seq}"
            if db.get(ExternalResource, res_id) is not None:
                continue
            db.add(
                ExternalResource(
                    id=res_id,
                    kp_id=kp_id,
                    type=rtype,
                    title=title,
                    source=source,
                    url=url,
                    embed=embed,
                    duration=duration,
                    relevance=rel,
                    credibility=cred,
                    reason=reason,
                )
            )


def _migrate_b6() -> None:
    """B6 轻量迁移：既有开发库 job_snapshots 缺 sort_order 列时补齐。

    create_all 只建新表不加列；SQLite 支持 ADD COLUMN，幂等（先查 PRAGMA）。
    """
    inspector = inspect(engine)
    if "job_snapshots" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("job_snapshots")}
    if "sort_order" not in columns:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE job_snapshots ADD COLUMN sort_order INTEGER DEFAULT 0")
            )


def _migrate_b9() -> None:
    """B9 轻量迁移：既有开发库 users 表缺 is_active / last_active_at 列时补齐。

    create_all 只建新表不加列；SQLite 支持 ADD COLUMN，幂等（先查 PRAGMA）。
    既有行 is_active 默认 1（启用），last_active_at 为 NULL（从未登录）。
    """
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("users")}
    with engine.begin() as conn:
        if "is_active" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT 1"))
        if "last_active_at" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN last_active_at DATETIME"))


def _migrate_path_plan() -> None:
    """轻量迁移：既有开发库 journeys 表缺 path_plan 列时补齐（真实路径规划存储）。

    create_all 只建新表不加列；SQLite 支持 ADD COLUMN，幂等（先查 PRAGMA）。
    既有行 path_plan 默认 NULL（尚未生成个性化路径 → GET 回落全局种子路径）。
    """
    inspector = inspect(engine)
    if "journeys" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("journeys")}
    with engine.begin() as conn:
        if "path_plan" not in columns:
            conn.execute(text("ALTER TABLE journeys ADD COLUMN path_plan JSON"))
        if "path_fingerprint" not in columns:  # C2：路径缓存指纹（画像变即失效重算）
            conn.execute(text("ALTER TABLE journeys ADD COLUMN path_fingerprint VARCHAR(32)"))
        if "path_narrative" not in columns:  # C2：整体规划叙述缓存（GET 回 summary.narrative）
            conn.execute(text("ALTER TABLE journeys ADD COLUMN path_narrative TEXT"))


def _migrate_mastery_score() -> None:
    """C2 轻量迁移：既有开发库 mastery 表缺 score/confidence/score_source 列时补齐。

    create_all 只建新表不加列；SQLite 支持 ADD COLUMN，幂等（先查 PRAGMA）。
    既有行三列默认 NULL（能力分未测 → 4.4 雷达回落未测/低基线，不臆造）。
    """
    inspector = inspect(engine)
    if "mastery" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("mastery")}
    with engine.begin() as conn:
        if "score" not in columns:
            conn.execute(text("ALTER TABLE mastery ADD COLUMN score INTEGER"))
        if "confidence" not in columns:
            conn.execute(text("ALTER TABLE mastery ADD COLUMN confidence FLOAT"))
        if "score_source" not in columns:
            conn.execute(text("ALTER TABLE mastery ADD COLUMN score_source VARCHAR(16)"))


def _migrate_genlog_source() -> None:
    """文档学习轻量迁移：既有开发库 generation_logs 表缺 source/doc_id/doc_title 列时补齐。

    create_all 只建新表不加列；SQLite 支持 ADD COLUMN，幂等（先查 PRAGMA）。
    既有内置课程行三列默认 NULL（= builtin，behavior 不变）；文档学习行写 "document"。
    """
    inspector = inspect(engine)
    if "generation_logs" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("generation_logs")}
    with engine.begin() as conn:
        if "source" not in columns:
            conn.execute(text("ALTER TABLE generation_logs ADD COLUMN source VARCHAR(16)"))
        if "doc_id" not in columns:
            conn.execute(text("ALTER TABLE generation_logs ADD COLUMN doc_id VARCHAR(64)"))
        if "doc_title" not in columns:
            conn.execute(text("ALTER TABLE generation_logs ADD COLUMN doc_title VARCHAR(256)"))


def _migrate_kp_catalog() -> None:
    """知识体系录入轻量迁移：既有开发库 knowledge_points 表缺体系元信息列时补齐。

    create_all 只建新表不加列；SQLite 支持 ADD COLUMN，幂等（先查 PRAGMA）。既有 6
    核心行 is_core 默认 1（DEFAULT 1）→ 保持「已验证基准」；code/category/level 空、
    prerequisites NULL，由 knowledge_catalog.seed 幂等回填。
    """
    inspector = inspect(engine)
    if "knowledge_points" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("knowledge_points")}
    with engine.begin() as conn:
        if "code" not in columns:
            conn.execute(text("ALTER TABLE knowledge_points ADD COLUMN code VARCHAR(16) DEFAULT ''"))
        if "category" not in columns:
            conn.execute(text("ALTER TABLE knowledge_points ADD COLUMN category VARCHAR(16) DEFAULT ''"))
        if "level" not in columns:
            conn.execute(text("ALTER TABLE knowledge_points ADD COLUMN level VARCHAR(16) DEFAULT ''"))
        if "prerequisites" not in columns:
            conn.execute(text("ALTER TABLE knowledge_points ADD COLUMN prerequisites JSON"))
        if "is_core" not in columns:
            conn.execute(text("ALTER TABLE knowledge_points ADD COLUMN is_core BOOLEAN DEFAULT 1"))


def _migrate_genlog_artifact() -> None:
    """产物落库轻量迁移：既有开发库 generation_logs 表缺 artifact/artifact_updated_at 列时补齐。

    create_all 只建新表不加列；SQLite 支持 ADD COLUMN，幂等（先查 PRAGMA）。既有行
    artifact 默认 NULL（= 尚未物化产物，内置行读 ResourceCache、文档旧行首次查看时物化）。
    """
    inspector = inspect(engine)
    if "generation_logs" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("generation_logs")}
    with engine.begin() as conn:
        if "artifact" not in columns:
            conn.execute(text("ALTER TABLE generation_logs ADD COLUMN artifact JSON"))
        if "artifact_updated_at" not in columns:
            conn.execute(
                text("ALTER TABLE generation_logs ADD COLUMN artifact_updated_at DATETIME")
            )


def _migrate_learning_event_protocol() -> None:
    """TASK-005-C：为既有 learning_events 补齐幂等事件协议字段。"""
    inspector = inspect(engine)
    if "learning_events" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("learning_events")}
    with engine.begin() as conn:
        if "event_id" not in columns:
            conn.execute(text("ALTER TABLE learning_events ADD COLUMN event_id VARCHAR(64)"))
        if "source_type" not in columns:
            conn.execute(text("ALTER TABLE learning_events ADD COLUMN source_type VARCHAR(32)"))
        if "source_id" not in columns:
            conn.execute(text("ALTER TABLE learning_events ADD COLUMN source_id VARCHAR(128)"))
        if "algorithm_version" not in columns:
            conn.execute(text("ALTER TABLE learning_events ADD COLUMN algorithm_version VARCHAR(32)"))
        conn.execute(
            text(
                "UPDATE learning_events SET "
                "event_id = COALESCE(event_id, 'legacy_' || id), "
                "source_type = COALESCE(source_type, CASE event_type "
                "WHEN 'quiz' THEN 'quiz_result' "
                "WHEN 'diagnostic' THEN 'diagnostic' "
                "WHEN 'feynman' THEN 'feynman' "
                "WHEN 'learning_step' THEN 'learning_step' "
                "ELSE event_type END), "
                "source_id = COALESCE(source_id, 'learning_event:' || id), "
                "algorithm_version = COALESCE(algorithm_version, 'ks-logodds-v1')"
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_learning_event_event_id ON learning_events(event_id)"
            )
        )


def _migrate_async_tasks_user_type() -> None:
    """轻量迁移：async_tasks 表加 user_id / task_type / scope_key 列。

    create_all 只建新表不加列；SQLite 支持 ADD COLUMN，幂等（先查 PRAGMA）。
    """
    inspector = inspect(engine)
    if "async_tasks" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("async_tasks")}
    with engine.begin() as conn:
        if "user_id" not in columns:
            conn.execute(text("ALTER TABLE async_tasks ADD COLUMN user_id VARCHAR(64)"))
        if "task_type" not in columns:
            conn.execute(text("ALTER TABLE async_tasks ADD COLUMN task_type VARCHAR(32)"))
        if "scope_key" not in columns:
            conn.execute(text("ALTER TABLE async_tasks ADD COLUMN scope_key VARCHAR(256)"))


def init_db() -> None:
    """建表 + 幂等种子。"""
    # 版本化迁移先于 create_all：现有库可升级，新库由迁移显式建立受管表。
    upgrade()
    _migrate_b6()
    _migrate_b9()
    _migrate_path_plan()
    _migrate_mastery_score()
    _migrate_genlog_source()
    _migrate_genlog_artifact()
    _migrate_kp_catalog()
    _migrate_learning_event_protocol()
    _migrate_async_tasks_user_type()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        _seed_users(db)
        _seed_knowledge_points(db)
        _seed_catalog(db)
        _seed_lessons(db)
        _seed_quiz_questions(db)
        _seed_job_snapshots(db)
        _seed_external_resources(db)
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    print("[init_db] 数据库初始化与种子完成（幂等）。")
