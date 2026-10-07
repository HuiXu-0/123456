"""`trusted_rag` 的标准 Tool 形态（R4.3）—— 只解决**执行链**，不重写 RAG。

## 为什么单独一个文件

`registry.py` → `rag_tool.py`（既有依赖方向）。若把本类放回 `rag_tool.py`，
它就要反向 import `registry` 的 `AgentTool` / `ToolSpec` → **循环导入**。
故单独成文件，只依赖 `registry` + `rag_tool`，方向单向。

## 与既有工厂形态的关系

`trusted_rag` 此前只有**工厂**形态（`create_rag_tool(user_id=..., trace_id=...)`），
被 `agents/resource_rag_shadow.py`（实验侧）消费。R4.3 给它**追加一个 Spec 形态**，
供 `ToolRegistry.execute()` 走：

```
Tool Loop → ToolRegistry.execute("trusted_rag", ...) → RagSearchTool → RAGTool.search()
```

**两个命名空间相互独立**（见 `register_spec` 文档），互不覆盖 ——
既有影子路径一行未改，RAG 算法一行未改。

## Mock

`mock_rag_search()` 返回固定切片；真实路径因向量库/嵌入不可用而失败时回落 mock
并标 ``degraded=True``（与其余 Tool 同一套降级语义）。
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents.tools.rag_tool import RAGTool
from app.agents.tools.registry import (
    RAG_TOOL_NAME,
    AgentTool,
    ToolSpec,
    get_tool_registry,
)

logger = logging.getLogger("app.agents.tools.rag_search")

RAG_SEARCH_OPERATION = "search"
MAX_TOP_K = 20
MAX_QUERY_LEN = 512


class RagSearchInput(BaseModel):
    """``trusted_rag`` 入参（类型 / 范围 / 必填全部在此校验）。"""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=64, description="目标用户（必须与 Tool 绑定身份一致）")
    query: str = Field(min_length=1, max_length=MAX_QUERY_LEN, description="检索查询词")
    top_k: int = Field(default=5, ge=1, le=MAX_TOP_K, description="返回切片数")
    knowledge_scope: str | None = Field(
        default=None, max_length=128, description="可选：知识域范围"
    )


def mock_rag_search(*, user_id: str, query: str, top_k: int = 5) -> dict[str, Any]:
    """确定性 mock：固定切片，同参数 → 同结果（无网络、无向量库）。"""
    chunks = [
        {
            "id": f"mock_{index + 1}",
            "content": f"「{query}」的第 {index + 1} 条确定性证据切片。",
            "metadata": {"docId": f"doc_{index + 1}", "title": f"Mock 文档 {index + 1}"},
            "score": round(0.9 - index * 0.05, 2),
        }
        for index in range(max(0, top_k))
    ]
    return {
        "tool": RAG_TOOL_NAME,
        "operation": RAG_SEARCH_OPERATION,
        "mock": True,
        "userId": user_id,
        "query": query,
        "count": len(chunks),
        "chunks": chunks,
    }


class RagSearchTool(AgentTool):
    """``trusted_rag`` 标准 Tool：把 `RAGTool.search()` 包成 AgentTool 契约。"""

    name = RAG_TOOL_NAME
    description = (
        "在可信知识库中检索与查询相关的证据切片（混合检索 + 重排），"
        "返回带来源元数据与置信分的 chunks。仅返回结构化 JSON。"
    )
    args_schema = RagSearchInput
    operation = RAG_SEARCH_OPERATION

    def _run(self, data: RagSearchInput) -> dict[str, Any]:
        try:
            return self._real(data)
        except Exception as exc:  # noqa: BLE001 — 向量库/嵌入不可用时降级，不打断 Loop
            logger.warning(
                "tool_%s_degraded error_type=%s → 回落 mock",
                self.name,
                type(exc).__name__,
            )
            payload = mock_rag_search(
                user_id=data.user_id, query=data.query, top_k=data.top_k
            )
            payload["degraded"] = True
            payload["errorType"] = type(exc).__name__
            return payload

    def _real(self, data: RagSearchInput) -> dict[str, Any]:
        # 复用既有 RAGTool（其内部持有 RAGService + traceId 校验），不重写检索
        rag = RAGTool(user_id=self.user_id)
        response = rag.search(
            query=data.query,
            knowledge_scope=data.knowledge_scope,
            top_k=data.top_k,
        )
        payload = response.model_dump(mode="json")
        return {
            "tool": self.name,
            "operation": self.operation,
            "mock": False,
            "userId": data.user_id,
            "query": data.query,
            "count": payload.get("evidence_count", 0),
            "chunks": payload.get("chunks") or payload.get("results") or [],
            "response": payload,
        }


RAG_SEARCH_SPEC = ToolSpec(
    name=RAG_TOOL_NAME,
    description=RagSearchTool.description,
    args_schema=RagSearchInput,
    factory=RagSearchTool,
    operation=RAG_SEARCH_OPERATION,
)

# 导入即注册（agents/tools/__init__.py 负责触发导入）
get_tool_registry().register_spec(RAG_SEARCH_SPEC)
