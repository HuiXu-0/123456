"""LangGraph 节点包（R2）。

按子图分文件：``resource_generation`` 是既有生产子图（诊断→检索→生成→校验→决策）。
后续子图（path_planning / interaction / evaluation，见 R3 Supervisor 分发）各自成模块，
不往这里堆。
"""
from __future__ import annotations

from app.workflows.nodes.resource_generation import (
    MAX_RETRIES,
    VALIDATION_THRESHOLD,
    Retriever,
    build_graph,
)

__all__ = ["MAX_RETRIES", "VALIDATION_THRESHOLD", "Retriever", "build_graph"]
