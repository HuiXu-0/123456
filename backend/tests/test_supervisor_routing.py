"""R3 契约测试：Supervisor 路由决策（L1 规则）+ Registry 接入。

验收口径（R3 实施报告 §3/§4）：

1. **意图 → 子图**：resource / planner / evaluation / interaction 四类意图各自命中；
2. **优先级确定**：具体意图（如"学习路径"）必须赢过宽泛关键词（如"生成"）；
3. **输出结构稳定**：``RoutingDecision`` 恰好 3 个字段、``extra=forbid``、
   confidence ∈ [0,1]；
4. **Registry 是输入而非对象**：Supervisor **不在** Registry 中，
   Registry 仍是 7 个业务 Agent（守住"不增加 Agent 数量"）；
5. **能力驱动可用子图**：Registry 为空时可用子图回落到兜底项。

本文件不跑图、不需要 DB —— 纯决策函数测试。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents import registry
from app.agents.supervisor import (
    CONFIDENCE_RULE_HIT,
    FALLBACK_GRAPH,
    TARGET_GRAPHS,
    RoutingDecision,
    SupervisorAgent,
)
from app.workflows.state import MultiAgentState

BUSINESS_AGENT_IDS = (
    "diagnosis", "generation", "critic", "planner", "dialogue", "feynman", "evaluation",
)


def _state(objective: str = "", kp_id: str = "nn") -> MultiAgentState:
    """构造一个最小但字段齐全的 MultiAgentState。"""
    return {
        "identity": {"userId": "u_10001", "workflowId": "wf_sup", "sessionId": None},
        "learning_context": {
            "kpId": kp_id,
            "kpName": "神经网络",
            "kpDescription": "核心概念",
            "difficulty": "初级",
            "targetJob": "大模型应用工程师",
            "objective": objective,
        },
        "plan": {"maxRetries": 2},
        "execution": {
            "currentNode": "", "status": "pending", "retryCount": 0, "nodeLog": [],
        },
        "evidence": {"ragContext": [], "references": []},
        "messages": [],
        "artifacts": {"markdown": "", "feedback": None},
        "quality": {"result": {}, "hallucinationRate": None},
        "errors": [],
        "result": {},
    }


@pytest.fixture()
def supervisor() -> SupervisorAgent:
    return SupervisorAgent()


# ── 1. 意图 → 子图（L1 规则）─────────────────────────────────────────────────


def test_resource_intent_routes_to_resource_generation(supervisor):
    decision = supervisor.decide(_state("生成神经网络的自适应讲义"))
    assert decision.target_graph == "resource_generation"
    assert decision.confidence == CONFIDENCE_RULE_HIT
    # R4.5.2 修正：`reason` 记录的是**命中的关键词**（「生成」），不是整句意图。
    # `_rule_route` 的语义保持 R3 原样，测试只验证「路由正确 + 理由非空且可解释」。
    assert decision.reason.startswith("rule[resource_generation]")
    assert "命中关键词" in decision.reason
    assert "生成" in decision.reason  # 实际命中的那个词
    assert decision.reason.strip()


def test_planner_intent_routes_to_path_planning(supervisor):
    decision = supervisor.decide(_state("请给我一条学习路径"))
    assert decision.target_graph == "path_planning"
    assert decision.confidence == CONFIDENCE_RULE_HIT
    assert decision.reason.startswith("rule[path_planning]")


def test_evaluation_intent_routes_to_evaluation(supervisor):
    decision = supervisor.decide(_state("评估我这个知识点掌握得怎么样"))
    assert decision.target_graph == "evaluation"
    assert decision.reason.startswith("rule[evaluation]")


def test_interaction_intent_routes_to_interaction(supervisor):
    decision = supervisor.decide(_state("我想和你聊聊天"))
    assert decision.target_graph == "interaction"
    assert decision.reason.startswith("rule[interaction]")


def test_specific_intent_beats_generic_keyword(supervisor):
    """「生成学习路径」同时命中 path_planning 与 resource_generation ——
    具体意图必须赢，否则会被宽泛的「生成」抢走。"""
    decision = supervisor.decide(_state("生成学习路径"))
    assert decision.target_graph == "path_planning"
    assert decision.confidence == CONFIDENCE_RULE_HIT


def test_evaluation_beats_interaction_on_shared_word(supervisor):
    """「评估我的对话表现」含"对话"，但"评估"更具体 → evaluation 优先。"""
    decision = supervisor.decide(_state("评估我这段对话的表现"))
    assert decision.target_graph == "evaluation"


def test_objective_is_case_insensitive(supervisor):
    assert supervisor.decide(_state("GENERATE A LECTURE")).target_graph == (
        "resource_generation"
    )


# ── 2. 输出结构稳定 ──────────────────────────────────────────────────────────


def test_routing_decision_has_exactly_three_fields(supervisor):
    decision = supervisor.decide(_state("生成讲义"))
    assert set(decision.model_dump()) == {"target_graph", "reason", "confidence"}
    assert 0.0 <= decision.confidence <= 1.0
    assert decision.reason


def test_routing_decision_rejects_extra_fields():
    with pytest.raises(ValidationError):
        RoutingDecision(
            target_graph="resource_generation", reason="x", confidence=0.9,
            source="llm",  # 不允许的第 4 个字段
        )


def test_routing_decision_rejects_out_of_range_confidence():
    with pytest.raises(ValidationError):
        RoutingDecision(target_graph="resource_generation", reason="x", confidence=1.5)
    with pytest.raises(ValidationError):
        RoutingDecision(target_graph="resource_generation", reason="x", confidence=-0.1)


def test_routing_decision_rejects_blank_reason():
    with pytest.raises(ValidationError):
        RoutingDecision(target_graph="resource_generation", reason="", confidence=0.9)


def test_target_graph_always_in_declared_set(supervisor):
    for objective in ("", "生成讲义", "学习路径", "评估", "聊天", "买一张桌子"):
        decision = supervisor.decide(_state(objective))
        assert decision.target_graph in TARGET_GRAPHS


# ── 3. Registry 接入（Supervisor 的输入，不是被登记对象）─────────────────────


def test_supervisor_is_not_registered_as_an_agent():
    """守住「不增加 Agent 数量」：Supervisor 是决策入口，不是业务 Agent。"""
    assert "supervisor" not in registry.available()


def test_registry_still_holds_exactly_seven_business_agents():
    assert registry.available() == tuple(sorted(BUSINESS_AGENT_IDS))
    assert len(registry.manifests()) == 7


def test_available_graphs_driven_by_registry_capabilities(supervisor):
    available = supervisor.available_graphs()
    # 7 个 Agent 的能力声明齐备 → 4 个子图全部可路由
    assert set(available) == set(TARGET_GRAPHS)
    # 顺序与 TARGET_GRAPHS 一致（保持确定性）
    assert available == TARGET_GRAPHS


def test_capability_report_exposes_declared_agents(supervisor):
    report = supervisor.capability_report()
    assert set(report) == {"llmRouting", "availableGraphs", "targets", "declaredAgents"}
    assert set(report["declaredAgents"]) == set(BUSINESS_AGENT_IDS)
    assert "path_planning" in report["declaredAgents"]["planner"]
    assert "rag_grounded_generation" in report["declaredAgents"]["generation"]
    assert report["llmRouting"] is False  # 默认关闭


def test_empty_registry_falls_back_to_single_graph(supervisor, monkeypatch):
    """Registry 为空 → 可用子图回落到兜底项，Supervisor 仍有确定输出。"""
    monkeypatch.setattr(registry, "manifests", lambda: [])
    assert supervisor.available_graphs() == (FALLBACK_GRAPH,)


def test_capability_less_graph_is_not_routable(supervisor, monkeypatch):
    """若 Registry 里没有任何 Agent 声明 path_planning 能力 → 该子图不可路由。"""
    manifests = [
        m for m in registry.manifests() if "path_planning" not in m["capabilities"]
    ]
    monkeypatch.setattr(registry, "manifests", lambda: manifests)
    available = supervisor.available_graphs()
    assert "path_planning" not in available
    assert FALLBACK_GRAPH in available  # 仍保留兜底子图
    # 意图命中不可用子图时，不得强行路由过去
    decision = supervisor.decide(_state("请给我一条学习路径"))
    assert decision.target_graph in available
    assert decision.target_graph != "path_planning"
    assert decision.reason.startswith("fallback:")


# ── 4. 节点适配（把决策写回 State）───────────────────────────────────────────


def test_call_writes_routing_into_plan_only(supervisor):
    update = supervisor(_state("生成讲义"))
    assert set(update) == {"plan", "execution"}
    assert update["plan"]["targetGraph"] == "resource_generation"
    assert update["plan"]["routingConfidence"] == CONFIDENCE_RULE_HIT
    assert update["plan"]["routingReason"].startswith("rule[resource_generation]")
    # ⚠️ R3.5：不得写 execution.currentNode —— 否则 _phase_step_active 落兜底分支，
    # stats.progress 提前置 100、前端显示"已完成"（见 R3.5 报告 §3 阻断项）
    assert "currentNode" not in update["execution"]
    # 路由可观测性记录（5 字段），只在进程内、不进 11.2
    assert set(update["execution"]["routingTrace"]) == {
        "input_type", "selected_graph", "reason", "confidence", "fallback",
    }
    # 不得触碰任何业务字段
    assert "messages" not in update
    assert "artifacts" not in update
    assert "quality" not in update
    assert "result" not in update
    assert "learning_context" not in update


def test_supervisor_does_not_require_db_or_llm(supervisor):
    """Supervisor 不持有 DB Session、不调用 LLM —— 只读 State 做决策。"""
    assert not hasattr(supervisor, "db")
    assert not hasattr(supervisor, "session")
    # 决策是纯函数：同一输入必得同一输出
    state = _state("生成讲义")
    first = supervisor.decide(state)
    second = supervisor.decide(state)
    assert first == second
