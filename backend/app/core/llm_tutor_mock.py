"""智能辅导的确定性 Mock 决策逻辑。

仅负责关键词匹配和契约形状构造，不调用模型、不持有会话，也不执行资源生成。
"""
from __future__ import annotations

import re
from typing import Any

REMEDIAL_TYPES: tuple[str, ...] = ("diagram", "example", "video", "lecture")

_TUTOR_BRANCHES: tuple[tuple[re.Pattern[str], str, tuple[str, ...]], ...] = (
    (
        re.compile(r"激活函数|激活"),
        "好问题。先想一想：如果没有激活函数，多层网络叠加后等价于什么？",
        ("等价于线性变换", "可以拟合任意函数", "不确定"),
    ),
    (
        re.compile(r"线性|等价"),
        "对——叠加后仍是一个线性变换，这正是需要非线性的原因。那么哪个激活函数计算最快、还能缓解梯度消失？",
        ("ReLU", "Sigmoid", "Tanh"),
    ),
    (
        re.compile(r"relu", re.I),
        "正是 ReLU。它在正区间导数恒为 1——想一想：这对反向传播中的梯度意味着什么？",
        ("梯度不会被反复压缩", "梯度会消失", "不确定"),
    ),
    (
        re.compile(r"梯度|反向|backprop|损失|下降", re.I),
        "很好，你已经把前向与反向串起来了。试着用一句话说说：网络是如何利用梯度来更新权重的？",
        ("沿梯度下降方向更新", "随机调整权重", "不确定"),
    ),
    (
        re.compile(r"偏置|bias|\bb\b", re.I),
        "没错，是偏置 b。那么加权求和加偏置得到 z 之后，为什么不能直接把 z 当输出？",
        ("因为要引入非线性", "因为 z 太大", "不确定"),
    ),
    (
        re.compile(r"加权|求和|相乘|权重|乘"),
        "不错的起点。加权求和之后，为了让决策边界可以平移，还要加上一个量——它叫什么？",
        ("偏置 b", "学习率", "损失函数"),
    ),
)

_TUTOR_FALLBACK: tuple[str, tuple[str, ...]] = (
    "别急，换个角度想想：神经元的本质是把多个输入「汇总」成一个值，这个汇总最直接的数学操作是什么？",
    ("加权求和", "取最大值", "不确定"),
)

_REMEDIAL_KEYWORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"激活|relu|sigmoid|tanh|非线性", re.I), "激活函数与非线性"),
    (re.compile(r"反向|梯度|backprop|链式|求导"), "反向传播与梯度"),
    (re.compile(r"加权|求和|权重|偏置|bias", re.I), "神经元加权求和与偏置"),
    (re.compile(r"卷积|池化|感受野|卷积核"), "卷积与池化"),
    (re.compile(r"注意力|attention|qkv|q/k/v|多头", re.I), "自注意力机制"),
    (re.compile(r"位置编码|position", re.I), "位置编码"),
    (re.compile(r"过拟合|正则|泛化"), "过拟合与正则化"),
    (re.compile(r"优化器|sgd|adam|学习率", re.I), "优化器与学习率"),
    (re.compile(r"lora|微调|peft|对齐|rlhf|dpo", re.I), "大模型微调与对齐"),
)

_REMEDIAL_TYPE_META: dict[str, tuple[str, str]] = {
    "diagram": ("知识图解：{point}", "用流程图直观呈现「{point}」的关键步骤与依赖关系"),
    "example": ("例题精讲：{point}", "一道围绕「{point}」的例题 + 分步解析"),
    "video": ("短视频讲解：{point}", "3-5 个分镜的动画讲解，配旁白逐步拆解「{point}」"),
    "lecture": ("补充讲义片段：{point}", "针对「{point}」的精炼讲义片段，含要点与小结"),
}


def tutor_reply(message: str) -> tuple[str, list[str]]:
    """按关键词返回确定性苏格拉底追问和快捷建议。"""
    for pattern, reply, suggestions in _TUTOR_BRANCHES:
        if pattern.search(message or ""):
            return reply, list(suggestions)
    return _TUTOR_FALLBACK[0], list(_TUTOR_FALLBACK[1])


def identify_problem(question: str, kp_name: str) -> str:
    """识别学生问题点；未命中时回落当前知识点核心概念。"""
    for pattern, point in _REMEDIAL_KEYWORDS:
        if pattern.search(question or ""):
            return point
    return f"{kp_name}的核心概念"


def build_remedial_suggestions(point: str) -> list[dict[str, Any]]:
    """按稳定顺序构造四类补救资源建议。"""
    return [
        {
            "id": f"r-{resource_type}",
            "type": resource_type,
            "title": _REMEDIAL_TYPE_META[resource_type][0].format(point=point),
            "expect": _REMEDIAL_TYPE_META[resource_type][1].format(point=point),
        }
        for resource_type in REMEDIAL_TYPES
    ]
