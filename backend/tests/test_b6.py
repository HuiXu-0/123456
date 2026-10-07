"""B6 契约测试：P1 特色 6 接口（JobMarket / KnowledgeGraph / Reinforce / External / Dashboard）。

验收口径（执行方案 B6 + 接口文档 5/8.6/9.2/10/12 章）：
- JobMarket：预置 4 岗位（种子自 frontend/public/data/job-market/*.json）；
  数据源不可用（job_market_offline 模拟）→ code 2002 + data.offline=true（HTTP 200 非 500）；
- KnowledgeGraph：12 节点 / 14 边；category 按 mastery 实时推导
  （passed→0 / learning|pending-check→1 / 未开始高 value→2 / value<20 盲区→3），
  含推导边界用例；
- Reinforce：mock 确定性输出（两次调用逐字一致）；deepseek 路径（monkeypatch
  llm_deepseek.chat，无 Key 可跑）生成练习题须 critic 审核自洽——
  correct_answer 在 options 中且 explanation 非空，不自洽重试一次仍失败 → 2001；
- External：6 KP 精选种子，relevance 降序，字段对齐 8.6；
- Dashboard：与明细接口严格一致（radar=4.4、targetSummary=7.4、
  强弱项/评分与 10.1 图谱节点同口径），mastery 置 passed → 图谱该节点 category=0
  且 overall_score / strong_topics 联动（一致性断言）。

测试自带 mastery 状态清理（dev 库不残留），全程 mock provider（conftest 基线）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import Mastery, StudentPortrait, User
from app.services import student_portrait as portrait_service
from app.services.knowledge_graph import derive_node


@pytest.fixture(scope="module")
def client():
    # with 触发 lifespan：建表 + 幂等种子（含 B6 JobSnapshot / ExternalResource）
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
def learner_headers(client) -> dict[str, str]:
    return _login(client, "learner_001", "123456")


@pytest.fixture()
def clean_mastery(db):
    """测试前后清空 learner_001 的掌握度行（保存原状并复原，不污染 dev 库）。"""
    user = db.query(User).filter(User.username == "learner_001").one()
    original = [
        (m.kp_id, m.status)
        for m in db.query(Mastery).filter(Mastery.user_id == user.id).all()
    ]

    def _reset() -> None:
        db.query(Mastery).filter(Mastery.user_id == user.id).delete()
        db.commit()

    _reset()
    yield user
    _reset()
    for kp_id, status in original:
        db.add(Mastery(user_id=user.id, kp_id=kp_id, status=status))
    db.commit()


# ---- JobMarket（接口文档 5.1/5.2/15.5） ----------------------------------------

def test_job_hot_list_contract_order(client, learner_headers):
    res = client.get("/api/v1/job-market/hot", headers=learner_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == 0
    assert [j["id"] for j in body["data"]] == [
        "llm-app", "algo-engineer", "ml-engineer", "data-analyst"
    ]
    assert body["data"][0]["name"] == "大模型应用工程师"


def test_job_snapshot_contract_fields(client, learner_headers, monkeypatch):
    # 本用例只验证正常态字段；陈旧快照降级由 TASK-006-G 专项覆盖。
    monkeypatch.setattr(settings, "job_market_max_age_hours", 24 * 365 * 10)
    res = client.get("/api/v1/job-market/llm-app", headers=learner_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == 0
    data = body["data"]
    # 2.4 JobMarket 契约字段（前端 JSON 原样导入）
    for key in (
        "id", "name", "salaryRange", "salaryMedian", "heat", "heatPct",
        "openings", "source", "fetchedAt", "skills", "radar",
    ):
        assert key in data, f"缺字段 {key}"
    assert data["id"] == "llm-app" and data["heatPct"] == 95
    assert {s["name"] for s in data["skills"]} >= {"Prompt 工程", "Python"}
    # 雷达 6 维固定键名（2.4 备注）
    assert set(data["radar"]) == {
        "机器学习基础", "神经网络", "深度学习", "注意力机制", "Transformer", "大模型微调"
    }
    assert "offline" not in data  # 正常路径不带降级标记


def test_job_snapshot_degrades_to_2002_not_500(client, learner_headers, monkeypatch):
    """模拟岗位数据源故障 → 2002 + offline:true + 最近快照（而非 500）。"""
    monkeypatch.setattr(settings, "job_market_offline", True)
    res = client.get("/api/v1/job-market/llm-app", headers=learner_headers)
    assert res.status_code == 200  # 1.3：2002 → HTTP 200
    body = res.json()
    assert body["code"] == 2002
    assert body["data"]["offline"] is True
    assert body["data"]["id"] == "llm-app"  # 最近快照仍完整返回
    assert body["data"]["fetchedAt"]  # 前端 timeAgo 渲染快照年龄


def test_job_snapshot_unknown_id_404(client, learner_headers):
    res = client.get("/api/v1/job-market/nope", headers=learner_headers)
    assert res.status_code == 404
    assert res.json()["code"] == 1004


# ---- KnowledgeGraph（接口文档 10.1） -------------------------------------------

def test_graph_structure_12_nodes_14_links(client, learner_headers):
    res = client.get("/api/v1/knowledge-graph", headers=learner_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert len(data["nodes"]) == 12
    assert len(data["links"]) == 14
    assert [c["name"] for c in data["categories"]] == [
        "已掌握", "学习中", "待学习", "知识盲区"
    ]
    node = data["nodes"][0]
    assert set(node) == {"id", "name", "category", "value"}
    assert {"source": "ml", "target": "nn"} in data["links"]


def test_graph_category_derived_from_mastery(client, learner_headers, clean_mastery):
    """mastery 联动：passed→0/100；learning→1/min(值,60)；未开始按种子值推导。"""
    # 未测知识点不再使用虚构基线分：统一待学习(2) / value=0。
    nodes = {
        n["id"]: n
        for n in client.get("/api/v1/knowledge-graph", headers=learner_headers)
        .json()["data"]["nodes"]
    }
    assert (nodes["nn"]["category"], nodes["nn"]["value"]) == (2, 0)
    assert (nodes["transformer"]["category"], nodes["transformer"]["value"]) == (2, 0)

    # nn 置 passed → category 0 / value 100
    assert (
        client.post("/api/v1/mastery/nn/pass", headers=learner_headers).status_code
        == 200
    )
    # dl 置 pending-check 但尚无实测分 → category 1 / value 0
    assert (
        client.post("/api/v1/mastery/dl/check", headers=learner_headers).status_code
        == 200
    )
    nodes = {
        n["id"]: n
        for n in client.get("/api/v1/knowledge-graph", headers=learner_headers)
        .json()["data"]["nodes"]
    }
    assert (nodes["nn"]["category"], nodes["nn"]["value"]) == (0, 100)
    assert (nodes["dl"]["category"], nodes["dl"]["value"]) == (1, 0)


def test_derive_node_boundaries():
    """category 推导边界：盲区阈值 20、学习中 60 封顶、passed 恒 100。"""
    assert derive_node("passed", 12) == (0, 100)
    assert derive_node("learning", 85) == (1, 60)   # 高种子值封顶 60
    assert derive_node("pending-check", 35) == (1, 35)
    assert derive_node(None, 20) == (2, 20)          # 阈值上：待学习
    assert derive_node(None, 19) == (3, 19)          # 阈值下：盲区
    assert derive_node(None, 92) == (2, 92)


# ---- Reinforce（接口文档 9.2） --------------------------------------------------

def test_reinforce_mock_deterministic(client, learner_headers):
    """mock 确定性：两次调用逐字一致；卡片结构与预置库对齐。"""
    payload = {"kpId": "nn", "wrongQuestionIds": ["nn_q1", "nn_q3"]}
    first = client.post("/api/v1/reinforce", json=payload, headers=learner_headers)
    second = client.post("/api/v1/reinforce", json=payload, headers=learner_headers)
    assert first.status_code == 200
    assert first.json()["data"] == second.json()["data"]  # 确定性

    cards = first.json()["data"]
    assert [c["questionId"] for c in cards] == ["nn_q1", "nn_q3"]
    assert cards[0]["point"] == "神经元运算顺序"
    assert cards[0]["practice"]["question_id"] == "nn_q1-r"
    for card in cards:
        practice = card["practice"]
        option_ids = [o["option_id"] for o in practice["options"]]
        correct = practice["correct_answer"]
        assert (
            all(c in option_ids for c in correct)
            if isinstance(correct, list)
            else correct in option_ids
        )
        assert practice["explanation"].strip()
        assert card["recap"].strip()


def test_reinforce_generic_fallback_self_consistent(client, learner_headers):
    """预置库外的题走确定性变式兜底：选项轮转后答案仍自洽。"""
    res = client.post(
        "/api/v1/reinforce",
        json={"kpId": "ml", "wrongQuestionIds": ["ml_q1"]},
        headers=learner_headers,
    )
    assert res.status_code == 200
    card = res.json()["data"][0]
    assert card["questionId"] == "ml_q1"
    assert card["practice"]["question_text"].startswith("【强化·变式】")
    option_ids = [o["option_id"] for o in card["practice"]["options"]]
    assert card["practice"]["correct_answer"] in option_ids


def test_reinforce_unknown_kp_404_and_unknown_qid_filtered(client, learner_headers):
    res = client.post(
        "/api/v1/reinforce",
        json={"kpId": "nope", "wrongQuestionIds": ["x"]},
        headers=learner_headers,
    )
    assert res.status_code == 404 and res.json()["code"] == 1004

    res = client.post(
        "/api/v1/reinforce",
        json={"kpId": "nn", "wrongQuestionIds": ["not_a_question"]},
        headers=learner_headers,
    )
    assert res.status_code == 200 and res.json()["data"] == []


@pytest.fixture()
def deepseek_llm(monkeypatch):
    """进程内 LLM 单例切到 deepseek provider（测试后自动还原）。"""
    from app.core import llm as llm_mod

    fake = llm_mod.LLMClient("deepseek")
    monkeypatch.setattr(llm_mod, "_client", fake)
    return fake


_VALID_REINFORCE_JSON = """
{"items": [{"questionId": "nn_q1", "point": "神经元计算流程",
  "recap": "先加权求和、加偏置，最后过激活函数。",
  "practice": {"question_id": "nn_q1-r", "question_type": "single",
    "question_text": "偏置 b 在神经元中的作用是？",
    "options": [{"option_id": "a", "option_text": "平移激活输入"},
                {"option_id": "b", "option_text": "归一化输出"}],
    "correct_answer": "a", "explanation": "偏置对加权和做平移，再交给激活函数。"}}]}
"""

# correct_answer 指向不存在的选项 → critic 审核必须拦下
_BROKEN_REINFORCE_JSON = _VALID_REINFORCE_JSON.replace(
    '"correct_answer": "a"', '"correct_answer": "c"'
)


def test_reinforce_deepseek_real_path_audited(
    client, learner_headers, deepseek_llm, monkeypatch
):
    """真实模式（monkeypatch chat）：生成结果过 critic 审核后原样返回。"""
    from app.core import llm_deepseek

    monkeypatch.setattr(llm_deepseek, "chat", lambda *a, **k: _VALID_REINFORCE_JSON)
    res = client.post(
        "/api/v1/reinforce",
        json={"kpId": "nn", "wrongQuestionIds": ["nn_q1"]},
        headers=learner_headers,
    )
    assert res.status_code == 200, res.text
    card = res.json()["data"][0]
    assert card["questionId"] == "nn_q1"
    option_ids = [o["option_id"] for o in card["practice"]["options"]]
    assert card["practice"]["correct_answer"] in option_ids  # 验收标准 3
    assert card["practice"]["explanation"].strip()


def test_reinforce_deepseek_retry_then_2001(
    client, learner_headers, deepseek_llm, monkeypatch
):
    """答案不自洽 → 带审核反馈重试一次；仍不自洽 → 2001 / HTTP 500。"""
    from app.core import llm_deepseek

    calls: list[str] = []

    def _always_broken(prompt: str, **kwargs):
        calls.append(prompt)
        return _BROKEN_REINFORCE_JSON

    monkeypatch.setattr(llm_deepseek, "chat", _always_broken)
    res = client.post(
        "/api/v1/reinforce",
        json={"kpId": "nn", "wrongQuestionIds": ["nn_q1"]},
        headers=learner_headers,
    )
    assert res.status_code == 500
    assert res.json()["code"] == 2001
    assert len(calls) == 2  # 首次 + 重试一次
    assert "审核" in calls[1]  # 重试 prompt 带审核反馈


def test_reinforce_deepseek_retry_recovers(
    client, learner_headers, deepseek_llm, monkeypatch
):
    """首轮不自洽、重试修正 → 正常返回（重试链路真实生效）。"""
    from app.core import llm_deepseek

    outputs = iter([_BROKEN_REINFORCE_JSON, _VALID_REINFORCE_JSON])
    monkeypatch.setattr(llm_deepseek, "chat", lambda *a, **k: next(outputs))
    res = client.post(
        "/api/v1/reinforce",
        json={"kpId": "nn", "wrongQuestionIds": ["nn_q1"]},
        headers=learner_headers,
    )
    assert res.status_code == 200
    assert res.json()["data"][0]["practice"]["correct_answer"] == "a"


# ---- External（接口文档 8.6） ---------------------------------------------------

def test_external_resources_seeded_and_sorted(client, learner_headers):
    res = client.get("/api/v1/resource/external/nn", headers=learner_headers)
    assert res.status_code == 200
    items = res.json()["data"]
    assert len(items) == 4  # nn 种子 4 条（与前端演示对齐）
    relevances = [i["relevance"] for i in items]
    assert relevances == sorted(relevances, reverse=True)  # 相关度降序
    top = items[0]
    for key in ("id", "type", "title", "source", "url", "relevance", "credibility", "reason"):
        assert key in top
    assert top["type"] == "视频" and top["embed"].startswith("https://")
    # 全部 6 核心 KP 均有 3-4 条种子
    for kp in ("ml", "dl", "cnn", "transformer", "finetune"):
        rows = client.get(
            f"/api/v1/resource/external/{kp}", headers=learner_headers
        ).json()["data"]
        assert 3 <= len(rows) <= 4, kp
        assert all(r["type"] in ("视频", "论文", "文档", "课程") for r in rows)


def test_external_unknown_kp_404(client, learner_headers):
    res = client.get("/api/v1/resource/external/nope", headers=learner_headers)
    assert res.status_code == 404 and res.json()["code"] == 1004


# ---- Dashboard（接口文档 12.1）：真实画像 / Mastery 派生（C1-c 真实化） ---------

# 一份确定性 6 维异质画像（与前端 synthesizeOverview 口径对拍用的固定输入）
_PORTRAIT_DIMS = [
    {"key": "knowledge_base", "label": "知识基础", "value": "扎实", "score": 80,
     "confidence": 0.7, "source": "dialogue"},
    {"key": "prior_experience", "label": "先验经验", "value": "有项目经验",
     "confidence": 0.6, "source": "dialogue"},
    {"key": "learning_goal", "label": "学习目标", "value": "转岗",
     "confidence": 0.9, "source": "dialogue"},
    {"key": "cognitive_style", "label": "认知风格", "value": "实践型",
     "confidence": 0.5, "source": "dialogue"},
    {"key": "learning_pace", "label": "学习节奏", "value": "适中",
     "confidence": 0.6, "source": "dialogue"},
    {"key": "error_preference", "label": "易错点偏好", "value": "概念混淆",
     "confidence": 0.5, "source": "inferred"},
]


@pytest.fixture()
def clean_portrait(db):
    """测试前后清空 learner_001 的 StudentPortrait（保存原状并复原，不污染 dev 库）。"""
    user = db.query(User).filter(User.username == "learner_001").one()
    original = db.get(StudentPortrait, user.id)
    saved = (list(original.dimensions or []), original.version) if original else None

    def _del() -> None:
        row = db.get(StudentPortrait, user.id)
        if row is not None:
            db.delete(row)
            db.commit()

    _del()
    yield user
    _del()
    if saved is not None:
        db.add(StudentPortrait(user_id=user.id, dimensions=saved[0], version=saved[1]))
        db.commit()


def test_dashboard_empty_for_new_undiagnosed_user(client, clean_mastery, clean_portrait):
    """验收 1（C2）：无画像、无 Mastery 的新用户 → 能力雷达 6 轴全 0（未测，不臆造）、
    优势/盲区为空（未测 ≠ 盲区）、综合分 0、覆盖率/已学资源为 0、偏好为空。"""
    from app.core.llm import ABILITY_DIMENSIONS

    headers = _login(client, "learner_001", "123456")
    data = client.get(
        "/api/v1/dashboard/overview", headers=headers
    ).json()["data"]
    for key in (
        "overall_level", "overall_score", "knowledge_graph_coverage",
        "learned_resources", "strong_topics", "weak_topics", "radar",
        "preferences", "comparison", "targetSummary",
    ):
        assert key in data, f"缺字段 {key}"
    # C2：能力雷达固定 6 知识点轴；未测 → 全 0（honest，不臆造）
    assert data["radar"] == {"dimensions": list(ABILITY_DIMENSIONS), "values": [0] * 6}
    assert data["strong_topics"] == [] and data["weak_topics"] == []  # 未测不计强弱
    assert data["preferences"] == []
    assert data["overall_score"] == 0.0 and data["overall_level"] == "初学"
    assert data["knowledge_graph_coverage"] == 0.0
    assert data["learned_resources"] == 0
    assert data["comparison"]["betterThanPct"] == 0


def test_dashboard_derived_from_ability_and_preferences(
    client, db, clean_mastery, clean_portrait
):
    """验收 2/3（C2）：overview 能力雷达/优势/盲区/综合分来自真实 Mastery 能力分（靠测）；
    偏好以类型标签呈现、不上轴；覆盖率/已学资源来自真实 Mastery 通过状态。"""
    from app.core.llm import ABILITY_DIMENSIONS
    from app.services import mastery as mastery_service

    user = clean_portrait
    # 偏好/主观画像（不打分、不上能力轴）
    portrait_service.apply_updates(db, user.id, _PORTRAIT_DIMS)
    # 能力分：实测写入 Mastery（ml 强、nn 弱，其余未测）
    mastery_service.set_baseline(db, user.id, "ml", score=80, confidence=0.45)
    mastery_service.set_baseline(db, user.id, "nn", score=30, confidence=0.45)
    headers = _login(client, "learner_001", "123456")

    before = client.get(
        "/api/v1/dashboard/overview", headers=headers
    ).json()["data"]

    # 能力雷达：固定 6 知识点轴；ml→80、nn→30、其余未测→0
    assert before["radar"] == {
        "dimensions": list(ABILITY_DIMENSIONS),
        "values": [80, 30, 0, 0, 0, 0],
    }
    # 优势 = 已测降序前 3（只 ml/nn 已测）
    assert before["strong_topics"] == [
        {"name": "机器学习基础", "mastery": 80},
        {"name": "神经网络", "mastery": 30},
    ]
    # 盲区 = 已测且 <60（nn）；未测知识点不计入盲区
    assert before["weak_topics"] == [{"name": "神经网络", "mastery": 30}]
    # 综合分 = (80+30+0+0+0+0)/6 = 18.3
    assert before["overall_score"] == 18.3
    # 偏好画像：类型标签、无分数、不在雷达轴上
    pref_keys = {p["key"] for p in before["preferences"]}
    assert pref_keys == {"cognitive_style", "learning_pace", "error_preference"}
    for p in before["preferences"]:
        assert "score" not in p and p["value"]
    assert not (set(before["radar"]["dimensions"]) & {"认知风格", "学习节奏", "易错点偏好"})
    # 覆盖率 / 已学资源：尚无通过的核心知识点 → 0
    assert before["knowledge_graph_coverage"] == 0.0
    assert before["learned_resources"] == 0
    journey = client.get("/api/v1/journey", headers=headers).json()["data"]
    assert before["targetSummary"] == {
        "hasDiagnosed": journey["hasDiagnosed"],
        "targetJobName": journey["targetJobName"],
        "matchPct": journey["matchPct"],
    }

    # 通过一个核心知识点 → 覆盖率 / 已学资源联动增长（能力分由实测驱动，与状态解耦）
    client.post("/api/v1/mastery/nn/pass", headers=headers)
    after = client.get(
        "/api/v1/dashboard/overview", headers=headers
    ).json()["data"]
    assert after["learned_resources"] == 1
    assert after["knowledge_graph_coverage"] == round(1 / 6, 2)  # 0.17
    # 能力雷达来自 Mastery 能力分；/pass 仅改状态不改分 → 雷达不变
    assert after["radar"] == before["radar"]
    assert after["overall_score"] == before["overall_score"]
