"""路由子图包（R3）。

``router`` 是统一入口：``START → supervisor → conditional edge → 子图 → END``。
旧入口 ``app.workflows.learning_workflow.run_learning_workflow`` **保持不变**，
两者并存（见 ``router`` 模块文档的兼容说明）。
"""
from __future__ import annotations

from app.workflows.graphs.router import (
    GraphNotReadyError,
    build_router_graph,
    run_router_workflow,
)

__all__ = ["GraphNotReadyError", "build_router_graph", "run_router_workflow"]
