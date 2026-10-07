"""Held-out validation protocol for TASK-004-E4-A Trusted RAG admission."""
from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


QUERY_TYPES = (
    "concept_explanation",
    "method_comparison",
    "operation_steps",
    "programming_practice",
    "comprehensive_question",
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrustedRAGValidationCase(_StrictModel):
    case_id: str = Field(min_length=1, max_length=96)
    query_type: Literal[
        "concept_explanation",
        "method_comparison",
        "operation_steps",
        "programming_practice",
        "comprehensive_question",
    ]
    query: str = Field(min_length=8, max_length=500)
    expected_document_ids: list[str] = Field(min_length=1)
    required_concepts: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_expected_values(self) -> "TrustedRAGValidationCase":
        if len(self.expected_document_ids) != len(set(self.expected_document_ids)):
            raise ValueError("expected_document_ids must be unique")
        if len(self.required_concepts) != len(set(self.required_concepts)):
            raise ValueError("required_concepts must be unique")
        return self


class TrustedRAGValidationDataset(_StrictModel):
    """Independent, content-bearing validation labels; never used by production."""

    schema_version: Literal["trusted-rag-validation-v1"] = "trusted-rag-validation-v1"
    dataset_id: Literal["trusted-rag-e4a-held-out-v1"] = "trusted-rag-e4a-held-out-v1"
    created_at: str = Field(min_length=1)
    source: Literal["held_out_manual_topic_matrix"] = "held_out_manual_topic_matrix"
    tuning_dataset_reused: Literal[False] = False
    cases: list[TrustedRAGValidationCase] = Field(min_length=100)

    @model_validator(mode="after")
    def validate_strata(self) -> "TrustedRAGValidationDataset":
        ids = [case.case_id for case in self.cases]
        queries = [normalize_query(case.query) for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id must be unique")
        if len(queries) != len(set(queries)):
            raise ValueError("normalized query must be unique")
        counts = Counter(case.query_type for case in self.cases)
        missing = [name for name in QUERY_TYPES if counts[name] < 20]
        if missing:
            raise ValueError(f"each query type needs at least 20 cases: {missing}")
        return self

    def query_fingerprints(self) -> set[str]:
        return {query_fingerprint(case.query) for case in self.cases}


def normalize_query(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def query_fingerprint(value: str) -> str:
    return sha256(normalize_query(value).encode("utf-8")).hexdigest()


# Twenty-five topics produce one held-out question in each required stratum.
# The wording is intentionally independent of TASK-004-E3's tuning cases.
_TOPICS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("doc_005", "监督学习", ("标签", "分类", "回归")),
    ("doc_006", "无监督学习", ("无标签", "聚类", "降维")),
    ("doc_007", "损失函数", ("预测误差", "代价函数", "可微")),
    ("doc_008", "过拟合与正则化", ("训练集", "泛化", "偏差方差")),
    ("doc_009", "特征工程", ("构造", "选择", "变换")),
    ("doc_010", "人工神经元", ("加权求和", "偏置", "激活")),
    ("doc_011", "多层感知机", ("隐藏层", "全连接", "非线性")),
    ("doc_012", "激活函数", ("ReLU", "Sigmoid", "梯度")),
    ("doc_013", "反向传播", ("链式法则", "梯度", "参数更新")),
    ("doc_014", "梯度下降", ("学习率", "损失", "收敛")),
    ("doc_015", "归一化", ("分布稳定", "BatchNorm", "LayerNorm")),
    ("doc_016", "Dropout", ("随机失活", "正则化", "推理")),
    ("doc_017", "学习率调度", ("预热", "衰减", "收敛")),
    ("doc_018", "卷积层", ("卷积核", "步长", "填充")),
    ("doc_019", "池化层", ("下采样", "最大池化", "平均池化")),
    ("doc_020", "感受野", ("局部特征", "层叠", "上下文")),
    ("doc_021", "CNN架构演进", ("LeNet", "AlexNet", "残差")),
    ("doc_022", "自注意力", ("Query", "Key", "Value")),
    ("doc_023", "多头注意力", ("投影", "并行", "子空间")),
    ("doc_024", "位置编码", ("词序", "正弦余弦", "位置")),
    ("doc_025", "Transformer编码器与解码器", ("编码器", "解码器", "自回归")),
    ("doc_026", "预训练与迁移学习", ("自监督", "基础模型", "下游任务")),
    ("doc_027", "全参微调与参数高效微调", ("参数更新", "显存", "适配器")),
    ("doc_028", "LoRA低秩适配", ("冻结权重", "低秩矩阵", "参数量")),
    ("doc_029", "指令微调", ("指令响应", "监督微调", "遵循指令")),
)


def build_validation_dataset() -> TrustedRAGValidationDataset:
    cases: list[TrustedRAGValidationCase] = []
    for index, (document_id, topic, concepts) in enumerate(_TOPICS):
        next_document, next_topic, next_concepts = _TOPICS[(index + 1) % len(_TOPICS)]
        base = f"e4a_{index + 1:02d}"
        cases.extend(
            (
                TrustedRAGValidationCase(
                    case_id=f"{base}_concept",
                    query_type="concept_explanation",
                    query=f"面向初学者说明{topic}的核心含义、关键机制与典型用途。",
                    expected_document_ids=[document_id],
                    required_concepts=list(concepts),
                ),
                TrustedRAGValidationCase(
                    case_id=f"{base}_compare",
                    query_type="method_comparison",
                    query=f"比较{topic}和{next_topic}的目标、工作方式及适用条件，如何选择？",
                    expected_document_ids=[document_id, next_document],
                    required_concepts=[concepts[0], next_concepts[0]],
                ),
                TrustedRAGValidationCase(
                    case_id=f"{base}_steps",
                    query_type="operation_steps",
                    query=f"在机器学习项目中落地{topic}时，请给出从准备、配置到检查结果的操作步骤。",
                    expected_document_ids=[document_id],
                    required_concepts=list(concepts),
                ),
                TrustedRAGValidationCase(
                    case_id=f"{base}_code",
                    query_type="programming_practice",
                    query=f"用Python或PyTorch实现{topic}的最小实践，需要哪些组件、伪代码和避坑检查？",
                    expected_document_ids=[document_id],
                    required_concepts=list(concepts),
                ),
                TrustedRAGValidationCase(
                    case_id=f"{base}_synthesis",
                    query_type="comprehensive_question",
                    query=f"综合分析{topic}与{next_topic}在完整训练流程中的衔接关系、风险和验证方法。",
                    expected_document_ids=[document_id, next_document],
                    required_concepts=[concepts[0], concepts[1], next_concepts[0]],
                ),
            )
        )
    return TrustedRAGValidationDataset(
        created_at=datetime.now(UTC).isoformat(),
        cases=cases,
    )
