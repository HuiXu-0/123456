"""R2 契约测试：工作流外部契约与迁移前逐字一致（parity）。

验收口径（R2 实施报告 §6/§7）：

1. **签名与返回**：``run_learning_workflow`` 返回 ``{workflowId, finalOutput, trace}``，
   ``finalOutput`` 与 ``trace`` 的字段集合与迁移前一致；
2. **11.2 messages 文案**：内容与迁移前手工 ``_msg()`` 逐字一致 —— 投影后
   **前端完全无感**；
3. **runner 零改动**：把投影结果直接喂给 ``workflow_runner._snapshot_from_state``
   （迁移前的消费者），必须正常产出 11.2 快照 —— 这是最硬的等价证明；
4. **导出面冻结**：``workflow_runner`` / ``resource`` / ``regen_lectures`` /
   ``test_workflow_b5a`` 依赖的符号仍可从 ``learning_workflow`` 导入；
5. **checkpoint 默认关闭**：config 与迁移前一致（仅 ``recursion_limit``）。

与 ``test_workflow_b5a.py`` 的分工：那份是 B5-a 原始验收（仍应全绿），
本份专门盯 R2 迁移引入的差异。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agents import critic_agent
from app.core import checkpoint
from app.core.database import SessionLocal
from app.core.llm import set_force_critic_low
from app.main import app
from app.services import workflow_runner
from app.workflows import learning_workflow as lw
from app.workflows.learning_workflow import (
    AGENT_ORDER,
    MAX_RETRIES,
    VALIDATION_THRESHOLD,
    Retriever,
    WorkflowState,
    run_learning_workflow,
)

AGENT_IDS = ["diagnosis", "generation", "critic"]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # 触发 lifespan：建表（含 WorkflowTrace）+ 幂等种子
        yield c


@pytest.fixture()
def db(client):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _reset_critic_hook():
    yield
    set_force_critic_low(False)


# ── 1. 导出面冻结（5 类调用方的依赖）──────────────────────────────────────────


def test_public_exports_remain_importable():
    assert callable(run_learning_workflow)
    assert AGENT_ORDER == ("diagnosis", "generation", "critic")
    assert MAX_RETRIES == 2
    assert isinstance(VALIDATION_THRESHOLD, float)
    assert callable(lw._ability_tier)          # services/resource + scripts/regen_lectures
    assert callable(lw._build_graph)
    assert WorkflowState.__name__ == "LegacyWorkflowState"  # workflow_runner 依赖该类型
    assert callable(Retriever)


def test_validation_threshold_single_source():
    """R2 把阈值并成单一真值源：不再各处手写 0.8。"""
    assert VALIDATION_THRESHOLD == critic_agent.PASS_SCORE == 0.8


def test_checkpoint_disabled_by_default():
    assert checkpoint.enabled() is False
    assert checkpoint.get_checkpointer() is None
    # 关闭时 config 与迁移前逐字一致
    assert checkpoint.graph_config("wf_1", None) == {"recursion_limit": 50}
    # 开启时才附带 thread_id（LangGraph checkpointer 模式要求）
    assert checkpoint.graph_config("wf_1", object()) == {
        "recursion_limit": 50,
        "configurable": {"thread_id": "wf_1"},
    }


def test_graph_compiles_without_checkpointer(db):
    graph = lw._build_graph(db, lw._mock_retriever)
    assert graph is not None


# ── 2. 返回结构 parity ────────────────────────────────────────────────────────


def test_return_contract_and_trace_shape(db):
    result = run_learning_workflow(db, user_id="u_10001", kp_id="nn", difficulty="初级")
    assert set(result) == {"workflowId", "finalOutput", "trace"}

    final = result["finalOutput"]
    assert set(final) == {
        "markdown", "validationScore", "hallucinationRate", "degraded", "retryCount",
    }
    assert final["degraded"] is False

    trace = result["trace"]
    assert set(trace) == {
        "workflowId", "phase", "step", "agents", "messages",
        "nodes", "ragContextUsed", "stats",
    }
    assert trace["phase"] == "complete" and trace["step"] == 4
    assert [a["id"] for a in trace["agents"]] == AGENT_IDS
    assert trace["stats"] == {
        "completedAgents": 3,
        "messageCount": len(trace["messages"]),
        "progress": 100,
    }


def test_trace_nodes_keep_migration_before_fields(db):
    result = run_learning_workflow(db, user_id="u_10001", kp_id="nn", difficulty="初级")
    nodes = result["trace"]["nodes"]
    agents_in_nodes = [n["agent"] for n in nodes]
    for agent_id in AGENT_IDS:
        assert agent_id in agents_in_nodes
    for n in nodes:
        assert {"agent", "name", "startedAt", "endedAt", "durationMs",
                "retryIndex", "outputSummary"} <= set(n)
        assert n["startedAt"] and n["endedAt"]
        assert isinstance(n["durationMs"], (int, float)) and n["durationMs"] >= 0
        assert isinstance(n["retryIndex"], int)
        assert n["outputSummary"]
    # promptExcerpt 只在 Agent 节点（诊断/生成/审核）出现，RAG 节点不带
    assert any("promptExcerpt" in n for n in nodes)


# ── 3. 11.2 messages 文案 parity（前端无感的核心）─────────────────────────────


def _messages(db, **kwargs):
    result = run_learning_workflow(db, user_id="u_10001", **kwargs)
    return result["trace"]["messages"], result


def test_message_texts_match_migration_before(db):
    messages, _ = _messages(db, kp_id="nn", difficulty="初级")
    assert [m["id"] for m in messages] == list(range(1, len(messages) + 1))
    for m in messages:
        assert set(m) == {"id", "from", "to", "message", "type", "timestamp"}
        assert m["from"] and m["to"] and m["message"] and m["timestamp"]
        assert m["type"] in ("request", "response", "error")

    # 逐条对齐迁移前 _msg() 的文案
    first = messages[0]
    assert first["from"] == "用户"
    assert first["to"] == "学情诊断Agent"
    # 文案含知识点**名称**（kp.name，非 kp_id）—— 与迁移前 f-string 完全一致
    assert first["message"].startswith("发起「")
    assert first["message"].endswith("学习资源生成请求")
    assert first["type"] == "request"

    assert any(m["from"] == "RAG系统" for m in messages)
    assert any("检索到 " in m["message"] and "条相关知识切片" in m["message"] for m in messages)
    assert any("讲义草稿生成完成" in m["message"] for m in messages)
    assert any("请校验" in m["message"] and m["type"] == "request" for m in messages)
    assert messages[-1]["from"] == "内容审核校验Agent"
    assert messages[-1]["to"] == "用户"
    assert messages[-1]["message"] == "校验通过，学习资源已交付"


def test_degraded_message_texts_and_error_types(db):
    set_force_critic_low(True)
    messages, result = _messages(db, kp_id="attention", difficulty="高级")
    errors = [m for m in messages if m["type"] == "error"]
    assert errors, "降级路径必须产出 error 类型消息"
    assert any("发起第 1 次重试" in m["message"] for m in errors)
    assert any("触发降级输出" in m["message"] for m in errors)
    assert messages[-1]["message"].startswith("已降级交付（validationScore=")
    assert messages[-1]["to"] == "用户"

    final = result["finalOutput"]
    assert final["degraded"] is True
    assert final["retryCount"] == MAX_RETRIES == 2
    nodes = result["trace"]["nodes"]
    gen_nodes = [n for n in nodes if n["agent"] == "generation"]
    critic_nodes = [n for n in nodes if n["agent"] == "critic"]
    assert len(gen_nodes) == 3 and len(critic_nodes) == 3
    assert [n["retryIndex"] for n in gen_nodes] == [0, 1, 2]
    assert {a["id"]: a["status"] for a in result["trace"]["agents"]}["critic"] == "error"


# ── 4. runner 零改动：投影结果直接喂给迁移前的消费者 ─────────────────────────


def test_projected_state_feeds_unchanged_snapshot_builder(db):
    """把 on_update 收到的每一帧直接交给 workflow_runner._snapshot_from_state。

    runner 是迁移前的消费者、一行未改；它能跑通 = 投影对齐 = 前端快照不变。
    """
    frames: list[dict] = []

    def on_update(state: WorkflowState) -> None:
        frames.append(state)
        # 迁移前 runner 的两个真实读取动作（原样复刻）
        snapshot = workflow_runner._snapshot_from_state("wf_parity", state)
        assert set(snapshot) == {
            "workflowId", "phase", "step", "agents", "messages", "stats",
        }
        assert [a["id"] for a in snapshot["agents"]] == list(AGENT_ORDER)
        for m in snapshot["messages"]:
            assert list(m)[:1] == ["id"]

    result = run_learning_workflow(
        db,
        user_id="u_10001",
        kp_id="nn",
        difficulty="初级",
        on_update=on_update,
    )

    # 至少回调了诊断/检索/生成/校验/决策 5 帧
    assert len(frames) >= 5
    # 首帧（无 current_node）必须被跳过 —— 与迁移前行为一致
    assert all(f.get("current_node") for f in frames)
    assert frames[-1]["current_node"] == "decision"

    # runner 的相位推导在每一帧上都成立
    for frame in frames:
        phase, step, active = workflow_runner._phase_step_active(frame)
        assert phase in ("diagnosis", "generation", "validation", "complete")
        assert isinstance(step, int)


def test_final_snapshot_matches_returned_trace(db):
    """on_update 最后一帧喂给 runner，应与 run_learning_workflow 返回的 trace 同源。"""
    last_frame: dict = {}

    def on_update(state: WorkflowState) -> None:
        last_frame.clear()
        last_frame.update(state)

    result = run_learning_workflow(
        db, user_id="u_10001", kp_id="nn", difficulty="初级", on_update=on_update
    )
    assert last_frame["current_node"] == "decision"
    assert last_frame["workflow_status"] == "success"
    # 消息条数一致（投影同源），编号后即 11.2
    assert len(last_frame["messages"]) == len(result["trace"]["messages"])
    assert last_frame["node_execution_log"] == result["trace"]["nodes"]


# ── 5. 状态分层：节点只写增量，嵌套不互吞 ─────────────────────────────────────


def test_retry_rounds_accumulate_node_log_and_feedback(db):
    """重试 2 轮：nodeLog 累加到 6 条 Agent 节点、feedback 回灌但不抹掉 markdown。"""
    set_force_critic_low(True)
    result = run_learning_workflow(db, user_id="u_10001", kp_id="attention",
                                   difficulty="高级")
    nodes = result["trace"]["nodes"]
    agent_nodes = [n for n in nodes if n["agent"] in AGENT_IDS]
    # 诊断 1 + 生成 3 + 审核 3 = 7（RAG 节点 agent="rag" 不计入）
    assert len(agent_nodes) == 7
    # 降级仍交付非空产物 → artifacts.markdown 未被 feedback 覆盖
    assert result["finalOutput"]["markdown"]
    assert result["finalOutput"]["requiresReview"] is True
