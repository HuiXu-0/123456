"""Agent-facing tool ports and adapters（R4.1 工具端口；R4.2 接入标准 Tool）。

导入本包即完成**标准 Tool 的模块级注册**（`memory_tool` / `learning_tool`
在模块底部调用 `get_tool_registry().register_spec(...)`），因此
只要触达 `app.agents.tools.*` 任一子模块，发现入口就是完整的。

注册表内当前有两族：

- **工厂族**（既有）：`trusted_rag` —— 需注入 `trace_id`，走 `create_rag_tool`；
- **Spec 族**（R4.2）：`memory/search`、`learning/profile`、`learning/mastery`、
  `learning/path` —— 走统一入口 `registry.execute(name, user_id=..., **args)`。

> ⚠️ **本阶段没有 Tool Loop**：注册与能力声明已完成，但**没有任何代码会因为
> LLM 的输出去自动调用这些 Tool**（见 R4.2 报告 §10）。
"""
from __future__ import annotations

from app.agents.tools import (  # noqa: F401 — 导入即注册
    learning_tool,
    memory_tool,
    rag_search_tool,
)
from app.agents.tools.rag_search_tool import RAG_SEARCH_OPERATION
from app.agents.tools.rag_tool import AgentRAGPort, RAGTool, RAGToolError
from app.agents.tools.registry import (
    LEARNING_MASTERY_OPERATION,
    LEARNING_MASTERY_TOOL,
    LEARNING_PATH_OPERATION,
    LEARNING_PATH_TOOL,
    LEARNING_PROFILE_OPERATION,
    LEARNING_PROFILE_TOOL,
    MEMORY_SEARCH_OPERATION,
    MEMORY_SEARCH_TOOL,
    RAG_TOOL_NAME,
    AgentTool,
    ToolPermissionError,
    ToolRegistry,
    ToolSpec,
    get_tool_registry,
)

# R4.5.2：`*_OPERATION` 与 `*_TOOL` 是同一组公开 symbol（操作名 ↔ 注册名），
# 此前只导出了 `*_TOOL`，导致 `from app.agents.tools import LEARNING_MASTERY_OPERATION`
# 直接 ImportError、tests/test_memory_tools.py 整文件无法收集。
# 两侧一致后，公开面完整且与 registry.py 的定义一一对应。
__all__ = [
    "AgentRAGPort",
    "AgentTool",
    "LEARNING_MASTERY_OPERATION",
    "LEARNING_MASTERY_TOOL",
    "LEARNING_PATH_OPERATION",
    "LEARNING_PATH_TOOL",
    "LEARNING_PROFILE_OPERATION",
    "LEARNING_PROFILE_TOOL",
    "MEMORY_SEARCH_OPERATION",
    "MEMORY_SEARCH_TOOL",
    "RAG_SEARCH_OPERATION",
    "RAG_TOOL_NAME",
    "RAGTool",
    "RAGToolError",
    "ToolPermissionError",
    "ToolRegistry",
    "ToolSpec",
    "get_tool_registry",
    "learning_tool",
    "memory_tool",
]
