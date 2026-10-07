"""Memory Tool（R4.2）：把 R4.1 的 `MemoryPort` 暴露为标准 Tool。

## 实现的操作

``memory/search`` → ``search_user_memory``

底层**只经 `MemoryPort`**（``get_memory(user_id)``），因此：

| 不接触 | 原因 |
|---|---|
| SQLite Session / 连接 / cursor | 由 `LongTermMemory` 内部自开自关 |
| ORM 实体 | 只拿到可 JSON 序列化的 `Evidence` |
| service 内部私有状态 | 跨池读会话走 `short_term`，不碰 `_sessions` 字典本身 |

## 用户隔离（纵深两层）

1. **绑定身份**：Tool 构造时注入 `user_id`，`_real()` 用它取记忆
   （``get_memory(data.user_id)`` 返回**用户绑定视图**，`remember`/`recall` 自动带前缀）；
2. **交叉校验**：入参里的 `user_id` 与绑定不符 → `ToolPermissionError`
   （由 `AgentTool.execute` 统一执行）。

即：**数据访问只认绑定身份，入参只能被拒绝、不能被采信。**

## Mock

`mock_search_user_memory()` 返回**固定内容**（按 limit 截断），同参数必得同结果，
无随机、无网络。真实路径因基础设施异常失败时回落 mock 并标记 ``degraded=True``
（日志记录 errorType，**不静默**）。
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents.memory import get_memory
from app.agents.tools.registry import (
    MEMORY_SEARCH_OPERATION,
    MEMORY_SEARCH_TOOL,
    AgentTool,
    ToolSpec,
    get_tool_registry,
)

logger = logging.getLogger("app.agents.tools.memory")

# 参数边界（Pydantic 校验 + 本层再收口一次）
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_QUERY_LEN = 200
MAX_SESSION_ID_LEN = 64


class SearchUserMemoryInput(BaseModel):
    """``memory/search`` 的入参 schema（类型 / 范围 / 必填全部在此校验）。"""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=64, description="目标用户（必须与 Tool 绑定身份一致）")
    query: str = Field(default="", max_length=MAX_QUERY_LEN, description="检索词；空 = 全量")
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="返回条数上限")
    session_id: str | None = Field(
        default=None, max_length=MAX_SESSION_ID_LEN, description="可选：同时取该会话的短期消息"
    )


def mock_search_user_memory(
    *,
    user_id: str,
    query: str = "",
    limit: int = DEFAULT_LIMIT,
    session_id: str | None = None,
) -> dict[str, Any]:
    """确定性 mock（同参数 → 同结果；无随机、无网络、无 DB）。"""
    seed = [
        {
            "kind": "portrait",
            "user_id": user_id,
            "subject": "learning_goal",
            "summary": "画像 学习目标：掌握 Transformer",
            "value": {"key": "learning_goal", "value": "掌握 Transformer"},
            "observed_at": None,
            "confidence": 0.8,
        },
        {
            "kind": "mastery",
            "user_id": user_id,
            "subject": "nn",
            "summary": "掌握度 nn：learning，能力分 60（diagnostic）",
            "value": {"kpId": "nn", "status": "learning", "score": 60, "scoreSource": "diagnostic"},
            "observed_at": None,
            "confidence": 1.0,
        },
    ]
    picked = [dict(item) for item in seed[: max(0, limit)]]
    return {
        "tool": MEMORY_SEARCH_TOOL,
        "operation": MEMORY_SEARCH_OPERATION,
        "mock": True,
        "userId": user_id,
        "query": query,
        "count": len(picked),
        "evidence": picked,
        "session": None if session_id is None else {"sessionId": session_id, "messages": []},
    }


class SearchUserMemoryTool(AgentTool):
    """``memory/search``：查用户长期记忆（可选附带某会话的短期消息）。"""

    name = MEMORY_SEARCH_TOOL
    description = (
        "检索指定用户的历史学习记忆：画像维度、掌握度、学习步骤、笔记、测验、学习事件。"
        "query 为空返回全量（受 limit 限制）。仅返回结构化 JSON，不含原始数据库对象。"
    )
    args_schema = SearchUserMemoryInput
    operation = MEMORY_SEARCH_OPERATION

    def _run(self, data: SearchUserMemoryInput) -> dict[str, Any]:
        try:
            return self._real(data)
        except Exception as exc:  # noqa: BLE001 — 工具失败不应打断调用方，回落确定性 mock
            logger.warning(
                "tool_%s_degraded error_type=%s → 回落 mock",
                self.name,
                type(exc).__name__,
            )
            payload = mock_search_user_memory(
                user_id=data.user_id, query=data.query, limit=data.limit,
                session_id=data.session_id,
            )
            payload["degraded"] = True
            payload["errorType"] = type(exc).__name__
            return payload

    def _real(self, data: SearchUserMemoryInput) -> dict[str, Any]:
        # 绑定身份优先：data.user_id 已由 AgentTool 交叉校验过，二者必相等
        memory = get_memory(data.user_id)
        # Port 只有 (user_id, query) 两参 —— limit 由本层收口，不越出 Port 契约
        items = memory.long_term(data.user_id, data.query)[: data.limit]
        evidence = [item.model_dump(mode="json") for item in items]

        session: dict[str, Any] | None = None
        if data.session_id:
            messages = memory.short_term(data.session_id)
            session = {
                "sessionId": data.session_id,
                "messages": [m.model_dump(mode="json") for m in messages],
            }
        return {
            "tool": self.name,
            "operation": self.operation,
            "mock": False,
            "userId": data.user_id,
            "query": data.query,
            "count": len(evidence),
            "evidence": evidence,
            "session": session,
        }


MEMORY_SEARCH_SPEC = ToolSpec(
    name=MEMORY_SEARCH_TOOL,
    description=SearchUserMemoryTool.description,
    args_schema=SearchUserMemoryInput,
    factory=SearchUserMemoryTool,
    operation=MEMORY_SEARCH_OPERATION,
)

# 模块导入即注册（agents/tools/__init__.py 负责触发导入）
get_tool_registry().register_spec(MEMORY_SEARCH_SPEC)
