"""费曼讲解评估概念库、确定性 Mock 判定与契约清洗。"""
from __future__ import annotations

from typing import Any

from app.core.llm_deepseek import LLMGenerationError

# 费曼讲解评估的「应覆盖核心概念」（接口文档 18.2）：mock 据此判定学生讲解
# 「讲漏」哪些关键点 → 生成 gaps。每概念：keys 命中关键词、title 缺口标题、
# detail 缺口说明、severity 严重度、ask 引导补讲的追问。deepseek 走真实评估。
FEYNMAN_CONCEPTS: dict[str, list[dict[str, Any]]] = {
    "nn": [
        {"keys": ["激活", "非线性", "relu", "sigmoid", "tanh"], "title": "激活函数与非线性",
         "detail": "未清楚说明激活函数引入非线性——否则多层网络等价于单层线性变换。",
         "severity": "high", "ask": "如果去掉激活函数，三层网络和一层线性模型有什么区别？"},
        {"keys": ["加权", "求和", "权重", "相乘"], "title": "加权求和",
         "detail": "未说明输入与权重加权求和这一基本运算。", "severity": "medium",
         "ask": "一个神经元是如何把多个输入汇总成一个数的？"},
        {"keys": ["反向", "梯度", "误差", "更新", "backprop"], "title": "反向传播与梯度更新",
         "detail": "未讲清反向传播如何利用梯度更新权重。", "severity": "medium",
         "ask": "网络是怎样根据误差来调整权重的？"},
        {"keys": ["偏置", "bias"], "title": "偏置项",
         "detail": "未提到偏置 b 对决策边界平移的作用。", "severity": "low",
         "ask": "偏置 b 在加权求和之后起什么作用？"},
    ],
    "ml": [
        {"keys": ["过拟合", "泛化", "overfit"], "title": "过拟合与泛化",
         "detail": "未提到过拟合——模型训练集好、测试集差的泛化问题。", "severity": "high",
         "ask": "如果模型训练集表现很好但测试集很差，说明了什么？"},
        {"keys": ["监督", "标注", "分类", "回归"], "title": "监督学习",
         "detail": "未区分监督/无监督学习与典型任务（分类、回归）。", "severity": "medium",
         "ask": "分类、回归和聚类分别属于哪类学习？"},
        {"keys": ["正则", "l1", "l2", "惩罚", "早停"], "title": "正则化",
         "detail": "未说明正则化如何抑制过拟合。", "severity": "medium",
         "ask": "有哪些手段可以缓解过拟合？"},
        {"keys": ["特征", "损失"], "title": "特征与损失",
         "detail": "未提到特征与损失函数在训练中的作用。", "severity": "low",
         "ask": "模型用什么来衡量预测好坏并据此优化？"},
    ],
    "dl": [
        {"keys": ["反向", "链式", "backprop"], "title": "反向传播",
         "detail": "未讲清反向传播用链式法则求梯度。", "severity": "high",
         "ask": "梯度是怎样从输出层逐层传回每个参数的？"},
        {"keys": ["梯度下降", "优化器", "sgd", "adam", "学习率"], "title": "梯度下降与优化器",
         "detail": "未提到用梯度下降/优化器按学习率更新参数。", "severity": "medium",
         "ask": "拿到梯度之后，参数是按什么规则更新的？"},
        {"keys": ["损失", "目标函数"], "title": "损失函数",
         "detail": "未说明以损失为优化目标。", "severity": "medium",
         "ask": "训练优化的目标是什么？"},
        {"keys": ["梯度消失", "梯度爆炸", "归一化", "batchnorm", "dropout"], "title": "训练稳定性",
         "detail": "未提到梯度消失/爆炸与归一化、正则等稳定手段。", "severity": "low",
         "ask": "深层网络训练常见哪些梯度问题，如何缓解？"},
    ],
    "cnn": [
        {"keys": ["卷积", "卷积核", "局部", "特征"], "title": "卷积与局部特征",
         "detail": "未说明卷积核滑动提取局部特征。", "severity": "high",
         "ask": "卷积核是如何在图像上提取局部特征的？"},
        {"keys": ["池化", "下采样", "pool"], "title": "池化",
         "detail": "未提到池化下采样降低尺寸、增强平移不变性。", "severity": "medium",
         "ask": "池化层的作用是什么？"},
        {"keys": ["权重共享", "参数共享", "共享"], "title": "权重共享",
         "detail": "未说明卷积通过权重共享大幅减少参数。", "severity": "medium",
         "ask": "相比全连接，卷积为什么参数更少？"},
        {"keys": ["感受野"], "title": "感受野",
         "detail": "未提到感受野随网络加深而扩大。", "severity": "low",
         "ask": "为什么深层卷积能看到更大范围的信息？"},
    ],
    "transformer": [
        {"keys": ["自注意力", "注意力", "attention", "query", "q/k/v", "qkv"], "title": "自注意力机制",
         "detail": "未讲清自注意力用 Q/K/V 建模序列依赖。", "severity": "high",
         "ask": "自注意力是怎样让每个位置关注到其他位置的？"},
        {"keys": ["多头", "multi-head", "multihead"], "title": "多头注意力",
         "detail": "未提到多头在不同子空间并行建模。", "severity": "medium",
         "ask": "为什么要用多个注意力头而不是一个？"},
        {"keys": ["位置编码", "position", "顺序"], "title": "位置编码",
         "detail": "未提到位置编码为模型注入顺序信息。", "severity": "medium",
         "ask": "自注意力本身不区分顺序，靠什么补充位置信息？"},
        {"keys": ["前馈", "残差", "layernorm", "归一化"], "title": "前馈与残差归一化",
         "detail": "未提到前馈网络与残差 + LayerNorm。", "severity": "low",
         "ask": "编码器每层除了注意力还有哪些子层？"},
    ],
    "finetune": [
        {"keys": ["lora", "低秩"], "title": "LoRA 低秩适配",
         "detail": "未讲清 LoRA 冻结原权重、用低秩矩阵学增量。", "severity": "high",
         "ask": "LoRA 是如何在不改动原权重的情况下微调的？"},
        {"keys": ["全参", "全量", "参数高效", "peft", "adapter"], "title": "全参 vs 参数高效微调",
         "detail": "未对比全参微调与 PEFT 的开销差异。", "severity": "medium",
         "ask": "全参微调和参数高效微调的主要区别是什么？"},
        {"keys": ["指令", "sft", "监督微调"], "title": "指令微调",
         "detail": "未提到指令微调提升模型遵循指令的能力。", "severity": "medium",
         "ask": "如何让模型更好地理解并遵循指令？"},
        {"keys": ["对齐", "rlhf", "dpo", "偏好"], "title": "对齐",
         "detail": "未提到 RLHF/DPO 等对齐方法。", "severity": "low",
         "ask": "训练后如何让模型输出更符合人类偏好？"},
    ],
}

# 缺口严重度排序（接口文档 18.2）：high 最先，决定 feedback 焦点与 complete 判定
SEVERITY_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}
# 含此类缺口即「讲解未充分」（done=False）；仅 low 或无缺口 → complete=True
FEYNMAN_BLOCKING: tuple[str, ...] = ("high", "medium")


def evaluate_mock(kp_id: str, kp_name: str, explanation: str) -> dict[str, Any]:
    """按应覆盖概念确定性评估讲解，定位遗漏点并给出追问。"""
    text = explanation or ""
    low = text.lower()
    concepts = FEYNMAN_CONCEPTS.get(kp_id)
    if not concepts:
        if len(text.strip()) >= 40:
            return {
                "feedback": f"你对「{kp_name}」做了讲解，已覆盖主要思路，可继续补充细节使其更完整。",
                "gaps": [],
                "score": 70,
                "followups": [],
                "complete": True,
            }
        return {
            "feedback": f"你的讲解略显简略，试着把「{kp_name}」的核心机制讲得更具体一些。",
            "gaps": [
                {
                    "kpId": kp_id,
                    "title": f"{kp_name}讲解过于简略",
                    "detail": "讲解信息量不足，难以判断理解程度，建议展开核心机制。",
                    "severity": "medium",
                }
            ],
            "followups": [f"用你自己的话说说，{kp_name}最关键的一步是什么？"],
            "complete": False,
        }

    covered: list[str] = []
    gaps: list[dict[str, Any]] = []
    for concept in concepts:
        if any(key.lower() in low for key in concept["keys"]):
            covered.append(concept["title"])
        else:
            gaps.append(
                {
                    "kpId": kp_id,
                    "title": concept["title"],
                    "detail": concept["detail"],
                    "severity": concept["severity"],
                    "_ask": concept["ask"],
                }
            )
    total = len(concepts)
    score = round(len(covered) / total * 100) if total else 0
    gaps.sort(key=lambda gap: SEVERITY_RANK.get(gap["severity"], 9))
    ack = f"你讲清了「{'、'.join(covered)}」。" if covered else "你的讲解还比较笼统。"
    if gaps:
        top = gaps[0]
        body = f"但还有需要补充的地方：最关键的是{top['title']}——{top['detail']}"
    else:
        body = "关键点都覆盖到了，讲解相当完整！"
    followups = [gap["_ask"] for gap in gaps[:2]]
    complete = not any(gap["severity"] in FEYNMAN_BLOCKING for gap in gaps)
    clean_gaps = [
        {
            "kpId": gap["kpId"],
            "title": gap["title"],
            "detail": gap["detail"],
            "severity": gap["severity"],
        }
        for gap in gaps
    ]
    return {
        "feedback": ack + body,
        "gaps": clean_gaps,
        "score": score,
        "followups": followups,
        "complete": complete,
    }


def clean_feynman(data: dict[str, Any], kp_id: str) -> dict[str, Any]:
    """清洗真实评估：缺口字段、严重度、分数、追问和完成状态回正。"""
    gaps: list[dict[str, Any]] = []
    for gap in data.get("gaps") or []:
        if not isinstance(gap, dict):
            continue
        title = str(gap.get("title") or "").strip()
        detail = str(gap.get("detail") or "").strip()
        if not title or not detail:
            continue
        severity = (
            gap.get("severity") if gap.get("severity") in SEVERITY_RANK else "medium"
        )
        gaps.append(
            {"kpId": kp_id, "title": title, "detail": detail, "severity": severity}
        )
    gaps.sort(key=lambda item: SEVERITY_RANK[item["severity"]])
    feedback = str(data.get("feedback") or "").strip()
    if not feedback:
        raise LLMGenerationError("费曼评估缺 feedback")
    try:
        score = int(data.get("score"))
    except (TypeError, ValueError):
        blocking = len([gap for gap in gaps if gap["severity"] in FEYNMAN_BLOCKING])
        score = max(0, 100 - 25 * blocking)
    score = max(0, min(100, score))
    followups = [
        str(followup).strip()
        for followup in (data.get("followups") or [])
        if str(followup).strip()
    ][:3]
    complete = data.get("complete")
    if not isinstance(complete, bool):
        complete = not any(gap["severity"] in FEYNMAN_BLOCKING for gap in gaps)
    return {
        "feedback": feedback,
        "gaps": gaps,
        "score": score,
        "followups": followups,
        "complete": complete,
    }
