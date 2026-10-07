"""R4.3 契约测试：受控 Tool Loop（决策 → 白名单 → 执行 → 回灌 → 预算/fallback）。

验收口径（R4.3 实施报告 §10），逐条对应规格 §12 的 15 个覆盖点。

**决策源用注入的脚本化 provider**，因此每条用例都确定性、零网络、不依赖 LLM；
第 15 条单独验证默认 mock 决策源（``LLMClient.decide_tool``）能跑完整条链路。

零网络、零计费（conftest 强制 mock provider + 临时隔离库）。
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agents import registry as agent_registry
from app.agents.tool_loop import (
    MAX_TOOL_STEPS,
    FinalAnswer,
    ToolCall,
    ToolLoop,
    parse_decision,
)
from app.agents.tools import (
    LEARNING_PROFILE_TOOL,
    MEMORY_SEARCH_TOOL,
    RAG_TOOL_NAME,
    get_tool_registry,
)
from app.main import app
from app.workflows.nodes.tool_loop import (
    build_tool_loop_graph,
    initial_tool_loop_state,
    run_tool_loop,
)

_ACTOR = "u_r43_toolloop"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # 触发 lifespan：建表 + 幂等种子
        yield c


def scripted(*decisions: dict[str, Any]):
    """按顺序吐出决策的 provider；队列耗尽 → 收敛作答（防止越界）。

    用它即可精确控制「模型第 N 步决定做什么」，无需真 LLM。
    """
    queue = list(decisions)

    def provider(**_kwargs: Any) -> dict[str, Any]:
        return queue.pop(0) if queue else {"kind": "final_answer", "answer": "队列耗尽"}

    return provider


def _call(tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"kind": "tool_call", "tool": tool, "arguments": arguments or {}}


def _final(answer: str = "完成") -> dict[str, Any]:
    return {"kind": "final_answer", "answer": answer}


# ── 1 / 2. generation、critic 可以触发 Tool ──────────────────────────────────


def test_generation_can_trigger_tool(client):
    loop = ToolLoop(
        agent_id="generation",
        actor_id=_ACTOR,
        decision_provider=scripted(_call(MEMORY_SEARCH_TOOL), _final("已查到画像")),
    )
    assert MEMORY_SEARCH_TOOL in loop.allowed_names  # R4.2 声明的权限生效

    result = loop.run("查我的学习画像")
    assert result.tool_calls == 1
    assert result.last_tool == MEMORY_SEARCH_TOOL
    assert result.answer == "已查到画像"
    assert result.deliverable is True
    assert result.truncated is False
    assert result.error is None


def test_critic_can_trigger_tool(client):
    loop = ToolLoop(
        agent_id="critic",
        actor_id=_ACTOR,
        decision_provider=scripted(_call(LEARNING_PROFILE_TOOL), _final("校验完毕")),
    )
    assert LEARNING_PROFILE_TOOL in loop.allowed_names

    result = loop.run("复核讲义前先看画像")
    assert result.tool_calls == 1
    assert result.last_tool == LEARNING_PROFILE_TOOL
    assert result.answer == "校验完毕"


# ── 3. 未授权 Agent 无法调用 Tool ────────────────────────────────────────────


def test_unauthorized_agent_cannot_call_tool(client):
    """planner 的 ``BaseAgent.tools`` 为空 → 声明层直接拒绝，工具不会被执行。"""
    assert agent_registry.tools_of("planner") == ()
    loop = ToolLoop(
        agent_id="planner",
        actor_id=_ACTOR,
        decision_provider=scripted(_call(MEMORY_SEARCH_TOOL), _final("兜底")),
    )
    assert loop.permitted == frozenset()
    assert loop.allowed_names == ()

    result = loop.run("给 planner 发一个 memory/search")
    assert result.tool_calls == 0  # **一次都没执行**
    rejected = [s for s in result.steps if s.decision == "tool_call"]
    assert rejected and "未授权" in (rejected[0].error or "")


def test_unknown_agent_gets_no_permission(client):
    """未注册的 Agent id → 权限为空（默认拒绝），不抛异常。"""
    loop = ToolLoop(agent_id="not-a-real-agent", actor_id=_ACTOR)
    assert loop.permitted == frozenset()
    assert loop.allowed_specs == ()


# ── 4. 未注册 Tool 被拒绝 ────────────────────────────────────────────────────


def test_unregistered_tool_is_rejected(client, monkeypatch):
    """权限里有、但 ToolRegistry 没注册 → KeyError → 拒绝执行。"""
    monkeypatch.setattr(agent_registry, "tools_of", lambda _aid: ("ghost/tool",))
    loop = ToolLoop(
        agent_id="generation",
        actor_id=_ACTOR,
        decision_provider=scripted(_call("ghost/tool"), _final()),
    )
    assert "ghost/tool" in loop.permitted
    assert "ghost/tool" not in loop.allowed_names  # 声明有但未注册

    result = loop.run("调一个不存在的工具")
    assert result.tool_calls == 0
    rejected = [s for s in result.steps if s.decision == "tool_call"]
    assert rejected and "未在 ToolRegistry 注册" in (rejected[0].error or "")


def test_whitelist_is_intersection_of_declared_and_registered(client):
    """可用工具 = ``BaseAgent.tools`` ∩ 已注册 Spec（两处都过才可用）。"""
    loop = ToolLoop(agent_id="generation", actor_id=_ACTOR)
    declared = set(agent_registry.tools_of("generation"))
    registered = set(get_tool_registry().available_tools)
    assert set(loop.allowed_names) <= declared
    assert set(loop.allowed_names) <= registered
    # 规格 §7：generation 也可访问既有 trusted_rag
    assert RAG_TOOL_NAME in loop.allowed_names
    # 未声明的工具即便已注册也不可用
    assert "learning/path" not in set(agent_registry.tools_of("critic"))


# ── 5. 非法 arguments 被拒绝 ─────────────────────────────────────────────────


def test_invalid_arguments_are_rejected(client):
    """limit 越界 → args_schema 校验失败 → Observation(success=False)，不执行。"""
    loop = ToolLoop(
        agent_id="generation",
        actor_id=_ACTOR,
        decision_provider=scripted(_call(MEMORY_SEARCH_TOOL, {"limit": 99999}), _final()),
    )
    result = loop.run("越界调用")
    assert result.tool_calls == 0
    failed = [s for s in result.steps if s.decision == "tool_call"]
    assert failed and not failed[0].success
    assert failed[0].error  # 错误反馈给模型，且 Loop 未崩


def test_unknown_argument_is_rejected(client):
    loop = ToolLoop(
        agent_id="generation",
        actor_id=_ACTOR,
        decision_provider=scripted(
            _call(MEMORY_SEARCH_TOOL, {"drop_tables": True}), _final()
        ),
    )
    result = loop.run("注入字段")
    assert result.tool_calls == 0


# ── 6. actor_id / user_id 越权被拒绝 ─────────────────────────────────────────


def test_cross_user_tool_call_is_rejected(client):
    """actor_id=A、args.user_id=B → ToolPermissionError，数据不外泄。"""
    loop = ToolLoop(
        agent_id="generation",
        actor_id="u_r43_alice",
        decision_provider=scripted(
            _call(MEMORY_SEARCH_TOOL, {"user_id": "u_r43_bob"}), _final()
        ),
    )
    result = loop.run("读别人的记忆")
    assert result.tool_calls == 0
    failed = [s for s in result.steps if s.decision == "tool_call"]
    assert failed and "越权" in (failed[0].error or "")


def test_runtime_injects_actor_id_when_model_omits_user_id(client):
    """模型没给 user_id → Runtime 用 actor_id 注入（不是漏参报错）。"""
    seen: list[dict[str, Any]] = []

    def provider(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs)
        return _call(MEMORY_SEARCH_TOOL) if not seen[1:] else _final()

    loop = ToolLoop(agent_id="generation", actor_id=_ACTOR, decision_provider=provider)
    result = loop.run("查画像")
    assert result.tool_calls == 1
    observation = [s for s in result.steps if s.decision == "tool_call"][0]
    assert observation.arguments.get("user_id") == _ACTOR  # 由 Runtime 注入


# ── 7. Tool result 可以正确回灌 ──────────────────────────────────────────────


def test_observation_is_fed_back_to_next_decision(client):
    captured: list[list[dict[str, Any]]] = []

    def provider(**kwargs: Any) -> dict[str, Any]:
        captured.append(list(kwargs.get("history") or []))
        return _call(MEMORY_SEARCH_TOOL) if len(captured) == 1 else _final("ok")

    loop = ToolLoop(agent_id="generation", actor_id=_ACTOR, decision_provider=provider)
    result = loop.run("查画像")

    assert len(captured) >= 2
    first_history = captured[0]
    assert not any(i.get("kind") == "observation" for i in first_history)  # 首轮尚无观察

    second_history = captured[1]
    tool_calls = [i for i in second_history if i.get("kind") == "tool_call"]
    observations = [i for i in second_history if i.get("kind") == "observation"]
    assert tool_calls and tool_calls[0]["tool"] == MEMORY_SEARCH_TOOL
    assert observations and observations[0]["content"]  # 结果确实回灌了
    assert "degraded" in observations[0]

    # 调用方拿到的 history 同样含这两类记录
    assert any(i.get("kind") == "tool_call" for i in result.history)
    assert any(i.get("kind") == "observation" for i in result.history)


def test_observation_never_leaks_db_objects(client):
    """回灌给模型的内容必须是纯 JSON（无 Session / ORM）。"""
    loop = ToolLoop(
        agent_id="generation",
        actor_id=_ACTOR,
        decision_provider=scripted(_call(MEMORY_SEARCH_TOOL), _final()),
    )
    result = loop.run("查画像")
    observation = next(i for i in result.history if i.get("kind") == "observation")
    parsed = json.loads(observation["content"])
    assert not any(
        hasattr(v, "_sa_instance_mapper")
        for v in parsed.values()
        if not isinstance(v, (str, int, float, bool, type(None)))
    )


# ── 8. 模型可以继续第二次 Tool Call ──────────────────────────────────────────


def test_second_tool_call_is_allowed(client):
    loop = ToolLoop(
        agent_id="generation",
        actor_id=_ACTOR,
        decision_provider=scripted(
            _call(MEMORY_SEARCH_TOOL),
            _call(LEARNING_PROFILE_TOOL),
            _final("两次都查完了"),
        ),
    )
    result = loop.run("先查记忆再查画像")
    assert result.tool_calls == 2
    assert [s.tool for s in result.steps if s.decision == "tool_call"] == [
        MEMORY_SEARCH_TOOL,
        LEARNING_PROFILE_TOOL,
    ]
    assert result.answer == "两次都查完了"


# ── 9 / 10. MAX_TOOL_STEPS=3 与 truncated ────────────────────────────────────


def test_max_tool_steps_is_three():
    assert MAX_TOOL_STEPS == 3


def test_loop_stops_at_max_steps(client):
    """决策源一直想调工具 → **最多 3 步就停**，不会跑到第 4 次。"""
    loop = ToolLoop(
        agent_id="generation",
        actor_id=_ACTOR,
        decision_provider=lambda **_k: _call(MEMORY_SEARCH_TOOL),
    )
    result = loop.run("永远想调工具")
    assert result.tool_steps == 3  # 预算用满即止
    assert len([s for s in result.steps if s.decision == "tool_call"]) == 3


def test_budget_exhausted_returns_truncated_with_last_observation(client):
    """超过 3 步 → 不抛异常，返回 ``truncated=True`` + 最后一次 Observation。"""
    loop = ToolLoop(
        agent_id="generation",
        actor_id=_ACTOR,
        decision_provider=lambda **_k: _call(MEMORY_SEARCH_TOOL),
    )
    result = loop.run("预算耗尽")

    assert result.truncated is True
    assert result.fallback_reason == "max_tool_steps_exceeded"
    assert result.tool_steps == MAX_TOOL_STEPS
    assert result.last_tool == MEMORY_SEARCH_TOOL
    assert result.final_observation is not None
    # 规格 C：交付最后一次 Observation，而不是抛异常
    assert result.answer
    assert isinstance(result.answer, str)


def test_result_is_json_serializable(client):
    loop = ToolLoop(
        agent_id="generation",
        actor_id=_ACTOR,
        decision_provider=lambda **_k: _call(MEMORY_SEARCH_TOOL),
    )
    payload = loop.run("x").model_dump(mode="json")
    text = json.dumps(payload, ensure_ascii=False, default=str)
    assert json.loads(text)["truncated"] is True


# ── 11. Tool Error 不导致 Loop 崩溃 ──────────────────────────────────────────


def test_tool_runtime_error_does_not_crash_loop(client, monkeypatch):
    registry = get_tool_registry()

    def _boom(*_a, **_k):
        raise RuntimeError("向量库炸了")

    monkeypatch.setattr(registry, "execute", _boom)
    loop = ToolLoop(
        agent_id="generation",
        actor_id=_ACTOR,
        decision_provider=scripted(_call(MEMORY_SEARCH_TOOL), _final("出错也能答")),
    )
    result = loop.run("工具会抛错")  # **不抛**
    assert result.answer == "出错也能答"
    failed = [s for s in result.steps if s.decision == "tool_call"]
    assert failed and not failed[0].success
    # R4.5.2：以 R4.3 **实际数据模型**为准 —— StepRecord 携带 `error`（文本），
    # `error_type` 属于 Observation，StepRecord 上并无该字段。
    # 不为迁就测试往 StepRecord 塞重复字段；本用例验证的是
    # 「runtime error 不崩 Loop + 错误信息可查 + 按契约继续收敛」。
    assert "向量库炸了" in (failed[0].error or "")
    assert failed[0].duration_ms >= 0
    # 循环继续到下一轮并正常收敛（未达预算上限）
    assert result.deliverable is True
    assert result.truncated is False
    assert result.tool_calls == 0  # 失败的那次不计入成功调用


def test_decision_source_error_does_not_crash_loop():
    """决策源抛错 → 回喂并继续，不崩 Loop。"""
    def _boom(**_kwargs):
        raise RuntimeError("LLM 挂了")

    loop = ToolLoop(agent_id="generation", actor_id=_ACTOR, decision_provider=_boom)
    result = loop.run("决策源会挂")
    assert result.tool_calls == 0
    assert any(s.decision == "decision_error" for s in result.steps)
    # 预算耗尽后交付 fallback，而非抛异常
    assert result.truncated is True
    assert result.fallback_reason == "max_tool_steps_exceeded"


# ── 12. degraded Tool Result 能传回模型 ──────────────────────────────────────


def test_degraded_observation_is_fed_back(client, monkeypatch):
    registry = get_tool_registry()
    monkeypatch.setattr(
        registry,
        "execute",
        lambda *_a, **_k: {"tool": MEMORY_SEARCH_TOOL, "mock": True, "degraded": True, "count": 2},
    )
    captured: list[list[dict[str, Any]]] = []

    def provider(**kwargs: Any) -> dict[str, Any]:
        captured.append(list(kwargs.get("history") or []))
        return _call(MEMORY_SEARCH_TOOL) if len(captured) == 1 else _final()

    loop = ToolLoop(agent_id="generation", actor_id=_ACTOR, decision_provider=provider)
    loop.run("拿降级数据")

    observations = [i for i in captured[-1] if i.get("kind") == "observation"]
    assert observations and observations[-1]["degraded"] is True  # 模型知道这是降级数据


# ── 13. Final Answer 正常结束 Loop ───────────────────────────────────────────


def test_final_answer_ends_loop_without_tool_execution(client):
    loop = ToolLoop(
        agent_id="generation",
        actor_id=_ACTOR,
        decision_provider=scripted(_final("不用工具的答案")),
    )
    result = loop.run("不需要工具")
    assert result.answer == "不用工具的答案"
    assert result.deliverable is True
    assert result.tool_calls == 0
    assert result.truncated is False
    assert result.fallback_reason is None


def test_empty_allowed_tools_immediately_finalizes(client):
    """无任何授权工具 → 决策源只能给 final_answer，不进执行。"""
    loop = ToolLoop(
        agent_id="planner",
        actor_id=_ACTOR,
        decision_provider=scripted(_final("无工具可调")),
    )
    result = loop.run("x")
    assert result.answer == "无工具可调"
    assert result.tool_calls == 0


# ── 14. 没有 Tool Call 时不进入 Tool Execution Node（图级）───────────────────


def test_graph_skips_execution_node_when_no_tool_call(client):
    """图驱动：只有 final_answer → ``tool_execute`` 节点**根本不被访问**。"""
    loop = ToolLoop(
        agent_id="generation",
        actor_id=_ACTOR,
        decision_provider=scripted(_final("直接作答")),
    )
    graph = build_tool_loop_graph(loop)
    visited: list[str] = []
    for update in graph.stream(
        initial_tool_loop_state(actor_id=_ACTOR, task="不需要工具"),
        stream_mode="updates",
    ):
        visited.extend(update.keys())

    assert "tool_execute" not in visited  # ← 规格 §12-14 的核心断言
    assert "agent_decision" in visited and "final" in visited


def test_graph_enters_execution_node_on_tool_call(client):
    loop = ToolLoop(
        agent_id="generation",
        actor_id=_ACTOR,
        decision_provider=scripted(_call(MEMORY_SEARCH_TOOL), _final("ok")),
    )
    graph = build_tool_loop_graph(loop)
    visited: list[str] = []
    for update in graph.stream(
        initial_tool_loop_state(actor_id=_ACTOR, task="需要工具"), stream_mode="updates"
    ):
        visited.extend(update.keys())

    assert visited.count("tool_execute") == 1
    assert visited.count("agent_decision") == 2  # 决策 → 执行 → 再决策
    assert "final" in visited


def test_graph_budget_exhaustion_is_bounded(client):
    """图路径同样受 3 步限制：``tool_step`` 不会超过 MAX_TOOL_STEPS。"""
    loop = ToolLoop(
        agent_id="generation",
        actor_id=_ACTOR,
        decision_provider=lambda **_k: _call(MEMORY_SEARCH_TOOL),
    )
    graph = build_tool_loop_graph(loop)
    final_state = graph.invoke(initial_tool_loop_state(actor_id=_ACTOR, task="无限调用"))

    assert final_state["tool_step"] <= MAX_TOOL_STEPS
    assert final_state["result"]["truncated"] is True
    assert final_state["result"]["fallbackReason"] == "max_tool_steps_exceeded"
    assert len(final_state["tool_calls"]) == 3


# ── 15. mock 模式可以完整执行 ────────────────────────────────────────────────


def test_mock_decision_source_runs_full_loop(client):
    """不注入 provider → 走 ``LLMClient.decide_tool`` 的 mock 分支，完整跑通。"""
    loop = ToolLoop(agent_id="generation", actor_id=_ACTOR)  # 默认决策源
    result = loop.run("帮我规划 Transformer 学习路径")

    assert result.tool_steps == MAX_TOOL_STEPS  # mock 依次调用未用过的工具
    assert result.tool_calls == MAX_TOOL_STEPS
    assert result.last_tool in loop.allowed_names
    assert result.truncated is True  # 3 步内没给出 final_answer → 按规格降级
    assert result.final_observation is not None
    json.dumps(result.model_dump(mode="json"), ensure_ascii=False, default=str)


def test_mock_decision_source_converges_when_budget_exceeds_tool_count(client):
    """预算(3) > 授权工具数时，mock 用尽工具后会给出 ``final_answer`` 收敛。"""
    loop = ToolLoop(
        agent_id="critic",
        actor_id=_ACTOR,
        max_steps=MAX_TOOL_STEPS + 2,  # 预算 5 > critic 的 4 个工具
    )
    assert len(loop.allowed_names) == 4  # 规格 §7 的 critic 权限集

    result = loop.run("复核讲义")
    assert result.deliverable is True
    assert result.truncated is False
    assert "mock" in result.answer  # mock 明确标出是模拟产出
    assert result.tool_calls == 4  # 4 个工具各调一次后收敛


def test_graph_and_imperative_paths_agree(client):
    """图驱动与命令式共用同一个 ToolLoop → 同输入同结果。"""
    decisions = (_call(MEMORY_SEARCH_TOOL), _final("两条路径一致"))
    imperative = ToolLoop(
        agent_id="generation", actor_id=_ACTOR, decision_provider=scripted(*decisions)
    ).run("同任务")
    graph_result = run_tool_loop(
        agent_id="generation",
        actor_id=_ACTOR,
        task="同任务",
        decision_provider=scripted(*decisions),
    )
    assert graph_result.answer == imperative.answer
    assert graph_result.tool_calls == imperative.tool_calls
    assert graph_result.truncated == imperative.truncated
    assert set(graph_result.model_dump()) == set(imperative.model_dump())


# ── 决策契约（结构化，不猜自然语言）──────────────────────────────────────────


def test_parse_decision_accepts_only_structured_paths():
    assert isinstance(parse_decision(_call("memory/search")), ToolCall)
    assert isinstance(parse_decision(_final("ok")), FinalAnswer)
    assert isinstance(parse_decision('{"kind":"final_answer","answer":"ok"}'), FinalAnswer)


def test_parse_decision_rejects_prose_and_malformed():
    """散文 / 缺字段 / 未知 kind 一律拒绝 —— **不把"想调工具"当调用**。"""
    assert parse_decision("我觉得应该查一下记忆") is None
    assert parse_decision('{"kind":"tool_call"}') is None  # 缺 tool
    assert parse_decision('{"kind":"tool_call","tool":""}') is None
    assert parse_decision('{"kind":"run_python","code":"..."}') is None
    assert parse_decision('{"kind":"final_answer"}') is None  # 缺 answer
    assert parse_decision(None) is None
    assert parse_decision(123) is None


def test_invalid_decision_consumes_budget_but_keeps_running(client):
    """非法决策：拒绝 + 记 error + 回喂，且消耗一步（有界），Loop 不崩。"""
    loop = ToolLoop(
        agent_id="generation",
        actor_id=_ACTOR,
        decision_provider=scripted(
            "这不是结构化决策",
            _final("第二次才答对"),
        ),
    )
    result = loop.run("先非法再合法")
    assert any(s.decision == "invalid_decision" for s in result.steps)
    assert result.answer == "第二次才答对"
    assert result.tool_calls == 0
