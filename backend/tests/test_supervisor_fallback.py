"""R3 契约测试：Supervisor fallback + LLM 门控 + Router 集成 + 旧入口兼容。

验收口径（R3 实施报告 §4/§5/§6）：

1. **未知请求 fallback**：无意图 / 关键词不命中 → 回落 `resource_generation`
   且 ``reason`` 以 ``fallback:`` 开头、``confidence`` 明显低于规则命中（0.9）；
2. **LLM routing 关闭时不会调用 LLM**：即便把 ``get_llm`` / ``LLMClient.complete``
   换成炸弹，决策照常产出 —— 证明结构性不依赖 LLM；
3. **开启开关也不失控**：``llm_routing=True`` 时仍回落 L1（接线点未接），
   同样不触碰 LLM；
4. **Router 集成**：resource 意图真跑通子图；未实现子图抛 ``GraphNotReadyError``
   并携带正确 ``target_graph``（＝路由正确）；
5. **消息不重复**：经路由跑出的 11.2 messages 与直跑旧入口**条数与 from 序列一致**；
6. **旧入口零回归**：``run_learning_workflow`` 返回结构不含新增的 ``routing``。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agents.supervisor import (
    CONFIDENCE_NO_SIGNAL,
    CONFIDENCE_RULE_HIT,
    CONFIDENCE_SIGNAL_FALLBACK,
    FALLBACK_GRAPH,
    SupervisorAgent,
)
from app.core import llm as llm_mod
from app.core.database import SessionLocal
from app.main import app
from app.workflows.graphs import GraphNotReadyError, run_router_workflow
from app.workflows.learning_workflow import run_learning_workflow


@pytest.fixture()
def supervisor() -> SupervisorAgent:
    return SupervisorAgent()


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


def _state(objective: str = "", kp_id: str = "nn"):
    return {
        "identity": {"userId": "u_10001", "workflowId": "wf_fb", "sessionId": None},
        "learning_context": {
            "kpId": kp_id, "kpName": "神经网络", "difficulty": "初级",
            "targetJob": "大模型应用工程师", "objective": objective,
        },
        "plan": {"maxRetries": 2},
        "execution": {"currentNode": "", "status": "pending", "retryCount": 0, "nodeLog": []},
        "evidence": {"ragContext": [], "references": []},
        "messages": [], "artifacts": {"markdown": "", "feedback": None},
        "quality": {"result": {}, "hallucinationRate": None},
        "errors": [], "result": {},
    }


def _bomb(*_args, **_kwargs):
    raise AssertionError("Supervisor 不得调用 LLM")


# ── 1. fallback ──────────────────────────────────────────────────────────────


def test_empty_objective_with_kp_falls_back(supervisor):
    decision = supervisor.decide(_state(objective=""))
    assert decision.target_graph == FALLBACK_GRAPH
    assert decision.confidence == CONFIDENCE_SIGNAL_FALLBACK
    assert decision.reason.startswith("fallback:")
    assert "objective 为空" in decision.reason


def test_unmatched_keyword_with_kp_falls_back(supervisor):
    decision = supervisor.decide(_state(objective="请给我买一张桌子"))
    assert decision.target_graph == FALLBACK_GRAPH
    assert decision.confidence == CONFIDENCE_SIGNAL_FALLBACK
    assert decision.reason.startswith("fallback:")
    assert "未识别到意图关键词" in decision.reason


def test_no_signal_gets_lowest_confidence(supervisor):
    decision = supervisor.decide(_state(objective="", kp_id=""))
    assert decision.target_graph == FALLBACK_GRAPH
    assert decision.confidence == CONFIDENCE_NO_SIGNAL
    assert decision.reason.startswith("fallback:")


def test_fallback_confidence_is_clearly_below_rule_hit(supervisor):
    assert CONFIDENCE_NO_SIGNAL < CONFIDENCE_SIGNAL_FALLBACK < CONFIDENCE_RULE_HIT
    fallback = supervisor.decide(_state(objective=""))
    rule = supervisor.decide(_state(objective="生成讲义"))
    assert fallback.confidence < rule.confidence


def test_fallback_never_raises_for_any_input(supervisor):
    """任意输入都必须给出合法决策 —— Supervisor 是入口，不能把请求挡在门外。"""
    for objective in ("", "   ", "买桌子", "asdf123", "生成", "学习路径", "评估", "聊天"):
        decision = supervisor.decide(_state(objective))
        assert decision.target_graph == FALLBACK_GRAPH or decision.confidence >= CONFIDENCE_RULE_HIT
        assert decision.reason


# ── 2. LLM 门控 ──────────────────────────────────────────────────────────────


def test_llm_routing_disabled_never_calls_llm(monkeypatch):
    """把 LLM 入口全换成炸弹 —— 关闭时决策照常产出，证明结构性不依赖 LLM。"""
    monkeypatch.setattr(llm_mod, "get_llm", _bomb)
    monkeypatch.setattr(llm_mod.LLMClient, "complete", _bomb)

    supervisor = SupervisorAgent(llm_routing=False)
    assert supervisor.llm_routing is False

    hit = supervisor.decide(_state(objective="生成讲义"))
    assert hit.target_graph == FALLBACK_GRAPH
    assert hit.confidence == CONFIDENCE_RULE_HIT

    fallback = supervisor.decide(_state(objective=""))
    assert fallback.target_graph == FALLBACK_GRAPH


def test_llm_routing_enabled_still_falls_back_without_llm_call(monkeypatch):
    """打开开关也不会失控：接线点未接 → 回落 L1，且不触碰 LLM。"""
    monkeypatch.setattr(llm_mod, "get_llm", _bomb)
    monkeypatch.setattr(llm_mod.LLMClient, "complete", _bomb)

    supervisor = SupervisorAgent(llm_routing=True)
    assert supervisor.llm_routing is True

    # L1 命中时 L2 根本不参与
    assert supervisor.decide(_state(objective="生成讲义")).target_graph == (
        FALLBACK_GRAPH
    )
    # L1 未命中 → L2 被咨询但未接线 → 仍回落 L1 fallback
    decision = supervisor.decide(_state(objective="买桌子"))
    assert decision.target_graph == FALLBACK_GRAPH
    assert decision.reason.startswith("fallback:")


def test_default_llm_routing_is_off_in_settings():
    from app.core.config import settings

    assert settings.supervisor_llm_routing is False


# ── 3. Router 集成 ───────────────────────────────────────────────────────────


def test_router_resource_generation_runs_end_to_end(db):
    result = run_router_workflow(
        db,
        user_id="u_10001",
        kp_id="nn",
        difficulty="初级",
        objective="生成神经网络的自适应讲义",
    )
    assert set(result) == {
        "workflowId", "finalOutput", "trace", "routing", "routingTrace",
    }
    assert result["workflowId"].startswith("wf_")

    routing = result["routing"]
    assert routing["target_graph"] == "resource_generation"
    assert routing["confidence"] == CONFIDENCE_RULE_HIT
    assert routing["reason"].startswith("rule[resource_generation]")

    final = result["finalOutput"]
    assert final["degraded"] is False
    assert isinstance(final["markdown"], str) and final["markdown"]

    trace = result["trace"]
    assert [a["id"] for a in trace["agents"]] == ["diagnosis", "generation", "critic"]
    assert trace["stats"]["completedAgents"] == 3
    assert trace["messages"]


def test_router_empty_objective_matches_old_default(db):
    """objective 留空 → fallback 到 resource_generation，与旧入口默认行为一致。"""
    result = run_router_workflow(
        db, user_id="u_10001", kp_id="nn", difficulty="初级", objective=""
    )
    assert result["routing"]["target_graph"] == FALLBACK_GRAPH
    assert result["routing"]["confidence"] == CONFIDENCE_SIGNAL_FALLBACK
    assert result["finalOutput"]["degraded"] is False


def test_router_pending_graph_raises_with_target(db):
    """路由到未实现子图 → 明确报错并携带 target_graph（＝路由结果可观察）。"""
    with pytest.raises(GraphNotReadyError) as excinfo:
        run_router_workflow(
            db,
            user_id="u_10001",
            kp_id="nn",
            difficulty="初级",
            objective="请给我一条学习路径",
        )
    error = excinfo.value
    assert error.target_graph == "path_planning"
    assert "尚未实现" in str(error)
    assert error.reason.startswith("rule[path_planning]")


def test_router_routes_all_four_targets(db):
    """四类意图各自到达对应分支（三个未实现的以 GraphNotReadyError 呈现）。"""
    cases = {
        "生成讲义": "resource_generation",
        "请给我一条学习路径": "path_planning",
        "评估我掌握得怎么样": "evaluation",
        "我想和你聊天": "interaction",
    }
    for objective, expected in cases.items():
        if expected == "resource_generation":
            result = run_router_workflow(
                db, user_id="u_10001", kp_id="nn", difficulty="初级", objective=objective
            )
            assert result["routing"]["target_graph"] == expected, objective
        else:
            with pytest.raises(GraphNotReadyError) as excinfo:
                run_router_workflow(
                    db, user_id="u_10001", kp_id="nn", difficulty="初级",
                    objective=objective,
                )
            assert excinfo.value.target_graph == expected, objective


# ── 4. 消息不重复（子图作为节点合并时的 reducer 风险）────────────────────────


def test_routed_messages_match_direct_run(db):
    routed = run_router_workflow(
        db, user_id="u_10001", kp_id="nn", difficulty="初级", objective="生成讲义"
    )
    direct = run_learning_workflow(
        db, user_id="u_10001", kp_id="nn", difficulty="初级"
    )
    routed_msgs, direct_msgs = routed["trace"]["messages"], direct["trace"]["messages"]
    # 条数与 from 序列必须一致 —— 经路由跑不产生重复追加
    assert len(routed_msgs) == len(direct_msgs)
    assert [m["from"] for m in routed_msgs] == [m["from"] for m in direct_msgs]
    # 节点日志同理
    assert len(routed["trace"]["nodes"]) == len(direct["trace"]["nodes"])


# ── 5. 旧入口零回归 ──────────────────────────────────────────────────────────


def test_old_entry_unchanged_by_r3(db):
    result = run_learning_workflow(
        db, user_id="u_10001", kp_id="nn", difficulty="初级"
    )
    # 返回结构未变：R3 的 routing 只存在于新入口
    assert set(result) == {"workflowId", "finalOutput", "trace"}
    assert "routing" not in result
    assert result["finalOutput"]["degraded"] is False


def test_both_entries_exist_and_are_distinct():
    from app.workflows.graphs.router import run_router_workflow as new_entry
    from app.workflows.learning_workflow import run_learning_workflow as old_entry

    assert new_entry is not old_entry
    # 新入口是旧入口的超集（多一个 objective），旧入口签名不受影响
    import inspect

    old_params = set(inspect.signature(old_entry).parameters)
    new_params = set(inspect.signature(new_entry).parameters)
    assert old_params <= new_params
    assert new_params - old_params == {"objective"}
