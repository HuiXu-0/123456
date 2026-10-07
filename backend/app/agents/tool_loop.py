"""Tool Loop Runtime（R4.3）—— 自研的**受控**工具循环，不是 ReAct 全量替换。

## 这是什么

```
LLM ──决策──▶ 结构化 Tool Call 或 Final Answer
                 │
                 ├─ tool_call ─▶ 白名单/权限/args_schema 校验 ─▶ ToolRegistry.execute()
                 │                                                     │
                 │◀────────────── Observation（JSON，回灌模型）◀────────┘
                 │
                 └─ final_answer ─▶ 结束
```

**三条硬边界**：

1. **不绕过 ToolRegistry** —— 唯一执行入口是 ``ToolRegistry.execute()``，
   LLM 永远拿不到 Python 对象、DB Session 或 ORM；
2. **不超过 ``MAX_TOOL_STEPS=3``** —— 预算耗尽**不抛异常**，返回
   ``truncated=True`` + 最后一次 Observation + ``fallback_reason``；
3. **权限来自声明** —— 可用工具 = ``BaseAgent.tools``（AgentRegistry）
   ∩ 已注册 Spec（ToolRegistry），**两处都过才可用**。

## 为什么不用 `create_react_agent`

禁止项之一。本模块是**自研运行时**：决策由 ``LLMClient.decide_tool`` 产出
（结构化双路径），循环、校验、回灌、预算、降级全部自控 ——
LangChain 只经 ``langchain_adapter`` 提供 tool schema 与 message 标准（兼容层）。

## 决策只能二选一（结构化，不猜自然语言）

- ``{"kind":"tool_call","tool":...,"arguments":{...}}``
- ``{"kind":"final_answer","answer":...}``

非 JSON / 结构不符 / 缺字段 → **判为非法决策**：拒绝执行、记 error、回喂模型，
**绝不把"听起来想调工具"的散文当成调用**。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.agents import langchain_adapter
from app.agents import registry as agent_registry
from app.agents.tools.registry import ToolSpec, get_tool_registry

logger = logging.getLogger("app.agents.tool_loop")

# 预算：固定 3，不做无限循环
MAX_TOOL_STEPS = 3

# 决策提供方签名：(task=, agent_id=, allowed_tools=, history=) -> dict
DecisionProvider = Callable[..., dict[str, Any]]


def _default_decision_provider(**kwargs: Any) -> dict[str, Any]:
    """默认决策源：走 ``LLMClient.decide_tool``（mock 确定性 / deepseek 真实）。

    延迟 import：``app.core.llm`` 依赖面大，避免在模块导入期把它拉进来。
    """
    from app.core.llm import get_llm

    return get_llm().decide_tool(**kwargs)


# ── 决策（结构化双路径）───────────────────────────────────────────────────────


class ToolCall(BaseModel):
    """路径 A：需要更多信息 → 调用工具。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["tool_call"] = "tool_call"
    tool: str = Field(min_length=1, description="必须属于当前 Agent 已授权且已注册的工具")
    arguments: dict[str, Any] = Field(default_factory=dict)


class FinalAnswer(BaseModel):
    """路径 B：信息已足够 → 输出最终答复。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["final_answer"] = "final_answer"
    answer: str = Field(min_length=1)


Decision = ToolCall | FinalAnswer


def _loads(text: str) -> Any:
    """严格 JSON 解析：剥 ``` 围栏后整体解析，**失败即 None**。

    刻意**不**去散文里扫 ``{...}`` —— 那会把"听起来想调工具"的自然语言
    误判成调用（规格 §4 明令禁止）。
    """
    payload = (text or "").strip()
    if payload.startswith("```"):
        parts = payload.split("```")
        payload = parts[1] if len(parts) >= 2 else payload
        if payload.lstrip().lower().startswith("json"):
            payload = payload.lstrip()[4:]
        payload = payload.strip()
    try:
        return json.loads(payload)
    except Exception:  # noqa: BLE001 — 非法结构一律拒绝，由上层回喂
        return None


def parse_decision(raw: Any) -> Decision | None:
    """把模型输出解析成结构化决策；任何不合规 → ``None``（拒绝执行）。"""
    if isinstance(raw, (ToolCall, FinalAnswer)):
        return raw
    if isinstance(raw, str):
        data = _loads(raw)
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        return None
    if not isinstance(data, dict):
        return None
    kind = str(data.get("kind") or "")
    try:
        if kind == "tool_call":
            return ToolCall.model_validate(data)
        if kind == "final_answer":
            return FinalAnswer.model_validate(data)
    except Exception:  # noqa: BLE001 — 缺字段/类型不符 → 非法
        return None
    return None


# ── Observation / Trace ──────────────────────────────────────────────────────


class Observation(BaseModel):
    """一次 Tool 执行的**标准回灌载荷**（模型可见，但不含任何内部对象）。"""

    model_config = ConfigDict(extra="forbid")

    success: bool
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] | None = None
    degraded: bool = False
    error: str | None = None
    error_type: str | None = None
    duration_ms: float = 0.0
    step: int = 0


class StepRecord(BaseModel):
    """内部 trace 单条（**不进 11.2**）。"""

    model_config = ConfigDict(extra="forbid")

    step: int
    agent: str
    decision: str  # tool_call | final_answer | invalid_decision | decision_error
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    success: bool = False
    degraded: bool = False
    error: str | None = None
    duration_ms: float = 0.0


class ToolLoopResult(BaseModel):
    """一次 Tool Loop 的完整结果（可 JSON 序列化，可进 State / trace）。"""

    model_config = ConfigDict(extra="forbid")

    answer: str = ""
    deliverable: bool = False
    truncated: bool = False
    tool_steps: int = Field(default=0, description="消耗的决策步数（0..MAX_TOOL_STEPS）")
    tool_calls: int = Field(default=0, description="实际成功执行的 Tool 次数")
    last_tool: str | None = None
    fallback_reason: str | None = None
    error: str | None = None
    steps: list[StepRecord] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)
    final_observation: dict[str, Any] | None = None

    def messages(self) -> list[Any]:
        """内部历史 → LangChain 标准消息（langchain 不可用时为空列表）。"""
        return langchain_adapter.to_messages(self.history)


# ── Runtime ──────────────────────────────────────────────────────────────────


class ToolLoop:
    """受控 Tool Loop 运行时。

    Args:
        agent_id: 决策主体 —— 权限来自 ``AgentRegistry.tools_of(agent_id)``。
        actor_id: **运行时身份** —— 注入 Tool 的绑定身份；数据只读这个用户。
        registry: Tool 注册表（默认进程单例）。
        decision_provider: 决策源（默认 ``LLMClient.decide_tool``）；
            测试可注入脚本化 provider 以获得确定性。
        max_steps: 预算，默认 ``MAX_TOOL_STEPS=3``。
    """

    def __init__(
        self,
        *,
        agent_id: str,
        actor_id: str,
        registry: Any | None = None,
        decision_provider: DecisionProvider | None = None,
        max_steps: int = MAX_TOOL_STEPS,
    ) -> None:
        if not agent_id or not agent_id.strip():
            raise ValueError("agent_id must not be blank")
        if not actor_id or not actor_id.strip():
            raise ValueError("actor_id must not be blank")
        if max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        self.agent_id = agent_id.strip()
        self.actor_id = actor_id.strip()
        self._registry = registry or get_tool_registry()
        self._provider = decision_provider or _default_decision_provider
        self.max_steps = int(max_steps)

    # ── 权限（BaseAgent.tools ∩ 已注册 Spec）────────────────────────────────

    @property
    def permitted(self) -> frozenset[str]:
        """该 Agent 声明可用的工具名（取不到 → 空集，**默认拒绝**）。"""
        try:
            return frozenset(agent_registry.tools_of(self.agent_id))
        except KeyError:
            logger.warning("tool_loop_unknown_agent agent_id=%s → 无任何工具权限", self.agent_id)
            return frozenset()

    @property
    def allowed_specs(self) -> tuple[ToolSpec, ...]:
        """真正可用的工具：**声明 ∩ 已注册**，两处都过才可用。"""
        permitted = self.permitted
        return tuple(spec for spec in self._registry.specs() if spec.name in permitted)

    @property
    def allowed_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.allowed_specs)

    def schemas(self) -> list[dict[str, Any]]:
        """暴露给模型的 tool 描述（经 LangChain 兼容层，见 langchain_adapter）。"""
        return langchain_adapter.tool_schemas(self.allowed_specs)

    # ── 决策 ─────────────────────────────────────────────────────────────────

    def decide(self, task: str, history: Sequence[Mapping[str, Any]]) -> Decision | None:
        """取一次结构化决策；非法 / 出错 → ``None``（由调用方记 error）。"""
        raw = self._provider(
            task=task,
            agent_id=self.agent_id,
            allowed_tools=self.schemas(),
            history=[dict(item) for item in history],
        )
        return parse_decision(raw)

    # ── 执行 ─────────────────────────────────────────────────────────────────

    def execute(self, call: ToolCall, *, step: int) -> Observation:
        """执行一次 Tool Call —— **永不抛异常**，一切失败都变成 Observation。"""
        started = time.perf_counter()
        arguments = dict(call.arguments or {})

        def _fail(error: str, error_type: str | None = None) -> Observation:
            return Observation(
                success=False,
                tool=call.tool,
                arguments=arguments,
                error=error,
                error_type=error_type,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                step=step,
            )

        # 1) 白名单：不在该 Agent 的授权集 → 拒绝，不执行
        if call.tool not in self.permitted:
            logger.warning(
                "tool_loop_rejected agent=%s tool=%s reason=not_permitted",
                self.agent_id,
                call.tool,
            )
            return _fail(
                f"工具 {call.tool!r} 未授权给 Agent {self.agent_id!r}",
                "ToolPermissionError",
            )

        # 2) 注册表白名单（未注册 → KeyError）
        try:
            spec = self._registry.spec(call.tool)
        except KeyError:
            logger.warning("tool_loop_rejected tool=%s reason=not_registered", call.tool)
            return _fail(f"工具 {call.tool!r} 未在 ToolRegistry 注册", "KeyError")

        # 3) 身份注入：schema 要求 user_id 且模型未给 → 用运行时 actor_id
        if "user_id" in getattr(spec.args_schema, "model_fields", {}) and not arguments.get(
            "user_id"
        ):
            arguments["user_id"] = self.actor_id

        # 4) 执行（唯一入口 = ToolRegistry.execute；args_schema 校验 + 越权校验在其内）
        try:
            data = self._registry.execute(spec.name, actor_id=self.actor_id, **arguments)
        except Exception as exc:  # noqa: BLE001 — ValidationError / ToolPermissionError / 运行时错
            logger.warning(
                "tool_loop_exec_failed tool=%s error_type=%s", spec.name, type(exc).__name__
            )
            return _fail(str(exc) or type(exc).__name__, type(exc).__name__)

        if not isinstance(data, dict):
            return _fail(f"工具返回了非 dict 结果：{type(data).__name__}", "TypeError")

        return Observation(
            success=True,
            tool=spec.name,
            arguments=arguments,
            data=data,
            degraded=bool(data.get("degraded")),
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            step=step,
        )

    # ── 一步式循环（非图调用方 / 测试）────────────────────────────────────────

    def run(self, task: str, *, history: Sequence[Mapping[str, Any]] | None = None) -> ToolLoopResult:
        """跑完整循环：最多 ``max_steps`` 轮，超出即 ``truncated=True`` 且**不抛错**。

        Args:
            task: 任务 / 用户诉求。
            history: 既有历史（供决策源读取），会话内累加。

        Returns:
            ``ToolLoopResult`` —— 含 answer / truncated / tool_steps / last_tool /
            fallback_reason / steps（内部 trace）/ final_observation。
        """
        turns: list[dict[str, Any]] = [dict(item) for item in (history or [])]
        steps: list[StepRecord] = []
        answer = ""
        error: str | None = None
        last_tool: str | None = None
        final_observation: dict[str, Any] | None = None
        tool_calls = 0
        tool_steps = 0
        truncated = False
        fallback_reason: str | None = None

        for step in range(1, self.max_steps + 1):
            tool_steps = step
            started = time.perf_counter()
            try:
                decision = self.decide(task, turns)
            except Exception as exc:  # noqa: BLE001 — 决策源故障：回喂，不崩 Loop
                error = f"决策失败：{type(exc).__name__}: {exc}"
                logger.warning("tool_loop_decision_error agent=%s error=%s", self.agent_id, error)
                steps.append(StepRecord(
                    step=step, agent=self.agent_id, decision="decision_error",
                    error=error, duration_ms=round((time.perf_counter() - started) * 1000, 2),
                ))
                turns.append({"kind": "observation", "error": error, "step": step})
                continue

            if decision is None:
                error = "模型输出不符合结构化决策契约（需 kind=tool_call 或 final_answer）"
                logger.warning("tool_loop_invalid_decision agent=%s step=%s", self.agent_id, step)
                steps.append(StepRecord(
                    step=step, agent=self.agent_id, decision="invalid_decision",
                    error=error, duration_ms=round((time.perf_counter() - started) * 1000, 2),
                ))
                turns.append({"kind": "observation", "error": error, "step": step})
                continue

            if isinstance(decision, FinalAnswer):
                answer = decision.answer
                steps.append(StepRecord(
                    step=step, agent=self.agent_id, decision="final_answer",
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                ))
                break

            # ── Tool Call 路径 ──
            observation = self.execute(decision, step=step)
            call_id = f"call_{step}_{decision.tool}"
            turns.append({
                "kind": "tool_call",
                "id": call_id,
                "tool": decision.tool,
                "arguments": observation.arguments,
                "step": step,
            })
            turns.append({
                "kind": "observation",
                "id": call_id,
                "tool": decision.tool,
                "content": json.dumps(
                    observation.data if observation.success else {"error": observation.error},
                    ensure_ascii=False,
                    default=str,
                ),
                "degraded": observation.degraded,
                "error": observation.error,
                "step": step,
            })
            steps.append(StepRecord(
                step=step,
                agent=self.agent_id,
                decision="tool_call",
                tool=decision.tool,
                arguments=observation.arguments,
                result=observation.data,
                success=observation.success,
                degraded=observation.degraded,
                error=observation.error,
                duration_ms=observation.duration_ms,
            ))
            if observation.success:
                tool_calls += 1
                last_tool = decision.tool
            final_observation = observation.model_dump(mode="json")
        else:
            # for/else：**没有 break** = 预算耗尽且模型始终没给 FinalAnswer
            truncated = True
            fallback_reason = "max_tool_steps_exceeded"

        # ── 产出最终可交付结果 ──
        deliverable = bool(answer.strip())
        if not deliverable:
            if final_observation is not None:
                # 预算耗尽但有最后一次 Observation → 按规格交付它，并显式标 truncated
                answer = json.dumps(
                    final_observation.get("data") or final_observation,
                    ensure_ascii=False,
                    default=str,
                )
                fallback_reason = fallback_reason or "truncated_last_observation"
            else:
                error = error or "no deliverable result"
                fallback_reason = fallback_reason or "no_result"

        return ToolLoopResult(
            answer=answer,
            deliverable=deliverable,
            truncated=truncated,
            tool_steps=tool_steps,
            tool_calls=tool_calls,
            last_tool=last_tool,
            fallback_reason=fallback_reason,
            error=error,
            steps=steps,
            history=turns,
            final_observation=final_observation,
        )
