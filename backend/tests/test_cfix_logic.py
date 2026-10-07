"""本会话三项逻辑修复的回归验证（C-fix）。

- 问题 4（工作流学情诊断接真实用户数据）：诊断节点读当前用户真实画像 + 掌握度，
  生成因人而异的诊断；不同用户 / 不同掌握度 → 诊断 prompt 与输出不同（不再写死）。
- 问题 2（画像统一 + 重做覆盖）：PUT /profile/student-portrait 覆盖写入权威画像，
  GET 回读一致；重做（再次 PUT）整份覆盖、不与旧维度合并；学情概览雷达由该权威画像派生。

阶段测试（问题 1）为纯前端交互（gate 解锁/资源中枢移除测试栏），由 tsc + 人工核对覆盖。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.services import mastery as mastery_service
from app.services import student_portrait as portrait_service
from app.workflows.learning_workflow import run_learning_workflow


@pytest.fixture(scope="module")
def client():
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
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['data']['token']}"}


def _diag_node(trace: dict) -> dict:
    return next(n for n in trace["nodes"] if n["agent"] == "diagnosis")


# ============ 问题 4：诊断因人而异 ============

def test_diagnosis_is_user_specific(db):
    """两个掌握度 / 画像不同的用户跑工作流 → 诊断 prompt 与输出不同。"""
    # 用户 A：画像知识基础偏高（62），已通过 2 个知识点
    portrait_service.replace_portrait(
        db,
        "u_cfix_A",
        [
            {"key": "knowledge_base", "label": "知识基础", "value": "ML 理论扎实", "score": 78, "confidence": 0.8, "source": "dialogue"},
            {"key": "learning_goal", "label": "学习目标", "value": "夯实深度学习", "confidence": 0.9, "source": "dialogue"},
        ],
    )
    mastery_service.set_status(db, "u_cfix_A", "nn", mastery_service.STATUS_PASSED)
    mastery_service.set_status(db, "u_cfix_A", "attention", mastery_service.STATUS_PASSED)

    # 用户 B：画像知识基础偏低（30），全部未通过
    portrait_service.replace_portrait(
        db,
        "u_cfix_B",
        [
            {"key": "knowledge_base", "label": "知识基础", "value": "零基础起步", "score": 30, "confidence": 0.6, "source": "manual"},
            {"key": "learning_goal", "label": "学习目标", "value": "转岗求职", "confidence": 0.8, "source": "manual"},
        ],
    )
    mastery_service.set_status(db, "u_cfix_B", "nn", mastery_service.STATUS_LEARNING)
    mastery_service.set_status(db, "u_cfix_B", "attention", mastery_service.STATUS_LEARNING)
    mastery_service.set_status(db, "u_cfix_B", "transformer", mastery_service.STATUS_LEARNING)

    res_a = run_learning_workflow(db, user_id="u_cfix_A", kp_id="transformer", difficulty="初级")
    res_b = run_learning_workflow(db, user_id="u_cfix_B", kp_id="finetune", difficulty="初级")

    diag_a = _diag_node(res_a["trace"])
    diag_b = _diag_node(res_b["trace"])

    # 1) 诊断节点 prompt 含各自真实画像摘要 + 真实掌握度（不再是写死的通用模板）
    assert "知识基础 78分" in diag_a["promptExcerpt"]
    assert "知识基础 30分" in diag_b["promptExcerpt"]
    # 定性维度（学习目标）取值因人而异地注入 prompt
    assert "夯实深度学习" in diag_a["promptExcerpt"]
    assert "转岗求职" in diag_b["promptExcerpt"]
    # 真实掌握度 JSON 注入（passed / learning 因人而异）
    assert '"nn": "passed"' in diag_a["promptExcerpt"]
    assert '"nn": "learning"' in diag_b["promptExcerpt"]

    # 2) 诊断输出摘要因人而异（薄弱点数量不同）
    assert diag_a["outputSummary"] != diag_b["outputSummary"]

    # 3) 不再出现写死的通用诊断依据
    assert "注意力机制/Transformer/大模型微调维度偏弱" not in diag_a["promptExcerpt"]
    assert "注意力机制/Transformer/大模型微调维度偏弱" not in diag_b["promptExcerpt"]


def test_diagnosis_empty_portrait_still_runs_and_differs_by_mastery(db):
    """Mock 兜底：无画像用户仍可跑，且不同掌握度仍产出不同诊断（占位派生而非全局同一句）。"""
    mastery_service.set_status(db, "u_cfix_empty1", "nn", mastery_service.STATUS_PASSED)
    # u_cfix_empty2 无任何掌握度行
    res1 = run_learning_workflow(db, user_id="u_cfix_empty1", kp_id="attention", difficulty="初级")
    res2 = run_learning_workflow(db, user_id="u_cfix_empty2", kp_id="attention", difficulty="初级")
    d1, d2 = _diag_node(res1["trace"]), _diag_node(res2["trace"])
    # 两者都能跑通（有输出摘要）
    assert d1["outputSummary"] and d2["outputSummary"]
    # 空画像落通用基线占位（可跑），掌握度不同 → prompt 中 masteryStatus 不同
    assert "画像尚未采集" in d1["promptExcerpt"]
    assert '"nn": "passed"' in d1["promptExcerpt"]
    assert '"nn": "passed"' not in d2["promptExcerpt"]


# ============ 问题 2：画像统一 + 重做覆盖 ============

def test_put_student_portrait_overwrites_and_drives_overview(client):
    """简历/手动路径覆盖写入 canonical 画像 → GET 一致；重做整份覆盖；概览雷达由其派生。"""
    headers = _login(client, "learner_001", "123456")
    canon_keys = ["knowledge_base", "cognitive_style", "error_preference", "learning_goal", "prior_experience", "learning_pace"]

    # 首次写入（模拟简历/手动路径 finish 映射出的 6 维 canonical 画像）
    first = {
        "dimensions": [
            {"key": "knowledge_base", "label": "知识基础", "value": "人工智能背景，均值 60", "score": 60, "confidence": 0.7, "source": "manual"},
            {"key": "prior_experience", "label": "先验经验", "value": "硕士 · 人工智能", "confidence": 0.7, "source": "manual"},
            {"key": "learning_goal", "label": "学习目标", "value": "职业培训", "confidence": 0.8, "source": "manual"},
            {"key": "error_preference", "label": "易错点偏好", "value": "大模型微调偏薄弱", "confidence": 0.5, "source": "inferred"},
            {"key": "cognitive_style", "label": "认知风格", "value": "待对话诊断补全", "confidence": 0.4, "source": "inferred"},
            {"key": "learning_pace", "label": "学习节奏", "value": "待对话诊断补全", "confidence": 0.4, "source": "inferred"},
        ]
    }
    res = client.put("/api/v1/profile/student-portrait", headers=headers, json=first)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    # 维度键与 canonical 完全一致（不再是另一套 RADAR_DIMS 知识点）
    assert {d["key"] for d in data["dimensions"]} == set(canon_keys)

    # GET 回读一致
    got = client.get("/api/v1/profile/student-portrait", headers=headers).json()["data"]
    assert {d["key"] for d in got["dimensions"]} == set(canon_keys)
    kb = next(d for d in got["dimensions"] if d["key"] == "knowledge_base")
    assert kb["score"] == 60

    # C2：能力雷达 = 知识点能力分（手动路径 knowledge_base 自陈分以 source=manual 落
    # Mastery 基线、均匀铺到各知识点轴，口径统一）；偏好维改类型标签、不上轴。
    ov = client.get("/api/v1/dashboard/overview", headers=headers).json()["data"]
    from app.core.llm import ABILITY_DIMENSIONS
    assert ov["radar"]["dimensions"] == list(ABILITY_DIMENSIONS)
    assert ov["radar"]["values"] == [60] * 6  # knowledge_base=60 自陈基线铺满能力轴
    assert "知识基础" not in ov["radar"]["dimensions"]  # 偏好/能力概览维不再混进能力轴
    # 认知风格改为偏好类型标签呈现（不在雷达轴）
    assert "cognitive_style" in {p["key"] for p in ov["preferences"]}

    # 重做诊断 = 覆盖更新（再次 PUT，整份覆盖、不与旧维度合并）
    redo = {
        "dimensions": [
            {"key": "knowledge_base", "label": "知识基础", "value": "重做后提升", "score": 85, "confidence": 0.8, "source": "manual"},
            {"key": "learning_goal", "label": "学习目标", "value": "学术研究", "confidence": 0.9, "source": "manual"},
        ]
    }
    res2 = client.put("/api/v1/profile/student-portrait", headers=headers, json=redo)
    assert res2.status_code == 200, res2.text
    after = client.get("/api/v1/profile/student-portrait", headers=headers).json()["data"]
    # 覆盖：仅剩本次 2 维，旧的 4 维已不在（不分叉、不残留）
    assert {d["key"] for d in after["dimensions"]} == {"knowledge_base", "learning_goal"}
    kb2 = next(d for d in after["dimensions"] if d["key"] == "knowledge_base")
    assert kb2["score"] == 85
