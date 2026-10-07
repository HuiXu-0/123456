"""AI 知识体系录入（78 点 / 7 板块）+ 诊断适配（先修推断）+ 无回归 测试（会话一）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.entities import KnowledgePoint, Mastery, User
from app.services import knowledge_catalog


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # lifespan → init_db 幂等种子（含 78 点录入）
        yield c


@pytest.fixture()
def db(client):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['data']['token']}"}


@pytest.fixture(scope="module")
def learner_headers(client) -> dict[str, str]:
    return _login(client, "learner_001", "123456")


# ── 录入正确性（验证 1） ──────────────────────────────────────────────────────

def test_78_points_seeded(db):
    assert db.query(KnowledgePoint).count() == 78


def test_board_and_level_counts(db):
    counts: dict[str, int] = {}
    for kp in db.query(KnowledgePoint).all():
        counts[kp.category] = counts.get(kp.category, 0) + 1
    assert counts == {"ML": 12, "DL": 13, "CV": 7, "LLM": 12, "GEN": 13, "AGT": 13, "RLX": 8}
    # 层级枚举合法
    assert {kp.level for kp in db.query(KnowledgePoint).all()} <= {"入门", "进阶", "前沿"}


def test_idempotent_reseed(db):
    """重复执行 seed 不产生重复行、不改变计数（幂等）。"""
    knowledge_catalog.seed(db)
    db.commit()
    assert db.query(KnowledgePoint).count() == 78


# ── 现有 6 点作为已验证基准保留（验证 1） ──────────────────────────────────────

def test_core_6_preserved(db):
    core = {kp.id: kp for kp in knowledge_catalog.core_kps(db)}
    assert set(core) == {"ml", "nn", "dl", "cnn", "transformer", "finetune"}
    # 映射到体系编码
    assert core["ml"].code == "ML-1"
    assert core["nn"].code == "DL-1"
    assert core["dl"].code == "DL-4"
    assert core["cnn"].code == "DL-6"
    assert core["transformer"].code == "LLM-2"
    assert core["finetune"].code == "LLM-5"
    # 已生成内容（description）与既有 lesson_seq 未被覆盖（保留基准）
    assert core["nn"].description and "神经" in core["nn"].name
    assert sorted(kp.lesson_seq for kp in core.values()) == [1, 2, 3, 4, 5, 6]


def test_prerequisites_resolved_to_ids(db):
    """先修以 id 存储；跨核心别名点正确解析（DL-1→nn、DL-10 为新点）。"""
    tf = db.get(KnowledgePoint, "transformer")
    assert tf.prerequisites == ["DL-10"]  # LLM-2 先修 DL-10（注意力机制，新点）
    nn = db.get(KnowledgePoint, "nn")
    assert nn.prerequisites == ["ML-3"]  # DL-1 先修 ML-3
    cv6 = db.get(KnowledgePoint, "CV-6")  # 跨板块：CV-2 + LLM-2（解析到 transformer）
    assert cv6.prerequisites == ["CV-2", "transformer"]


# ── 诊断适配：先修推断（验证 2） ──────────────────────────────────────────────

def test_prerequisite_closure_infers_ancestors(db):
    prereq_map = {kp.id: list(kp.prerequisites or []) for kp in db.query(KnowledgePoint).all()}
    inferred = knowledge_catalog.infer_prerequisite_closure({"transformer"}, prereq_map)
    # 掌握 Transformer(LLM-2) → 推断其先修链：注意力→RNN→深度网络(dl)→…→机器学习(ml)
    assert {"DL-10", "DL-8", "dl", "DL-2", "nn", "ML-3", "ml"} <= inferred
    assert "transformer" not in inferred  # 不含自身


def test_board_coverage_reflects_tested_and_inferred(db):
    # 已测掌握 finetune(LLM-5, 高分) → LLM 板块 covered ≥ 2（自身 + 先修推断 LLM-3）
    status_map = {"finetune": "passed"}
    score_map = {"finetune": {"score": 90, "status": "passed"}}
    cov = {c["board"]: c for c in knowledge_catalog.board_coverage(db, status_map, score_map)}
    assert cov["LLM"]["total"] == 12
    assert cov["LLM"]["tested"] >= 1
    assert cov["LLM"]["covered"] >= 2  # finetune + 先修 LLM-3
    assert 0.0 <= cov["LLM"]["coveragePct"] <= 1.0


# ── 新端点：体系数据可用（Mock 兜底，验证 2/4） ──────────────────────────────

def test_knowledge_system_endpoint(client, learner_headers):
    res = client.get("/api/v1/knowledge-system", headers=learner_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["code"] == 0
    data = body["data"]
    assert len(data["points"]) == 78
    assert len(data["boards"]) == 7
    assert {b["code"] for b in data["boards"]} == {"ML", "DL", "CV", "LLM", "GEN", "AGT", "RLX"}
    assert len(data["coverage"]) == 7
    # 目录点契约字段齐全
    p = data["points"][0]
    assert {"id", "code", "name", "category", "level", "prerequisites", "isCore"} <= set(p)


def test_knowledge_system_requires_auth(client):
    assert client.get("/api/v1/knowledge-system").status_code == 401


# ── 无回归：既有链路仍收窄到 6 核心（验证 3） ─────────────────────────────────

def test_microtest_still_core_only(db):
    from app.services import diagnostic_microtest

    picked = diagnostic_microtest.select_microtest(db)
    assert {q["kpId"] for q in picked} <= {"ml", "nn", "dl", "cnn", "transformer", "finetune"}
    assert len(picked) <= 6


def test_dashboard_coverage_denominator_is_6(db):
    assert len(knowledge_catalog.core_kp_ids(db)) == 6


def test_planner_path_core_trunk_bounded(db, client, learner_headers):
    """录入 78 点后路径不回归为 78 步：6 核心始终作主干，有目标则聚焦细化到合理长度（≤12）。"""
    from app.agents import planner_agent

    user = db.query(User).filter(User.username == "learner_001").one()
    plan = planner_agent.plan_path(db, user_id=user.id, narrate=False)
    kp_ids = {l["kpId"] for l in plan["lessons"]}
    assert set(knowledge_catalog.core_kp_ids(db)) <= kp_ids   # 6 核心主干恒在
    assert 6 <= len(plan["lessons"]) <= 12                     # 裁剪到合理长度，绝不铺满 78
