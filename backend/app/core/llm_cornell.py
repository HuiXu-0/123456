"""康奈尔笔记确定性模板、Mock 组装与契约清洗。"""
from __future__ import annotations

from typing import Any

from app.core.llm_deepseek import LLMGenerationError

# 康奈尔笔记法线索模板（接口文档 18.1）：每核心知识点的「线索区」问题/关键词
# （5-8 条）+「主笔记区」要点预填骨架 + 总结区引导语。mock 与 deepseek 兜底共用，
# 内容紧扣各知识点（保证返回真实线索问题而非占位）；未收录知识点参数化生成。
# 结构：kp_id -> {cues:[(type, text)], outline:[(heading, [points])], summaryHint}
CORNELL_TEMPLATES: dict[str, dict[str, Any]] = {
    "nn": {
        "cues": [
            ("question", "为什么神经网络需要激活函数？"),
            ("question", "一个神经元的前向计算分哪几步？"),
            ("question", "反向传播是如何更新权重的？"),
            ("question", "ReLU 相比 Sigmoid 有什么优势？"),
            ("keyword", "加权求和 → 偏置 → 激活"),
            ("keyword", "梯度消失"),
        ],
        "outline": [
            ("激活函数的作用", ["引入非线性表达能力", "若无激活，多层等价单层线性变换"]),
            ("神经元三步运算", ["加权求和 Σ w·x", "加偏置 + b", "激活函数得到输出"]),
            ("反向传播", ["链式法则逐层求梯度", "按梯度下降更新权重"]),
        ],
        "summaryHint": "用一句话概括：神经网络如何通过加权、激活与反向传播来学习？",
    },
    "ml": {
        "cues": [
            ("question", "监督学习和无监督学习有什么区别？"),
            ("question", "过拟合是怎么产生的，有什么表现？"),
            ("question", "正则化为什么能缓解过拟合？"),
            ("question", "训练集 / 验证集 / 测试集各有什么用？"),
            ("keyword", "特征工程 / 损失函数"),
            ("keyword", "偏差-方差权衡"),
        ],
        "outline": [
            ("监督 vs 无监督", ["分类/回归有标注目标", "聚类/降维无标注"]),
            ("过拟合与泛化", ["训练好、测试差", "模型记住了训练噪声"]),
            ("正则化", ["L1/L2 惩罚过大参数", "早停 / 交叉验证"]),
        ],
        "summaryHint": "用一句话概括：机器学习如何在拟合训练数据与保持泛化之间取得平衡？",
    },
    "dl": {
        "cues": [
            ("question", "反向传播的核心作用是什么？"),
            ("question", "梯度下降如何更新参数？"),
            ("question", "常见优化器（SGD/Adam）有什么区别？"),
            ("question", "什么是梯度消失/爆炸，如何缓解？"),
            ("keyword", "链式法则 / 计算图"),
            ("keyword", "BatchNorm / Dropout"),
        ],
        "outline": [
            ("反向传播", ["链式法则求梯度", "计算图自动求导"]),
            ("梯度下降与优化器", ["SGD / Adam / RMSprop", "学习率调度"]),
            ("训练稳定性", ["梯度消失 / 爆炸", "归一化与正则缓解"]),
        ],
        "summaryHint": "用一句话概括：深度网络如何通过反向传播与优化器迭代学习？",
    },
    "cnn": {
        "cues": [
            ("question", "卷积层为什么能提取局部特征？"),
            ("question", "权重共享带来了什么好处？"),
            ("question", "池化层的作用是什么？"),
            ("question", "感受野如何随网络加深变化？"),
            ("keyword", "卷积核 / 步长 / 填充"),
            ("keyword", "ResNet 残差连接"),
        ],
        "outline": [
            ("卷积与局部特征", ["卷积核滑动提取局部特征", "权重共享大幅减少参数"]),
            ("池化", ["下采样降低空间尺寸", "增强平移不变性"]),
            ("经典网络", ["LeNet / AlexNet / ResNet", "残差连接让网络更深"]),
        ],
        "summaryHint": "用一句话概括：CNN 如何通过卷积与池化逐层提取图像特征？",
    },
    "transformer": {
        "cues": [
            ("question", "自注意力机制解决了什么问题？"),
            ("question", "Q / K / V 分别代表什么？"),
            ("question", "为什么要用多头注意力？"),
            ("question", "位置编码为什么必要？"),
            ("keyword", "缩放点积注意力"),
            ("keyword", "残差 + LayerNorm"),
        ],
        "outline": [
            ("自注意力", ["Q/K/V 计算注意力权重", "建模长距离依赖"]),
            ("多头注意力", ["多个子空间并行", "捕捉不同类型关系"]),
            ("位置编码", ["注入序列顺序信息", "正弦 / 可学习编码"]),
        ],
        "summaryHint": "用一句话概括：Transformer 如何用自注意力建模序列中元素间的依赖？",
    },
    "finetune": {
        "cues": [
            ("question", "全参微调和参数高效微调有什么区别？"),
            ("question", "LoRA 的核心思想是什么？"),
            ("question", "指令微调（SFT）解决了什么问题？"),
            ("question", "RLHF / DPO 对齐的目标是什么？"),
            ("keyword", "低秩矩阵 / 冻结权重"),
            ("keyword", "Adapter / Prompt Tuning"),
        ],
        "outline": [
            ("微调范式", ["全参微调开销大", "PEFT 只更新少量参数"]),
            ("LoRA", ["冻结原权重", "低秩矩阵学习增量"]),
            ("对齐", ["SFT 指令微调", "RLHF / DPO 对齐人类偏好"]),
        ],
        "summaryHint": "用一句话概括：如何在低成本下让大模型适配下游任务并对齐人类偏好？",
    },
}


def assemble_cornell(
    cue_specs: list[tuple[str, str]],
    outline_specs: list[tuple[str, list[str]]],
    summary_hint: str,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """把线索、提纲和总结引导装配为带稳定编号的契约结构。"""
    cues = [
        {"id": f"c{i + 1}", "type": cue_type, "text": text}
        for i, (cue_type, text) in enumerate(cue_specs)
    ]
    note_outline: list[dict[str, Any]] = []
    for i, (heading, points) in enumerate(outline_specs):
        cue_id = cues[i]["id"] if i < len(cues) and cues[i]["type"] == "question" else None
        note_outline.append(
            {
                "id": f"n{i + 1}",
                "cueId": cue_id,
                "heading": heading,
                "points": list(points),
            }
        )
    return {
        "cues": cues,
        "noteOutline": note_outline,
        "summaryHint": summary_hint,
        "sources": [dict(source) for source in sources],
    }


def generate_mock_cornell(
    kp_id: str,
    kp_name: str,
    difficulty: str,
    description: str,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """生成确定性康奈尔线索，未收录知识点使用参数化兜底。"""
    del difficulty  # 保留与 LLMClient 方法一致的调用签名。
    template = CORNELL_TEMPLATES.get(kp_id)
    if template:
        return assemble_cornell(
            template["cues"], template["outline"], template["summaryHint"], sources
        )
    desc = (description or "").strip()
    cue_specs = [
        ("question", f"{kp_name}要解决的核心问题是什么？"),
        ("question", f"{kp_name}的关键步骤 / 组成有哪些？"),
        ("question", f"{kp_name}在实践中如何应用？"),
        ("question", f"学习{kp_name}时最容易混淆的点是什么？"),
        ("keyword", f"{kp_name}核心概念"),
    ]
    outline_specs = [
        (
            f"{kp_name}核心概念",
            [desc[:24] if desc else f"{kp_name}的定义与作用", "关键组成与原理"],
        ),
        ("实践应用", [f"{kp_name}的典型场景", "动手示例巩固理解"]),
    ]
    summary_hint = f"用一句话概括：{kp_name}是什么、解决了什么问题？"
    return assemble_cornell(cue_specs, outline_specs, summary_hint, sources)


def clean_cornell(
    data: dict[str, Any], sources: list[dict[str, Any]]
) -> dict[str, Any]:
    """清洗真实模型输出：线索 5–8 条、类型回正、提纲要点最多 4 条。"""
    raw_cues = data.get("cues")
    if not isinstance(raw_cues, list):
        raise LLMGenerationError("康奈尔线索缺 cues 数组")
    cues: list[dict[str, Any]] = []
    for cue in raw_cues:
        if not isinstance(cue, dict):
            continue
        text = str(cue.get("text") or "").strip()
        if not text:
            continue
        cue_type = cue.get("type") if cue.get("type") in ("question", "keyword") else "question"
        cues.append({"id": f"c{len(cues) + 1}", "type": cue_type, "text": text})
        if len(cues) >= 8:
            break
    if len(cues) < 5:
        raise LLMGenerationError(f"康奈尔线索有效条目不足 5 条（得到 {len(cues)}）")

    note_outline: list[dict[str, Any]] = []
    for outline in data.get("noteOutline") or []:
        if not isinstance(outline, dict):
            continue
        heading = str(outline.get("heading") or "").strip()
        if not heading:
            continue
        points = [
            str(point).strip()
            for point in (outline.get("points") or [])
            if str(point).strip()
        ][:4]
        i = len(note_outline)
        cue_id = cues[i]["id"] if i < len(cues) and cues[i]["type"] == "question" else None
        note_outline.append(
            {
                "id": f"n{len(note_outline) + 1}",
                "cueId": cue_id,
                "heading": heading,
                "points": points,
            }
        )
    return {
        "cues": cues,
        "noteOutline": note_outline,
        "summaryHint": str(data.get("summaryHint") or "").strip(),
        "sources": [dict(source) for source in sources],
    }
