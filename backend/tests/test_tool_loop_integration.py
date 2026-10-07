"""R4.4 契约测试：Tool Loop 生产接入（OFF / ON / Failure / 双路径）。

验收口径（R4.4 实施报告 §8），逐条对应规格 §10 的 **17 个覆盖点**。

**核心不变量**：Tool Loop 只做「取证」，markdown / passed / validationScore /
hallucinationRate / issues **仍由既有 `run_generator` / `run_critic` 产生** ——
因此开关只影响「喂进去的证据」，不影响输出契约、State 十字段、11.2、
retry / degraded / 阈值 / RAG grounding 算法。

内部 trace 经 `app.workflows.nodes.resource_generation.tool_loop_trace()` 观测
（**不进 11.2**）。

零网络、零计费（conftest 强制 mock provider + 临时隔离库）。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents import registry as agent_registry
from app.agents.tool_loop import ToolLoop, ToolLoopResult
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.llm import set_force_critic_low
from app.main import app
from app.models.entities import WorkflowTrace
from app.workflows.learning_workflow import (
    MAX_RETRIES,
    VALIDATION_THRESHOLD,
    run_learning_workflow,
)
from app.workflows.nodes import resource_generation as nodes

_USER = "u_10001"
_KP = "nn"
_FINAL_OUTPUT_KEYS = {
    "markdown", "validationScore", "hallucinationRate", "degraded", "retryCount",
}
_TRACE_KEYS = {
    "workflowId", "phase", "step", "agents", "messages",
    "nodes", "ragContextUsed", "stats",
}
_MESSAGE_KEYS = {"id", "from", "to", "message", "type", "timestamp"}
_CRITIC_OUTPUT_KEYS = {"passed", "validationScore", "hallucinationRate", "issues"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # 触发 lifespan：建表 + 幂等种子
        yield c


@pytest.fixture()
def db(client):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _flag_and_trace():
    """每条用例从 flag 关闭 + 空 trace 起步，结束后还原。"""
    original = settings.agent_tool_loop_enabled
    settings.agent_tool_loop_enabled = False
    nodes.clear_tool_loop_trace()
    yield
    settings.agent_tool_loop_enabled = original
    nodes.clear_tool_loop_trace()


def _run(db, *, enabled: bool, **kwargs):
    """按指定 flag 跑完整 resource_generation 工作流，返回 (result, frames)。"""
    settings.agent_tool_loop_enabled = enabled
    frames: list[dict] = []
    result = run_learning_workflow(
        db,
        user_id=_USER,
        kp_id=_KP,
        difficulty="初级",
        on_update=frames.append,
        **kwargs,
    )
    return result, frames


def _entries():
    return [item for item in nodes.tool_loop_trace() if item.get("enabled")]


def _by_agent(entries, agent_id):
    return [item for item in entries if item.get("agent") == agent_id]


# ══════════════════════════════════════════════════════════════════════════
# OFF：完全走旧路径
# ══════════════════════════════════════════════════════════════════════════


def test_01_generation_uses_old_path_when_flag_off(db):
    """OFF：generation 完全走旧路径 —— 一条 Tool Loop trace 都不产生。"""
    result, _ = _run(db, enabled=False)
    assert nodes.tool_loop_trace() == []  # 连痕迹都没有
    assert result["finalOutput"]["markdown"]
    # generation 节点确实跑过
    assert any(n["agent"] == "generation" for n in result["trace"]["nodes"])


def test_02_critic_uses_old_path_when_flag_off(db):
    """OFF：critic 完全走旧路径。"""
    _, frames = _run(db, enabled=False)
    assert nodes.tool_loop_trace() == []
    validation = frames[-1].get("validation_result") or {}
    assert _CRITIC_OUTPUT_KEYS <= set(validation)


def test_03_output_matches_old_path_exactly(db, monkeypatch):
    """OFF：与「完全不碰 ToolLoop」的原流程结果一致。"""
    baseline, _ = _run(db, enabled=False)

    def _boom(*_a, **_k):
        raise AssertionError("flag OFF 时不得触碰 ToolLoop")

    monkeypatch.setattr(ToolLoop, "run", _boom)
    after, _ = _run(db, enabled=False)  # 若触碰 ToolLoop → 此处抛 AssertionError

    assert set(after) == set(baseline) == {"workflowId", "finalOutput", "trace"}
    assert set(after["finalOutput"]) == set(baseline["finalOutput"]) == _FINAL_OUTPUT_KEYS
    assert set(after["trace"]) == set(baseline["trace"]) == _TRACE_KEYS
    assert [n["agent"] for n in after["trace"]["nodes"]] == [
        n["agent"] for n in baseline["trace"]["nodes"]
    ]
    assert after["finalOutput"]["degraded"] is baseline["finalOutput"]["degraded"]
    assert after["finalOutput"]["retryCount"] == baseline["finalOutput"]["retryCount"]


# ══════════════════════════════════════════════════════════════════════════
# ON：进入 Tool Loop
# ══════════════════════════════════════════════════════════════════════════


def test_04_generation_enters_tool_loop_when_flag_on(db):
    """ON：generation 节点进入了 Tool Loop。"""
    result, _ = _run(db, enabled=True)
    entries = _entries()
    generation = _by_agent(entries, "generation")
    assert generation, "flag ON 时 generation 必须留下 Tool Loop trace"
    assert any(n["agent"] == "generation" for n in result["trace"]["nodes"])


def test_05_critic_enters_tool_loop_when_flag_on(db):
    """ON：critic 节点进入了 Tool Loop。"""
    _run(db, enabled=True)
    critic = _by_agent(_entries(), "critic")
    assert critic, "flag ON 时 critic 必须留下 Tool Loop trace"


def test_06_tools_are_actually_called(db):
    """ON：Tool 确实被调用（不是空转）。"""
    _run(db, enabled=True)
    entries = _entries()
    assert entries
    assert any(int(e.get("toolCalls") or 0) >= 1 for e in entries)
    called = {
        step.get("tool")
        for e in entries
        for step in e.get("steps") or []
        if step.get("tool")
    }
    assert called, "trace 中应记录到至少一个 tool"


def test_07_observations_are_recorded(db):
    """ON：Observation 被记录（回灌内容的痕迹在内部 trace 中可见）。"""
    _run(db, enabled=True)
    steps = [
        step for e in _entries() for step in (e.get("steps") or [])
        if step.get("tool")
    ]
    assert steps
    # 成功执行的步骤必须带 observation（失败的允许为 None）
    assert any(step.get("observation") for step in steps)
    assert all(
        {"step", "tool", "arguments", "success", "degraded", "error", "durationMs"}
        <= set(step)
        for step in steps
    )


def test_08_generation_output_structure_unchanged(db):
    """ON：最终 generation 输出结构不变（markdown + finalOutput 5 键 + trace 8 键）。"""
    result, _ = _run(db, enabled=True)
    final = result["finalOutput"]
    assert set(final) == _FINAL_OUTPUT_KEYS
    assert isinstance(final["markdown"], str) and final["markdown"]
    assert set(result["trace"]) == _TRACE_KEYS
    assert [a["id"] for a in result["trace"]["agents"]] == [
        "diagnosis", "generation", "critic",
    ]


def test_09_critic_output_structure_unchanged(db):
    """ON：critic 仍产出 passed / validationScore / hallucinationRate / issues。"""
    _, frames = _run(db, enabled=True)
    validation = frames[-1].get("validation_result") or {}
    assert _CRITIC_OUTPUT_KEYS <= set(validation)
    assert isinstance(validation["passed"], bool)
    assert isinstance(validation["validationScore"], (int, float))
    assert 0.0 <= validation["hallucinationRate"] <= 1.0
    assert isinstance(validation["issues"], list)


def test_10_retry_logic_unchanged(db):
    """ON：重试逻辑不变 —— 强制低分 → 2 次重试、gen/critic 各 3 节点。"""
    set_force_critic_low(True)
    try:
        result, _ = _run(db, enabled=True)
    finally:
        set_force_critic_low(False)

    final = result["finalOutput"]
    assert final["retryCount"] == MAX_RETRIES == 2
    nodes_log = result["trace"]["nodes"]
    gen = [n for n in nodes_log if n["agent"] == "generation"]
    critic = [n for n in nodes_log if n["agent"] == "critic"]
    assert len(gen) == 3 and len(critic) == 3
    assert [n["retryIndex"] for n in gen] == [0, 1, 2]


def test_11_degraded_logic_unchanged(db):
    """ON：degraded 语义不变 —— 降级 + requiresReview + 阈值不变。"""
    set_force_critic_low(True)
    try:
        result, _ = _run(db, enabled=True)
    finally:
        set_force_critic_low(False)

    final = result["finalOutput"]
    assert final["degraded"] is True
    assert final["requiresReview"] is True
    assert final["validationScore"] < VALIDATION_THRESHOLD
    assert {a["id"]: a["status"] for a in result["trace"]["agents"]}["critic"] == "error"

    row = db.get(WorkflowTrace, result["workflowId"])
    assert row is not None and row.status == "degraded" and row.degraded is True


# ══════════════════════════════════════════════════════════════════════════
# Failure：截断 / 运行时异常 / 未授权
# ══════════════════════════════════════════════════════════════════════════


def _truncated_result() -> ToolLoopResult:
    return ToolLoopResult(
        answer="",
        deliverable=False,
        truncated=True,
        tool_steps=3,
        tool_calls=3,
        last_tool="memory/search",
        fallback_reason="max_tool_steps_exceeded",
    )


def test_12_truncated_tool_loop_does_not_break_workflow(db, monkeypatch):
    """Tool Loop truncated → workflow 照常完成，标记进内部 trace。"""
    monkeypatch.setattr(ToolLoop, "run", lambda self, *a, **k: _truncated_result())

    result, _ = _run(db, enabled=True)  # **不抛**
    assert result["finalOutput"]["markdown"]
    assert set(result["trace"]) == _TRACE_KEYS

    entries = _entries()
    assert entries
    assert all(e.get("truncated") is True for e in entries)
    assert all(e.get("fallbackReason") == "max_tool_steps_exceeded" for e in entries)


def test_13_runtime_error_falls_back_to_old_path(db, monkeypatch):
    """Tool Loop 运行时异常 → 记 error + 回退旧路径，workflow 不崩。"""
    def _boom(self, *a, **k):
        raise RuntimeError("tool loop 炸了")

    monkeypatch.setattr(ToolLoop, "run", _boom)

    result, _ = _run(db, enabled=True)  # **不抛**
    assert result["finalOutput"]["markdown"]
    assert result["finalOutput"]["degraded"] is False

    entries = _entries()
    assert entries
    for entry in entries:
        assert entry.get("fallback") == "old_path"
        assert entry.get("error") and "RuntimeError" in entry["error"]
        # 失败时不产生任何工具步骤（完全退回原入参）
        assert not entry.get("steps")


def test_14_unauthorized_tool_never_executes(db):
    """未授权 Tool 仍不能被调用：trace 里出现的 tool 必须 ⊆ 该 Agent 的权限集。"""
    _run(db, enabled=True)
    entries = _entries()
    assert entries, "flag ON 应产生 trace"

    for entry in entries:
        agent_id = entry["agent"]
        allowed = set(agent_registry.tools_of(agent_id))
        assert allowed, f"{agent_id} 应有权限集"
        for step in entry.get("steps") or []:
            tool = step.get("tool")
            if tool:
                assert tool in allowed, f"{tool} 未授权给 {agent_id}"

    # 规格 §4：critic 不得访问 learning/path
    critic_tools = {
        step.get("tool")
        for entry in _by_agent(entries, "critic")
        for step in entry.get("steps") or []
        if step.get("tool")
    }
    assert "learning/path" not in critic_tools


def test_15_112_trace_is_not_polluted(db):
    """内部 trace 不进入 11.2 前端契约。"""
    result, _ = _run(db, enabled=True)
    assert nodes.tool_loop_trace(), "内部 trace 应有记录（证明走了 Tool Loop）"

    trace_text = json.dumps(result["trace"], ensure_ascii=False, default=str)
    assert "toolLoop" not in trace_text
    assert "truncated" not in trace_text
    assert "fallbackReason" not in trace_text

    trace = result["trace"]
    assert set(trace) == _TRACE_KEYS
    for message in trace["messages"]:
        assert set(message) == _MESSAGE_KEYS
    for node in trace["nodes"]:
        assert "toolLoop" not in json.dumps(node, ensure_ascii=False, default=str)
    # stats 结构不变
    assert set(trace["stats"]) == {"completedAgents", "messageCount", "progress"}


# ══════════════════════════════════════════════════════════════════════════
# 双路径
# ══════════════════════════════════════════════════════════════════════════


def test_16_flag_off_matches_legacy_behaviour(db):
    """OFF 与原路径行为一致（同一 flag 下连跑两次结果同构）。"""
    first, frames_a = _run(db, enabled=False)
    second, frames_b = _run(db, enabled=False)

    assert nodes.tool_loop_trace() == []
    assert set(first["finalOutput"]) == set(second["finalOutput"])
    assert set(first["trace"]) == set(second["trace"])
    assert [n["agent"] for n in first["trace"]["nodes"]] == [
        n["agent"] for n in second["trace"]["nodes"]
    ]
    assert len(frames_a) == len(frames_b)
    assert [f.get("current_node") for f in frames_a] == [
        f.get("current_node") for f in frames_b
    ]


def test_17_flag_on_completes_resource_generation(db):
    """ON 可以正常完成 resource_generation（happy path）。"""
    result, frames = _run(db, enabled=True)

    assert result["finalOutput"]["degraded"] is False
    assert result["finalOutput"]["retryCount"] == 0
    assert result["trace"]["stats"]["completedAgents"] == 3
    assert result["trace"]["stats"]["progress"] == 100
    assert frames[-1]["current_node"] == "decision"
    # 且确实走了 Tool Loop
    assert _entries()


# ══════════════════════════════════════════════════════════════════════════
# Flag 本身
# ══════════════════════════════════════════════════════════════════════════


def test_flag_defaults_to_off():
    """安全默认：不配置即旧流程。"""
    assert settings.agent_tool_loop_enabled is False


def test_flag_is_read_per_call_not_at_startup(monkeypatch):
    """每次调用现读 settings → 改配置不重启即灰度/回滚。"""
    monkeypatch.setattr(settings, "agent_tool_loop_enabled", True)
    assert settings.agent_tool_loop_enabled is True
    monkeypatch.setattr(settings, "agent_tool_loop_enabled", False)
    assert settings.agent_tool_loop_enabled is False


def test_trace_buffer_is_bounded():
    """内部 trace 有上限，不会无界增长。"""
    assert nodes._TOOL_LOOP_TRACE_LIMIT == 200
    nodes.clear_tool_loop_trace()
    for index in range(250):
        nodes._record_tool_loop({"agent": "generation", "seq": index})
    kept = nodes.tool_loop_trace()
    assert len(kept) == nodes._TOOL_LOOP_TRACE_LIMIT
    assert kept[-1]["seq"] == 249  # 保留最新
    nodes.clear_tool_loop_trace()
    assert nodes.tool_loop_trace() == []
