# -*- coding: utf-8 -*-
"""B8 量化指标 ③：知识覆盖率（目标 ≥90%）。

口径（需求文档 7.2.3）：为 6 核心知识点定义「核心概念清单」（依据 init_db 知识点
描述与领域共识，每个概念附同义表述），检查该 KP 三档讲义合并文本对清单的命中率：

    coverageRate = 命中概念总数 / 概念总数（6 KP 合计 36 项）

命中判定：概念任一同义表述出现于合并文本（大小写不敏感）。
运行（backend 目录）：python scripts/metrics/knowledge_coverage.py
讲义来源与脚本 ① 同一批（generate_lecture 缓存复用）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from hallucination_rate import DIFFICULTIES, collect_lectures  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.entities import User  # noqa: E402

TARGET = 0.90

# 核心概念清单：kp_id -> [(概念名, [同义表述])]，每 KP 6 项
CONCEPT_CHECKLIST: dict[str, list[tuple[str, list[str]]]] = {
    "ml": [
        ("监督学习", ["监督学习", "有监督", "supervised"]),
        ("无监督学习", ["无监督", "unsupervised", "聚类"]),
        ("特征", ["特征", "feature"]),
        ("损失函数", ["损失", "loss"]),
        ("过拟合", ["过拟合", "overfit", "泛化"]),
        ("正则化", ["正则", "regulariz", "L1", "L2"]),
    ],
    "nn": [
        ("神经元", ["神经元", "neuron"]),
        ("加权求和", ["加权求和", "加权和", "w·x", "权重"]),
        ("偏置", ["偏置", "bias"]),
        ("激活函数", ["激活函数", "激活", "ReLU", "Sigmoid", "Tanh"]),
        ("前向传播", ["前向传播", "前向", "forward"]),
        ("反向传播", ["反向传播", "反向", "backprop"]),
    ],
    "dl": [
        ("反向传播", ["反向传播", "链式法则", "backprop"]),
        ("梯度下降", ["梯度下降", "gradient descent", "梯度"]),
        ("优化器", ["优化器", "SGD", "Adam", "RMSprop", "优化"]),
        ("学习率", ["学习率", "learning rate", "η"]),
        ("正则化", ["正则", "Dropout", "dropout"]),
        ("归一化", ["归一化", "BatchNorm", "Norm", "标准化"]),
    ],
    "cnn": [
        ("卷积", ["卷积", "convolution"]),
        ("卷积核", ["卷积核", "滤波器", "kernel", "filter"]),
        ("池化", ["池化", "pooling"]),
        ("感受野", ["感受野", "receptive field"]),
        ("权重共享", ["权重共享", "参数共享", "共享权重", "共享参数"]),
        ("经典网络", ["LeNet", "AlexNet", "ResNet", "VGG", "经典卷积网络", "经典网络"]),
    ],
    "transformer": [
        ("自注意力", ["自注意力", "self-attention", "注意力"]),
        ("Query/Key/Value", ["Query", "Key", "Value", "QKV", "Q/K/V"]),
        ("多头注意力", ["多头", "multi-head"]),
        ("位置编码", ["位置编码", "positional"]),
        ("编码器/解码器", ["编码器", "解码器", "encoder", "decoder"]),
        ("缩放点积", ["缩放点积", "scaled dot", "点积", "softmax"]),
    ],
    "finetune": [
        ("预训练", ["预训练", "pre-train", "pretrain"]),
        ("全参微调", ["全参", "全量微调", "full fine"]),
        ("LoRA", ["LoRA", "低秩"]),
        ("指令微调", ["指令微调", "SFT", "instruction"]),
        ("对齐", ["对齐", "RLHF", "DPO"]),
        ("参数高效", ["参数高效", "PEFT", "Adapter", "高效微调"]),
    ],
}


def run(lectures: dict[tuple[str, str], str] | None = None) -> dict[str, Any]:
    """合并三档讲义文本，逐概念命中判定。"""
    if lectures is None:
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == "learner_001").one()
            lectures = collect_lectures(db, user.id)
        finally:
            db.close()

    details: list[dict[str, Any]] = []
    hit_total = concept_total = 0
    for kp_id, checklist in CONCEPT_CHECKLIST.items():
        combined = "\n".join(
            lectures.get((kp_id, d), "") for d in DIFFICULTIES
        ).lower()
        hits, misses = [], []
        for concept, aliases in checklist:
            if any(a.lower() in combined for a in aliases):
                hits.append(concept)
            else:
                misses.append(concept)
        hit_total += len(hits)
        concept_total += len(checklist)
        details.append(
            {"kpId": kp_id, "hits": hits, "misses": misses,
             "rate": round(len(hits) / len(checklist), 4)}
        )

    rate = round(hit_total / concept_total, 4) if concept_total else 0.0
    return {
        "metric": "coverageRate",
        "rate": rate,
        "target": TARGET,
        "meetsTarget": rate >= TARGET,
        "provider": settings.llm_provider,
        "hitConcepts": hit_total,
        "totalConcepts": concept_total,
        "details": details,
    }


def main() -> None:
    result = run()
    print(f"\n== 知识覆盖率（核心概念命中，provider={result['provider']}）==")
    for d in result["details"]:
        flag = f"  ← 未命中 {d['misses']}" if d["misses"] else ""
        print(f"  {d['kpId']:<12} {len(d['hits'])}/{len(d['hits']) + len(d['misses'])}"
              f"  覆盖率 {d['rate']:.2f}{flag}")
    verdict = "达标 ✓" if result["meetsTarget"] else "未达标 ✗"
    print(f"\n  覆盖率 = {result['hitConcepts']}/{result['totalConcepts']} "
          f"= {result['rate']:.4f}（目标 ≥ {TARGET}）→ {verdict}")


if __name__ == "__main__":
    main()
