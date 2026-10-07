"""B5-a 契约测试：LangGraph 学习工作流骨架（纯 Mock provider）。

验收口径（执行方案 B5 前半 + 需求文档 4.2/9.3 + 接口文档 11.2）：
- 完整工作流跑通：trace 含 diagnosis/generation/critic 3 个 Agent 节点与耗时；
- 强制 critic 低分（测试钩子）→ 回 generator 重试 2 次 → 降级输出，
  final_output 标记 validation_score 与 degraded:true；
- PUT 修改 generation 模板后再跑 → 新 prompt 生效（B4-b 热更新消费验证）；
- trace（agents/messages/节点日志/rag_context_used）持久化，结构满足 11.2 渲染；
- 不触碰既有 API：/resource/lecture 仍走 B2 Mock service，无回归。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core.llm import get_llm, set_force_critic_low
from app.main import app
from app.models.entities import WorkflowTrace
from app.workflows.learning_workflow import (
    MAX_RETRIES,
    VALIDATION_THRESHOLD,
    run_learning_workflow,
)

AGENT_IDS = ["diagnosis", "generation", "critic"]


@pytest.fixture(scope="module")
def client():
    # with 触发 lifespan：建表（含 WorkflowTrace）+ 幂等种子
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db(client):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    res = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['data']['token']}"}


@pytest.fixture(scope="module")
def admin_headers(client) -> dict[str, str]:
    return _login(client, "admin", "admin123")


@pytest.fixture(scope="module")
def learner_headers(client) -> dict[str, str]:
    return _login(client, "learner_001", "123456")


@pytest.fixture(autouse=True)
def _reset_critic_hook():
    """每条用例后复位测试钩子，避免串扰。"""
    yield
    set_force_critic_low(False)


# ---- MockLLM：按 prompt 类型返回确定性结构化输出 -----------------------------

def test_mock_llm_structured_outputs():
    llm = get_llm()
    diag = llm.complete("诊断 prompt", agent_id="diagnosis")
    assert isinstance(diag.get("weakKpIds"), list) and diag["weakKpIds"]
    assert diag.get("summary")

    gen = llm.complete("生成 prompt", agent_id="generation")
    assert isinstance(gen.get("markdown"), str) and gen["markdown"]

    critic = llm.complete("审核 prompt", agent_id="critic")
    assert critic["passed"] is True
    assert critic["validationScore"] >= VALIDATION_THRESHOLD
    assert 0.0 <= critic["hallucinationRate"] <= 1.0

    # 测试钩子：强制低分
    set_force_critic_low(True)
    low = llm.complete("审核 prompt", agent_id="critic")
    assert low["passed"] is False
    assert low["validationScore"] < VALIDATION_THRESHOLD


# ---- 完整工作流（happy path） -------------------------------------------------

def test_workflow_happy_path_trace_and_persistence(db):
    result = run_learning_workflow(
        db, user_id="u_10001", kp_id="nn", difficulty="初级"
    )
    workflow_id = result["workflowId"]
    assert workflow_id.startswith("wf_")

    final = result["finalOutput"]
    assert final["degraded"] is False
    assert final["validationScore"] >= VALIDATION_THRESHOLD
    assert isinstance(final["markdown"], str) and final["markdown"]
    assert final["retryCount"] == 0

    trace = result["trace"]
    # 节点日志：3 个 Agent 节点、各有起止时间与耗时
    nodes = trace["nodes"]
    agents_in_nodes = [n["agent"] for n in nodes]
    for agent_id in AGENT_IDS:
        assert agent_id in agents_in_nodes
    for n in nodes:
        assert n["startedAt"] and n["endedAt"]
        assert isinstance(n["durationMs"], (int, float)) and n["durationMs"] >= 0
        assert isinstance(n["retryIndex"], int)
        assert n["outputSummary"]

    # 11.2 agents[]：3 项、id 固定、name 来自 PromptTemplate、status=success
    agents = trace["agents"]
    assert [a["id"] for a in agents] == AGENT_IDS
    for a in agents:
        assert a["name"] and a["status"] == "success" and a["lastAction"]

    # 11.2 messages[]：id 自增、字段齐全、type 合法
    messages = trace["messages"]
    assert messages
    assert [m["id"] for m in messages] == list(range(1, len(messages) + 1))
    for m in messages:
        assert m["from"] and m["to"] and m["message"] and m["timestamp"]
        assert m["type"] in ("request", "response", "error")

    # rag_context_used 非空（B5-a 为确定性 mock 切片，B5-b 接真实检索）
    assert trace["ragContextUsed"]

    # stats 满足 11.2 渲染
    stats = trace["stats"]
    assert stats["completedAgents"] == 3
    assert stats["messageCount"] == len(messages)
    assert stats["progress"] == 100

    # 持久化：WorkflowTrace 行存在且字段一致
    row = db.get(WorkflowTrace, workflow_id)
    assert row is not None
    assert row.degraded is False
    assert row.validation_score >= VALIDATION_THRESHOLD
    assert row.agents and row.messages and row.node_log
    assert row.rag_context_used
    assert row.status == "success"


# ---- 强制低分 → 重试 2 次 → 降级 ----------------------------------------------

def test_workflow_forced_low_score_retries_then_degrades(db):
    set_force_critic_low(True)
    result = run_learning_workflow(
        db, user_id="u_10001", kp_id="attention", difficulty="高级"
    )

    final = result["finalOutput"]
    assert final["degraded"] is True
    assert final["validationScore"] < VALIDATION_THRESHOLD
    assert final["retryCount"] == MAX_RETRIES == 2
    # 降级仍要有可用输出（降级输出，非空响应）
    assert isinstance(final["markdown"], str) and final["markdown"]

    # generation / critic 各执行 3 次（首次 + 2 次重试），retryIndex 递增
    nodes = result["trace"]["nodes"]
    gen_nodes = [n for n in nodes if n["agent"] == "generation"]
    critic_nodes = [n for n in nodes if n["agent"] == "critic"]
    assert len(gen_nodes) == 3 and len(critic_nodes) == 3
    assert [n["retryIndex"] for n in gen_nodes] == [0, 1, 2]

    # agents[]：critic 标记 error（驱动 AgentStatusCard 红灯）
    agents = {a["id"]: a for a in result["trace"]["agents"]}
    assert agents["critic"]["status"] == "error"

    # messages 含 error 类型消息（校验未通过）
    assert any(m["type"] == "error" for m in result["trace"]["messages"])

    # 持久化降级标记
    row = db.get(WorkflowTrace, result["workflowId"])
    assert row.degraded is True
    assert row.status == "degraded"
    assert row.retry_count == 2


# ---- Prompt 热更新：PUT 后再跑工作流，新 prompt 生效 ---------------------------

def test_prompt_hot_update_takes_effect_in_workflow(client, admin_headers, db):
    sentinel = "【B5A热更新哨兵】"
    before = client.get(
        "/api/v1/admin/prompts/generation", headers=admin_headers
    ).json()["data"]
    original = before["template"]
    # 新模板保留全部必需占位符（B8 起 generation 含 {description}）
    placeholders = "\n".join(f"{{{var}}}" for var in before["variables"])
    new_template = f"{sentinel}你是领域知识讲义生成专家。\n{placeholders}"
    try:
        res = client.put(
            "/api/v1/admin/prompts/generation",
            headers=admin_headers,
            json={"template": new_template},
        )
        assert res.status_code == 200, res.text

        result = run_learning_workflow(
            db, user_id="u_10001", kp_id="nn", difficulty="入门"
        )
        gen_nodes = [
            n for n in result["trace"]["nodes"] if n["agent"] == "generation"
        ]
        # 渲染后的 prompt 含哨兵 → get_template 现读 DB，热更新生效
        assert all(sentinel in n["promptExcerpt"] for n in gen_nodes)
        # 占位符已被渲染（不再残留 {kpName} 形式）
        assert "{kpName}" not in gen_nodes[0]["promptExcerpt"]
    finally:
        client.put(
            "/api/v1/admin/prompts/generation",
            headers=admin_headers,
            json={"template": original},
        )


# ---- 既有 API 无回归：lecture 仍走 B2 Mock service ----------------------------

def test_existing_lecture_api_untouched(client, learner_headers):
    res = client.post(
        "/api/v1/resource/lecture",
        headers=learner_headers,
        json={"kpId": "nn", "difficulty": "初级"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["markdown"] and data["sources"]
    assert 0.0 <= data["hallucinationRate"] <= 1.0
