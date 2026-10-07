"""Central Agent tool registry (TASK-003-E3；R4.2 扩展为标准 Tool 契约)。

## 两种注册形态（并存，互不干扰）

| 形态 | 用途 | 注册 | 执行 |
|---|---|---|---|
| **工厂**（R4.3-E3 既有） | `trusted_rag` 这类需要注入 `trace_id` 的能力 | `register(name, factory)` | `create(name, **kwargs)` / `create_rag_tool(...)` |
| **Spec**（R4.2 新增） | 带 `args_schema`、需按用户鉴权的标准 Tool | `register_spec(spec)` | **`execute(name, user_id=..., **args)`** |

`available_tools` 同时包含两者，故**发现入口统一**；`spec(name)` /
`execute(name, ...)` 只覆盖 Spec 形态（`trusted_rag` 走既有 `create_rag_tool`）。

## 为什么 AgentTool 不直接继承 LangChain `BaseTool`

`langchain` / `langchain-core` **不在 `requirements.txt` 中，版本未锁，本阶段又不可验证**
（R4.2 按指示不在本机跑测试）。此时引入未验证依赖，一旦 `BaseTool` API 与假设不符，
`agents/tools/__init__` 的模块级注册会让**整个应用起不来**。

因此本模块定义 **`AgentTool`**，字段与 LangChain `BaseTool` **同名同形**
（`name` / `description` / `args_schema` / Pydantic 校验），R4.3 若要接
`langchain-core` 可**机械映射**（见 R4.2 报告 §5 的决策项）。

## 身份与权限

- Tool **必须在构造时绑定 `user_id`**（与既有 `RAGTool(user_id=...)` 同款）；
- 数据访问一律用**绑定身份**，不读入参里的 `user_id`；
- 入参 `user_id` 仅作**交叉校验**：与绑定不符 → `ToolPermissionError`
  （纵深防御：即便调用方漏传也不会读到他人数据）。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel

from app.agents.tools.rag_tool import AgentRAGPort, RAGTool

ToolFactory = Callable[..., object]

# ── 已注册 Tool 名（= 权限键；BaseAgent.tools 声明用的就是这些）───────────────
RAG_TOOL_NAME = "trusted_rag"
MEMORY_SEARCH_TOOL = "memory/search"
LEARNING_PROFILE_TOOL = "learning/profile"
LEARNING_MASTERY_TOOL = "learning/mastery"
LEARNING_PATH_TOOL = "learning/path"

# Tool 实现的操作名（对应 R4.2 规格里的 search_user_memory / get_learning_profile …）
MEMORY_SEARCH_OPERATION = "search_user_memory"
LEARNING_PROFILE_OPERATION = "get_learning_profile"
LEARNING_MASTERY_OPERATION = "get_mastery_state"
LEARNING_PATH_OPERATION = "get_learning_path"


class ToolPermissionError(PermissionError):
    """越权：请求的 user_id 与 Tool 绑定身份不符。"""


@dataclass(frozen=True)
class ToolSpec:
    """一个已注册 Tool 的可发现描述（发现 / schema 查询 / 执行共用）。

    Attributes:
        name: 注册名兼**权限键**（如 ``memory/search``）。
        description: 供 LLM 理解用途的自然语言描述。
        args_schema: Pydantic 模型 —— 类型与范围校验的唯一依据。
        factory: ``(user_id=...) -> AgentTool``；身份**只从这里注入**。
        operation: 实现层操作名（如 ``search_user_memory``），仅作可读标识。
    """

    name: str
    description: str
    args_schema: type[BaseModel]
    factory: Callable[..., Any]
    operation: str = ""

    def json_schema(self) -> dict[str, Any]:
        """args_schema 的 JSON Schema（供上层/前端查询）。"""
        return self.args_schema.model_json_schema()

    def to_dict(self) -> dict[str, Any]:
        """可 JSON 序列化的 Tool 描述。"""
        fields = getattr(self.args_schema, "model_fields", {})
        return {
            "name": self.name,
            "operation": self.operation,
            "description": self.description,
            "requiresUserId": "user_id" in fields,
            "argsSchema": self.json_schema(),
        }


class AgentTool:
    """标准 Tool 基类（形状对齐 LangChain ``BaseTool``，见模块文档）。

    子类只需实现 ``_run(data) -> dict``；校验、鉴权、JSON 收口由本类统一完成。
    """

    name: str = ""
    description: str = ""
    args_schema: type[BaseModel] = BaseModel
    operation: str = ""

    def __init__(self, *, user_id: str) -> None:
        uid = (user_id or "").strip()
        if not uid:
            raise ValueError(f"tool {type(self).__name__} requires a bound user_id")
        if not (isinstance(self.args_schema, type) and issubclass(self.args_schema, BaseModel)):
            raise TypeError(f"tool {type(self).__name__}.args_schema must be a Pydantic BaseModel")
        if not self.name.strip() or not self.description.strip():
            raise TypeError(f"tool {type(self).__name__} must define name and description")
        self._user_id = uid

    @property
    def user_id(self) -> str:
        """绑定身份 —— **数据访问只认它**，与入参无关。"""
        return self._user_id

    def _authorize(self, requested: Any) -> None:
        """交叉校验：入参 user_id 与绑定身份不符 → 拒绝执行。"""
        if requested is None:
            return
        if str(requested).strip() != self._user_id:
            raise ToolPermissionError(
                f"tool {self.name} 拒绝越权访问：请求 {requested!r}，绑定 {self._user_id!r}"
            )

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """校验入参 → 鉴权 → 执行 → 收口为可 JSON 序列化的 dict。

        Raises:
            pydantic.ValidationError: 类型 / 范围校验失败。
            ToolPermissionError: 越权。
        """
        data = self.args_schema.model_validate(kwargs)
        self._authorize(getattr(data, "user_id", None))
        result = self._run(data)
        if not isinstance(result, dict):
            raise TypeError(
                f"tool {self.name}._run must return dict, got {type(result).__name__}"
            )
        return result

    def _run(self, data: BaseModel) -> dict[str, Any]:
        """子类实现：真实执行。"""
        raise NotImplementedError

    def spec(self) -> ToolSpec:
        """导出为可注册的 :class:`ToolSpec`。"""
        return ToolSpec(
            name=self.name,
            description=self.description,
            args_schema=self.args_schema,
            factory=type(self),
            operation=self.operation,
        )


class ToolRegistry:
    """Small extensible registry; RAGTool is the first built-in capability."""

    def __init__(self) -> None:
        self._factories: dict[str, ToolFactory] = {RAG_TOOL_NAME: RAGTool}
        self._specs: dict[str, ToolSpec] = {}

    @property
    def available_tools(self) -> tuple[str, ...]:
        """已注册 Tool 名（工厂 + Spec 两种形态的并集，字典序）。"""
        return tuple(sorted(set(self._factories) | set(self._specs)))

    # ── 工厂形态（既有 API，未改动）────────────────────────────────────────────

    def register(
        self, name: str, factory: ToolFactory, *, replace: bool = False
    ) -> None:
        normalized = name.strip()
        if not normalized:
            raise ValueError("tool name must not be blank")
        if normalized in self._factories and not replace:
            raise ValueError(f"tool already registered: {normalized}")
        self._factories[normalized] = factory

    def create(self, name: str, **kwargs: Any) -> object:
        try:
            factory = self._factories[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc
        return factory(**kwargs)

    def create_rag_tool(self, *, user_id: str, trace_id: str) -> AgentRAGPort:
        tool = self.create(RAG_TOOL_NAME, user_id=user_id, trace_id=trace_id)
        if not isinstance(tool, AgentRAGPort):
            raise TypeError("registered trusted_rag tool does not implement AgentRAGPort")
        return cast(AgentRAGPort, tool)

    # ── Spec 形态（R4.2 新增）─────────────────────────────────────────────────

    def register_spec(self, spec: ToolSpec, *, replace: bool = False) -> ToolSpec:
        """注册一个标准 Tool（同名重复且未 ``replace`` → ValueError）。

        **Spec 命名空间与工厂命名空间相互独立** —— 同一个名字可以同时有
        工厂形态（``create_rag_tool``，供既有影子路径用）与 Spec 形态
        （``execute``，供 Tool Loop 用），两者互不覆盖。
        """
        if not spec.name or not spec.name.strip():
            raise ValueError("tool name must not be blank")
        if not spec.description or not spec.description.strip():
            raise ValueError(f"tool {spec.name} must declare a non-blank description")
        if not (isinstance(spec.args_schema, type) and issubclass(spec.args_schema, BaseModel)):
            raise TypeError(f"tool {spec.name}.args_schema must be a Pydantic BaseModel")
        if not callable(spec.factory):
            raise TypeError(f"tool {spec.name}.factory must be callable")
        if spec.name in self._specs and not replace:
            raise ValueError(f"tool already registered: {spec.name}")
        self._specs[spec.name] = spec
        return spec

    def spec(self, name: str) -> ToolSpec:
        """按名取 Tool 描述（发现 / schema 查询入口）。未注册 → KeyError。"""
        try:
            return self._specs[name]
        except KeyError:
            available = ", ".join(sorted(self._specs)) or "(none)"
            raise KeyError(
                f"unknown tool spec: {name}；已注册 Spec: {available}"
            ) from None

    def specs(self) -> tuple[ToolSpec, ...]:
        """全部已注册 Spec（字典序）。"""
        return tuple(self._specs[name] for name in sorted(self._specs))

    def execute(self, name: str, *, actor_id: str, **kwargs: Any) -> Any:
        """**唯一标准执行入口**（白名单 → 构造（绑身份）→ 校验 → 鉴权 → 执行）。

        不允许绕过本方法直接调用 Tool：白名单在这里收口，
        未注册的名字直接 ``KeyError``。

        两个身份字段是**不同角色**，刻意分开：

        | 字段 | 来源 | 语义 |
        |---|---|---|
        | ``actor_id`` | **运行时注入**（当前登录用户） | Tool 的绑定身份 —— 数据访问只认它 |
        | ``kwargs["user_id"]`` | **调用方给出**（未来由 LLM 产出） | 目标用户 —— 与绑定不符即被拒 |

        这样一来 **经 Registry 调用时越权守卫是可测的**
        （``actor_id="a", user_id="b"`` → ``ToolPermissionError``），
        而不是被参数注入悄悄"修正"掉。R4.3 的 Tool Loop 里
        ``actor_id`` 由运行时给、``user_id`` 由模型给，语义天然对上。

        Args:
            name: 已注册的 Spec 名（``memory/search`` 等）。
            actor_id: **绑定身份** —— Tool 只会读这个用户的数据。
            kwargs: 透传给 ``args_schema`` 校验的入参（含 ``user_id``）。

        Returns:
            可 JSON 序列化的 dict。
        """
        spec = self.spec(name)
        tool = spec.factory(user_id=actor_id)
        return tool.execute(**kwargs)


_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
