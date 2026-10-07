"""学习过程评估 Agent 契约测试（接口文档 12.2，C-fix 批3）。

验收口径：
- GET /dashboard/evaluation 返回多维评估契约（overallScore/level/trend/dimensions/
  weakPoints/summary/suggestions/adjustment/...），mock 双模式可跑、需登录。
- **因人而异**：做题/掌握更多的用户，掌握进度/综合分高于零行为新用户；薄弱点据真实
  未通过知识点产出。
- 行为埋点：9.1 提交后 QuizAttempt 落库，评估据此聚合（attemptCount 增长）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

EVAL_KEYS = {
    "overallScore", "level", "trend", "dimensions", "weakPoints",
    "summary", "suggestions", "adjustment", "generatedBy", "signals",
    "generationMeta",
}
DIM_KEYS = {"key", "label", "score", "detail"}
ADJ_KEYS = {"nextKpId", "nextKpName", "difficultyAdvice", "action"}

# nn 全对（含简答按要点）→ 综合 ≥60 → passed，驱动 Mastery + QuizAttempt 埋点
_NN_PASS_ANSWERS = [
    {"question_id": "nn_q1", "answer": "b"},
    {"question_id": "nn_q2", "answer": ["a", "b", "d"]},
    {"question_id": "nn_q3", "answer": "true"},
    {"question_id": "nn_q4", "answer": "a"},
    {"question_id": "nn_q5", "answer": "b"},
    {"question_id": "nn_q6", "answer": "true"},
    {"question_id": "nn_q7", "answer": ["a", "b", "c"]},
    {"question_id": "nn_q8", "answer": "b"},
    {"question_id": "nn_q9", "answer": "false"},
    {"question_id": "nn_q10", "answer": "前向传播由输入逐层计算得到预测输出并计算损失；反向传播用链式法则反向逐层计算梯度更新参数；二者交替迭代完成训练。"},
]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, username: str) -> dict[str, str]:
    res = client.post("/api/v1/auth/register", json={"username": username, "password": "123456"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['data']['token']}"}


def _data(res) -> dict:
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["code"] == 0, body
    return body["data"]


def _assert_eval_shape(data: dict) -> None:
    assert set(data) == EVAL_KEYS, data.keys()
    assert isinstance(data["overallScore"], int) and 0 <= data["overallScore"] <= 100
    assert isinstance(data["level"], str) and data["level"]
    assert data["trend"] in {"improving", "declining", "stable"}
    assert isinstance(data["dimensions"], list) and len(data["dimensions"]) == 4
    for d in data["dimensions"]:
        assert set(d) == DIM_KEYS
        assert isinstance(d["score"], int) and 0 <= d["score"] <= 100
    assert isinstance(data["summary"], str) and data["summary"]
    assert isinstance(data["suggestions"], list) and data["suggestions"]
    assert all(isinstance(s, str) and s for s in data["suggestions"])
    assert set(data["adjustment"]) == ADJ_KEYS


def test_evaluation_requires_login(client):
    assert client.get("/api/v1/dashboard/evaluation").status_code == 401


def test_evaluation_fresh_user_zero_behavior(client):
    """零行为新用户：契约完整、掌握进度 0、薄弱点覆盖全部核心知识点（≤3 条）。"""
    headers = _register(client, "eval_fresh")
    data = _data(client.get("/api/v1/dashboard/evaluation", headers=headers))
    _assert_eval_shape(data)
    assert data["signals"]["attemptCount"] == 0
    assert data["signals"]["masteredCount"] == 0
    mastery_dim = next(d for d in data["dimensions"] if d["key"] == "mastery_progress")
    assert mastery_dim["score"] == 0
    # 未通过任何知识点 → 薄弱点非空（最多 3 条）
    assert 1 <= len(data["weakPoints"]) <= 3


def test_evaluation_reflects_real_behavior(client):
    """因人而异：做题通过后 attemptCount/掌握进度/综合分上升，且高于零行为用户。"""
    headers = _register(client, "eval_active")
    before = _data(client.get("/api/v1/dashboard/evaluation", headers=headers))

    submit = client.post("/api/v1/quiz/nn/submit", headers=headers, json={"answers": _NN_PASS_ANSWERS})
    assert submit.json()["data"]["passed"] is True

    after = _data(client.get("/api/v1/dashboard/evaluation", headers=headers))
    _assert_eval_shape(after)
    # 埋点生效：作答次数 +1
    assert after["signals"]["attemptCount"] == before["signals"]["attemptCount"] + 1
    # 掌握进度提升（nn 通过）
    assert after["signals"]["masteredCount"] >= 1
    after_mp = next(d for d in after["dimensions"] if d["key"] == "mastery_progress")["score"]
    before_mp = next(d for d in before["dimensions"] if d["key"] == "mastery_progress")["score"]
    assert after_mp > before_mp
    assert after["overallScore"] > before["overallScore"]
    # nn 已通过 → 不再出现在薄弱点
    assert all(w["kpId"] != "nn" for w in after["weakPoints"])
    # 动态调整建议指向某个未通过知识点（或全通过时为 None）
    nxt = after["adjustment"]["nextKpId"]
    assert nxt is None or nxt != "nn"
