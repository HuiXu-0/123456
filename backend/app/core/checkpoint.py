"""LangGraph Checkpoint 支持（R2，**可选能力，默认关闭**）。

## 默认关闭，语义零变化

``settings.workflow_checkpoint_enabled`` 默认 ``False`` → ``get_checkpointer()``
返回 ``None`` → ``graph.compile(checkpointer=None)`` 与迁移前的 ``graph.compile()``
完全等价，**运行语义逐字不变**。R2 的验收不依赖本模块被开启。

## 开启后得到什么

State 快照落 SQLite → 可跨进程恢复、可重放、可人工介入（R3+ 的
``interrupt`` 与 human-in-the-loop 的前置能力）。存放路径见
``settings.workflow_checkpoint_path``。

## 为什么 State 必须先"可序列化"

checkpoint 要把整个 ``MultiAgentState`` 序列化落盘。一旦 State 里混进
DB Session / Retriever 实例 / API Key，**开启即崩溃**。这就是
``app.workflows.state`` 模块文档里那条硬约束的由来。

## 失败即降级

``langgraph-checkpoint-sqlite`` 未安装、版本不符、路径不可写 —— 一律
**记 warning 后返回 None**，回落无检查点路径。可选能力绝不允许打断业务链路
（与 Mock-first 同一取向）。
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from typing import Any

from app.core.config import settings

logger = logging.getLogger("app.core.checkpoint")

_lock = threading.Lock()
_saver: Any = None
_attempted = False


def enabled() -> bool:
    """当前是否要求启用断点续跑。"""
    return bool(settings.workflow_checkpoint_enabled)


def get_checkpointer() -> Any:
    """返回 checkpointer 实例；关闭 / 构建失败 → ``None``。

    进程内单例（每进程只尝试构建一次），避免每次建图都开新连接。
    """
    global _saver, _attempted
    if not enabled():
        return None
    with _lock:
        if _attempted:
            return _saver
        _attempted = True
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except Exception as exc:  # noqa: BLE001 — 依赖缺失必须降级
            logger.warning(
                "langgraph_sqlite_checkpointer_unavailable error_type=%s "
                "→ 回落无检查点路径（业务不受影响）",
                type(exc).__name__,
            )
            return None
        try:
            path = settings.workflow_checkpoint_path
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            # check_same_thread=False：工作流跑在后台线程，checkpointer 可能被复用
            conn = sqlite3.connect(path, check_same_thread=False)
            _saver = SqliteSaver(conn)
            logger.info("workflow_checkpoint_enabled path=%s", path)
        except Exception as exc:  # noqa: BLE001 — 路径/版本问题同样降级
            logger.warning(
                "workflow_checkpoint_init_failed error_type=%s path=%s "
                "→ 回落无检查点路径（业务不受影响）",
                type(exc).__name__,
                settings.workflow_checkpoint_path,
            )
            _saver = None
        return _saver


def graph_config(workflow_id: str, checkpointer: Any) -> dict[str, Any]:
    """组装 ``stream()`` 用的 config。

    checkpointer 为 ``None`` 时**只带 recursion_limit**，与迁移前的
    ``{"recursion_limit": 50}`` 完全一致；启用时额外带 thread_id
    （LangGraph 要求 checkpointer 模式下必须有 ``configurable.thread_id``）。
    """
    config: dict[str, Any] = {"recursion_limit": 50}
    if checkpointer is not None:
        config["configurable"] = {"thread_id": workflow_id}
    return config


def reset() -> None:
    """清空单例与"已尝试"标记（仅供测试）。"""
    global _saver, _attempted
    with _lock:
        _saver = None
        _attempted = False
