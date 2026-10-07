"""LangChain 兼容层（R4.3 决策 A）——**先设计兼容层，再逐步接入**。

## 为什么需要这层

R4.3 正式引入 `langchain-core`，但**不能让它成为硬失败点**：

- `langchain-core` 是本阶段**新增**的依赖，版本配对若与测试服务器不符，
  硬导入会让 `agents/tools/__init__` 的模块级注册把**整个应用带崩**；
- 项目纪律要求 Mock-first、无 Key 可跑，任何新依赖都必须可降级
  （与 `core/checkpoint.py` 的做法一致）。

因此本模块把所有 LangChain 触点收敛到一处，**统一 try/except 降级**：

| 能力 | LangChain 可用时 | 不可用时 |
|---|---|---|
| `tool_schemas()` | `convert_to_openai_tool`（OpenAI 标准 tool 格式） | 自算 `model_json_schema()`，**形状一致** |
| `as_langchain_tool()` | 包成真 `BaseTool` | 返回 ``None`` |
| `to_messages()` | `HumanMessage` / `AIMessage` / `ToolMessage` | 返回空列表 |

**调用方只依赖本模块的函数签名**，因此 LangChain 在与不在，Tool Loop 都能跑。

## 本阶段真正用到 LangChain 的地方

1. **`tool_schemas()` —— 关键路径**：把 `ToolSpec.args_schema`（Pydantic）
   转成模型能读的 tool 描述，喂给 `LLMClient.decide_tool()`；
2. **`to_messages()` —— trace 增强**：把本轮 tool_call/observation 序列化成
   LangChain 标准消息，进内部 trace（不进 11.2）。

**刻意不做的**：不引入 `langchain` 主包、不用 `create_agent` / `create_react_agent`
（禁止项），Agent Runtime 仍是自研的 `app.agents.tool_loop.ToolLoop`。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("app.agents.langchain")

# ── 可选依赖探测（导入失败不抛，只降级）──────────────────────────────────────
try:  # noqa: SIM105
    from langchain_core.tools import BaseTool as _BaseTool
except Exception:  # noqa: BLE001
    _BaseTool = None  # type: ignore[assignment]

try:  # noqa: SIM105
    from langchain_core.utils.function_calling import convert_to_openai_tool
except Exception:  # noqa: BLE001
    convert_to_openai_tool = None  # type: ignore[assignment]

try:  # noqa: SIM105
    from langchain_core.messages import (
        AIMessage as _AIMessage,
        HumanMessage as _HumanMessage,
        ToolMessage as _ToolMessage,
    )
except Exception:  # noqa: BLE001
    _HumanMessage = _AIMessage = _ToolMessage = None  # type: ignore[assignment]

if _BaseTool is None:  # pragma: no cover - 取决于环境是否装了 langchain-core
    logger.warning(
        "langchain_core 不可用 → tool schema / message 降级为自算实现（功能不减，仅格式来源不同）"
    )


def has_langchain() -> bool:
    """当前环境是否装了 langchain-core。"""
    return _BaseTool is not None and convert_to_openai_tool is not None


def as_langchain_tool(spec: Any) -> Any | None:
    """把一个 :class:`ToolSpec` 包成 LangChain ``BaseTool``；不可用 → ``None``。

    产出的 BaseTool 与本项目的 ``AgentTool`` 同名同形
    （``name`` / ``description`` / ``args_schema``），执行仍委托回
    ``ToolSpec.factory`` —— **不绕过 ToolRegistry 的安全边界**。
    """
    if _BaseTool is None:
        return None

    class _SpecTool(_BaseTool):  # type: ignore[misc, valid-type]
        name: str = ""            # type: ignore[assignment]
        description: str = ""     # type: ignore[assignment]
        args_schema: Any = None   # type: ignore[assignment]
        _spec: Any = None

        def _run(self, **kwargs: Any) -> Any:  # pragma: no cover - 由 Loop 走 Registry
            tool = self._spec.factory(user_id=kwargs.get("user_id") or "")
            return tool.execute(**kwargs)

    try:
        wrapper = _SpecTool()
        wrapper.name = spec.name
        wrapper.description = spec.description
        wrapper.args_schema = spec.args_schema
        wrapper._spec = spec
        return wrapper
    except Exception as exc:  # noqa: BLE001 — BaseTool 各版本构造细节不同，失败即降级
        logger.warning("as_langchain_tool_failed tool=%s error_type=%s", spec.name, type(exc).__name__)
        return None


def _own_schema(spec: Any) -> dict[str, Any]:
    """无 LangChain 时的自算 tool 描述（与 OpenAI function 格式同构）。"""
    schema = spec.args_schema.model_json_schema()
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": schema,
        },
    }


def tool_schema(spec: Any) -> dict[str, Any]:
    """单个 Tool → 模型可读的描述（三层降级：BaseTool → Pydantic 模型 → 自算）。

    无论走哪条路，**`function.name` 恒等于 `spec.name`**（权限键），
    保证模型看到的名字与白名单校验的名字一致。
    """
    if convert_to_openai_tool is not None:
        # 路径 1：包成 BaseTool 再转（LangChain 标准链路）
        lc_tool = as_langchain_tool(spec)
        if lc_tool is not None:
            try:
                payload = convert_to_openai_tool(lc_tool)
                payload.setdefault("function", {})
                payload["function"]["name"] = spec.name
                payload["function"]["description"] = spec.description
                return payload
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "tool_schema_base_tool_failed tool=%s error_type=%s → 退 Pydantic 模型",
                    spec.name,
                    type(exc).__name__,
                )
        # 路径 2：直接把 args_schema（Pydantic 类）转 OpenAI 格式
        try:
            payload = convert_to_openai_tool(spec.args_schema)
            payload.setdefault("function", {})
            payload["function"]["name"] = spec.name
            payload["function"]["description"] = spec.description
            return payload
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "tool_schema_convert_failed tool=%s error_type=%s → 退自算 schema",
                spec.name,
                type(exc).__name__,
            )
    # 路径 3：完全不依赖 LangChain
    return _own_schema(spec)


def tool_schemas(specs: Any) -> list[dict[str, Any]]:
    """一批 ToolSpec → 模型可读的描述列表（保持输入顺序，便于测试断言）。"""
    return [tool_schema(spec) for spec in specs]


def to_messages(history: list[dict[str, Any]]) -> list[Any]:
    """内部 Tool Loop 历史 → LangChain 标准消息（供 trace 使用）。

    可用时返回 ``HumanMessage`` / ``AIMessage`` / ``ToolMessage``；
    langchain-core 不可用、**或消息构造因版本差异抛错**时返回空列表。

    R4.5 依赖核对：``AIMessage(tool_calls=[...])`` 的入参形状在 langchain-core
    大版本间有调整史，本函数**整体 try/except** —— trace 增强失败绝不能影响主链路。
    权威历史始终是入参 ``history``，调用方不得用本函数判断历史是否为空。
    """
    if _HumanMessage is None:
        return []
    messages: list[Any] = []
    try:
        for item in history:
            kind = str(item.get("kind") or "")
            if kind == "tool_call":
                messages.append(_AIMessage(content="", tool_calls=[{
                    "name": str(item.get("tool") or ""),
                    "args": dict(item.get("arguments") or {}),
                    "id": str(item.get("id") or ""),
                    "type": "tool_call",
                }]))
            elif kind == "observation":
                messages.append(_ToolMessage(
                    content=str(item.get("content") or ""),
                    tool_call_id=str(item.get("id") or ""),
                ))
            elif kind == "task":
                messages.append(_HumanMessage(content=str(item.get("content") or "")))
    except Exception as exc:  # noqa: BLE001 — 版本差异导致的构造失败 → 降级
        logger.warning(
            "langchain_to_messages_failed error_type=%s → 返回已构造部分",
            type(exc).__name__,
        )
    return messages
