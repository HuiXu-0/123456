"""真实学习路径规划 Agent 测试（接口文档 6.2，赛题功能 3）。

验证「真实化」核心断言（区别于此前 mock 写死的固定 6 课）：
1. **画像/掌握度差异 → 路径差异**：两个差异明显的学生生成的路径在顺序/覆盖上不同；
2. **薄弱点优先、已掌握后置**：未掌握点前置，passed 知识点排到路径末段（completed）；
3. **资源精准推送且可点开**：每步 resources 指向该 kpId 真实存在的资源端点；
4. **步骤带明确顺序 + 理由**：sequence 1..N 连续，每步 reason 非空且扣住排程信号；
5. **mock 无密钥可跑通**：全程 mock provider（conftest 基线），确定性、零网络。

为不污染共享种子用户（u_10000/u_10001 被其它用例断言），本测试创建一次性用户
并在 finally 清理；只读资源端点（GET）用于「可点开」证明，不写共享用户态。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agents.planner_agent import plan_path
from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import (
    Journey,
    Mastery,
    StudentPortrait,
    User,
)
from app.services import mastery as mastery_service
from app.services import student_portrait as portrait_service

API = "/api/v1"
_LESSON_CORE = {"sequence", "topic", "difficulty", "status", "progress", "description"}
_KP_IDS = {"ml", "nn", "dl", "cnn", "transformer", "finetune"}

# 两个差异明显的画像 / 掌握度场景
_USER_ZERO = "u_test_plan_zero"   # 零基础 + 目标「大模型应用」+ 全未掌握
_USER_PRIOR = "u_test_plan_prior"  # 有先验（基础扎实）+ ml/nn 已掌握 + 无目标岗位


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # 触发 lifespan 建表+种子
        yield c


@pytest.fixture(scope="module")
def two_profiles():
    """构造两个差异明显的学生（画像 + 掌握度 + 目标岗位），用后清理。"""
    db = SessionLocal()
    try:
        for uid in (_USER_ZERO, _USER_PRIOR):
            if db.get(User, uid) is None:
                db.add(User(id=uid, username=uid, display_name=uid, password_hash="x"))
        db.commit()

        # 零基础学生：knowledge_base 薄弱(score=22) + 目标大模型应用工程师
        portrait_service.apply_updates(
            db,
            _USER_ZERO,
            [
                {"key": "knowledge_base", "label": "知识基础", "value": "薄弱",
                 "score": 22, "confidence": 0.8, "source": "dialogue"},
                {"key": "learning_goal", "label": "学习目标", "value": "转大模型应用方向",
                 "confidence": 0.9, "source": "dialogue"},
            ],
        )
        jz = db.get(Journey, _USER_ZERO) or Journey(user_id=_USER_ZERO)
        jz.target_job_name = "大模型应用工程师"
        jz.has_diagnosed = True
        db.add(jz)
        # 全部未掌握（不写 Mastery 行 = 未开始）

        # 有先验学生：knowledge_base 扎实(score=88) + ml/nn 已通过、dl 学习中
        portrait_service.apply_updates(
            db,
            _USER_PRIOR,
            [
                {"key": "knowledge_base", "label": "知识基础", "value": "扎实",
                 "score": 88, "confidence": 0.8, "source": "dialogue"},
                {"key": "prior_experience", "label": "先验经验", "value": "有工程实践",
                 "confidence": 0.8, "source": "dialogue"},
            ],
        )
        jp = db.get(Journey, _USER_PRIOR) or Journey(user_id=_USER_PRIOR)
        jp.has_diagnosed = True
        db.add(jp)
        mastery_service.set_status(db, _USER_PRIOR, "ml", mastery_service.STATUS_PASSED)
        mastery_service.set_status(db, _USER_PRIOR, "nn", mastery_service.STATUS_PASSED)
        mastery_service.set_status(db, _USER_PRIOR, "dl", mastery_service.STATUS_LEARNING)
        db.commit()
        yield db
    finally:
        # 清理一次性用户的所有派生行，避免污染共享会话库
        for uid in (_USER_ZERO, _USER_PRIOR):
            db.query(Mastery).filter(Mastery.user_id == uid).delete()
            row = db.get(StudentPortrait, uid)
            if row is not None:
                db.delete(row)
            jr = db.get(Journey, uid)
            if jr is not None:
                db.delete(jr)
            u = db.get(User, uid)
            if u is not None:
                db.delete(u)
        db.commit()
        db.close()


def _topics(plan: dict) -> list[str]:
    return [s["topic"] for s in plan["lessons"]]


def test_plan_shape_and_ordering(two_profiles):
    """每步契约六字段 + additive(kpId/reason/resources/estimatedMinutes)；sequence 连续；理由非空。

    会话三：_USER_ZERO 目标「大模型应用」→ 在 78 点树上按 LLM 板块聚焦细化，路径长于 6、
    裁剪到合理长度（≤ 12）；6 核心仍作为主干包含在内。
    """
    db = two_profiles
    plan = plan_path(db, user_id=_USER_ZERO, target_job_id="llm-app")
    lessons = plan["lessons"]
    assert 6 <= len(lessons) <= 12                              # 目标聚焦 → 合理长度
    assert [l["sequence"] for l in lessons] == list(range(1, len(lessons) + 1))
    assert _KP_IDS <= {l["kpId"] for l in lessons}             # 6 核心主干仍在路径内
    for l in lessons:
        assert _LESSON_CORE <= set(l)               # 契约六字段齐全（向后兼容）
        assert l["status"] in {"completed", "in_progress", "pending"}
        assert 0 <= l["progress"] <= 100
        # additive：每步有 kpId / 非空 reason / 非空 resources（验证项 3、4）
        assert isinstance(l["kpId"], str) and l["kpId"].strip()
        assert isinstance(l["reason"], str) and l["reason"].strip()
        assert l["resources"] and all(r["kpId"] == l["kpId"] for r in l["resources"])
        # 会话三·时间线：每步预计时长（分钟，按层级/难度估算，约束 45~240）
        assert 45 <= l["estimatedMinutes"] <= 240


def test_two_profiles_yield_different_paths(two_profiles):
    """验证项 1：差异明显的画像/掌握度 → 顺序/覆盖不同（区别于写死固定路径）。"""
    db = two_profiles
    plan_zero = plan_path(db, user_id=_USER_ZERO, target_job_id="llm-app")
    plan_prior = plan_path(db, user_id=_USER_PRIOR)
    assert _topics(plan_zero) != _topics(plan_prior), "不同学生应生成不同顺序的路径"

    # 零基础：基础课先行（ml/nn 在前两步），难度被下调（首步入门级）
    zt = _topics(plan_zero)
    assert zt.index("机器学习基础") < zt.index("Transformer架构")
    assert plan_zero["lessons"][0]["difficulty"] in {"入门", "初级"}

    # 有先验：扎实基础 → 难度被上调（同一基础课难度更高），体现画像联动
    assert plan_prior["lessons"][0]["difficulty"] in {"初级", "中级", "高级", "精通"}


def test_weak_first_mastered_deferred(two_profiles):
    """验证项 2：薄弱点优先、已掌握后置，与 Mastery 真实联动。"""
    db = two_profiles
    plan = plan_path(db, user_id=_USER_PRIOR)
    lessons = plan["lessons"]
    seq_by_topic = {l["topic"]: l["sequence"] for l in lessons}

    # ml/nn 已通过 → status=completed 且排到所有未掌握(pending)步骤之后（后置复习）
    mastered_seqs = [l["sequence"] for l in lessons if l["status"] == "completed"]
    pending_seqs = [l["sequence"] for l in lessons if l["status"] == "pending"]
    assert {"机器学习基础", "神经网络基础"} <= {
        l["topic"] for l in lessons if l["status"] == "completed"
    }
    assert min(mastered_seqs) > max(pending_seqs), "已掌握点应排在未掌握点之后"
    # 薄弱点（未掌握）应抢占第 1 步，而非已掌握的 ml
    assert seq_by_topic["机器学习基础"] > 1

    # 理由层（经 LLMClient mock）扣住信号：已掌握点理由含「已掌握」，薄弱点含「薄弱/基础」
    ml_reason = next(l["reason"] for l in lessons if l["topic"] == "机器学习基础")
    assert "已掌握" in ml_reason
    weak_reason = " ".join(l["reason"] for l in lessons if l["status"] != "completed")
    assert ("薄弱" in weak_reason) or ("基础" in weak_reason)


def test_target_job_influences_path(two_profiles):
    """目标岗位真实参与规划：同一学生换目标岗位 → 重点强化项（理由）随岗位需求变化。

    先修依赖是硬约束（线性课程不因岗位破坏先修），故岗位需求体现为「重点强化项」
    标注：llm-app 对 Transformer/微调需求高(92/88)→ 标重点；data-analyst 需求低
    (15/10)→ 不标。两者的重点强化集合应不同（区别于与岗位无关的写死路径）。
    """
    db = two_profiles
    plan_llm = plan_path(db, user_id=_USER_ZERO, target_job_id="llm-app")
    plan_da = plan_path(db, user_id=_USER_ZERO, target_job_id="data-analyst")

    def emphasized(plan: dict) -> set[str]:
        return {l["topic"] for l in plan["lessons"] if "重点强化项" in l["reason"]}

    emp_llm, emp_da = emphasized(plan_llm), emphasized(plan_da)
    assert emp_llm != emp_da, "切换目标岗位应改变重点强化的知识点集合"
    # llm-app 应把 Transformer/微调列为重点（岗位需求 ≥80），data-analyst 不应
    assert {"Transformer架构", "大模型微调技术"} & emp_llm
    assert not ({"Transformer架构", "大模型微调技术"} & emp_da)


def test_resources_openable(client, two_profiles):
    """验证项 3：每步推送的资源指向真实存在、可点开的生成内容（只读端点证明）。"""
    db = two_profiles
    res = client.post(f"{API}/auth/login",
                      json={"username": "learner_001", "password": "123456"})
    headers = {"Authorization": f"Bearer {res.json()['data']['token']}"}

    plan = plan_path(db, user_id=_USER_ZERO, target_job_id="llm-app")
    for step in plan["lessons"]:
        kp = step["kpId"]
        # 思维导图（8.4）：任意知识点（含 78 点目录点）按需生成真实 Markdown（可点开）
        mind = client.get(f"{API}/resource/mindmap/{kp}", headers=headers)
        assert mind.status_code == 200 and mind.json()["data"]["markdown"]
        # 题库（9.1）/ 外部精选（8.6）为种子内容，仅 6 已验证核心点有；目录点按需生成其它形式
        if kp in _KP_IDS:
            quiz = client.get(f"{API}/quiz/{kp}", headers=headers)
            assert quiz.status_code == 200 and quiz.json()["data"]["questions"]
            ext = client.get(f"{API}/resource/external/{kp}", headers=headers)
            assert ext.status_code == 200 and ext.json()["data"]


# ---- C2：per-KP 能力分驱动顺序 + 偏好驱动资源形式 ------------------------------

_USER_C2 = "u_test_plan_c2"


def test_per_kp_ability_orders_and_preference_drives_resources():
    """C2 验证：① per-KP 能力分决定顺序（达标后置、薄弱优先）；② 偏好决定每步推荐资源形式。"""
    db = SessionLocal()
    try:
        if db.get(User, _USER_C2) is None:
            db.add(User(id=_USER_C2, username=_USER_C2, display_name=_USER_C2, password_hash="x"))
            db.commit()
        # 画像：基础好 + 图像型(visual) + 快速概览(overview)；微测能力分：基础强、进阶弱
        portrait_service.replace_portrait(db, _USER_C2, [
            {"key": "knowledge_base", "label": "知识基础", "value": "扎实", "score": 78, "source": "diagnostic"},
            {"key": "cognitive_style", "label": "认知风格", "value": "图像型", "optionKey": "visual", "source": "dialogue"},
            {"key": "learning_pace", "label": "学习节奏", "value": "快速概览型", "optionKey": "overview", "source": "dialogue"},
        ])
        for kp, sc in {"ml": 85, "nn": 82, "dl": 78, "cnn": 40, "transformer": 30, "finetune": 25}.items():
            mastery_service.set_baseline(db, _USER_C2, kp, score=sc, confidence=0.45)
        jp = db.get(Journey, _USER_C2) or Journey(user_id=_USER_C2)
        jp.has_diagnosed = True
        db.add(jp)
        db.commit()

        plan = plan_path(db, user_id=_USER_C2)
        order = [l["kpId"] for l in plan["lessons"]]
        # ① 能力达标(≥70：ml/nn/dl)后置、薄弱(cnn/transformer/finetune)优先 → 进阶弱点排在基础强点之前
        assert order.index("cnn") < order.index("ml"), "薄弱进阶点应排在已达标基础点之前（靠测）"
        assert order.index("transformer") < order.index("nn")
        # ② 每步默认推荐资源形式因偏好而变：图像型 → 首推「知识图解」diagram
        for l in plan["lessons"]:
            rec = next((r for r in l["resources"] if r.get("recommended")), None)
            assert rec is not None and rec["kind"] == "diagram", "图像型应默认推图解"
            assert rec.get("recommendReason")
        # 不同偏好 → 不同推荐形式（改成文字型 → 首推讲义）
        portrait_service.replace_portrait(db, _USER_C2, [
            {"key": "knowledge_base", "label": "知识基础", "value": "扎实", "score": 78, "source": "diagnostic"},
            {"key": "cognitive_style", "label": "认知风格", "value": "文字型", "optionKey": "textual", "source": "dialogue"},
        ])
        plan2 = plan_path(db, user_id=_USER_C2)
        rec2 = next(r for r in plan2["lessons"][0]["resources"] if r.get("recommended"))
        assert rec2["kind"] == "lecture", "文字型应默认推详细讲义"
    finally:
        db.query(Mastery).filter(Mastery.user_id == _USER_C2).delete()
        for row in (db.get(StudentPortrait, _USER_C2), db.get(Journey, _USER_C2), db.get(User, _USER_C2)):
            if row is not None:
                db.delete(row)
        db.commit()
        db.close()


def test_fresh_and_diagnostic_users_have_no_completed_nodes(client):
    """紧急修复回归：零基础新用户 / 仅做过微测的用户 → 路径无任何「已完成」节点。

    ① 全新未诊断用户 → 全部 pending（不再沿用种子表写死的 completed/in_progress）；
    ② 做过微测（写诊断基线）但未真实学习 → 仍全部 pending（微测≠学完，二者解耦）；
    ③ 真实通过某知识点阶段测试 → 仅该节点 completed，其余仍未完成。
    """
    import uuid

    username = f"fresh_{uuid.uuid4().hex[:8]}"
    reg = client.post(f"{API}/auth/register", json={"username": username, "password": "123456"})
    headers = {"Authorization": f"Bearer {reg.json()['data']['token']}"}
    uid = reg.json()["data"]["user"]["userId"]

    def statuses() -> list[str]:
        data = client.get(f"{API}/learning-path", headers=headers).json()["data"]
        return [l["status"] for l in data["lessons"]]

    # ① 全新未诊断 → 全部 pending（绝无 completed）
    assert statuses() == ["pending"] * 6, "零基础新用户不应出现已完成/进行中节点"

    # ② 写诊断微测基线（status=learning + source=diagnostic）+ 标记已诊断 → 仍全 pending
    db = SessionLocal()
    try:
        for kp in ("ml", "nn", "dl", "cnn", "transformer", "finetune"):
            mastery_service.set_baseline(db, uid, kp, score=80, confidence=0.45)  # 即便答得好
        j = db.get(Journey, uid) or Journey(user_id=uid)
        j.has_diagnosed = True
        db.add(j)
        db.commit()
        assert "completed" not in statuses(), "微测基线不应使节点变已完成（微测≠学完）"
        assert "in_progress" not in statuses(), "微测基线不应使节点变进行中（未真实学习）"

        # ③ 真实通过 nn 阶段测试 → 仅 nn completed
        mastery_service.set_score(db, uid, "nn", score=100)
        mastery_service.mark_pass(db, uid, "nn")
        db.commit()
        data = client.get(f"{API}/learning-path", headers=headers).json()["data"]
        by_topic = {l["topic"]: l["status"] for l in data["lessons"]}
        assert by_topic["神经网络基础"] == "completed"
        assert [t for t, s in by_topic.items() if s == "completed"] == ["神经网络基础"], "只有真实通过的节点才已完成"
    finally:
        db.query(Mastery).filter(Mastery.user_id == uid).delete()
        for row in (db.get(StudentPortrait, uid), db.get(Journey, uid), db.get(User, uid)):
            if row is not None:
                db.delete(row)
        db.commit()
        db.close()


def test_get_path_recomputes_when_portrait_changes(client):
    """C2 验证：画像变 → GET /learning-path 路径相应重算（缓存指纹失效，不写死）。"""
    import uuid

    username = f"plan_{uuid.uuid4().hex[:8]}"
    reg = client.post(f"{API}/auth/register", json={"username": username, "password": "123456"})
    headers = {"Authorization": f"Bearer {reg.json()['data']['token']}"}
    uid = reg.json()["data"]["user"]["userId"]

    db = SessionLocal()
    try:
        # 画像 A：零基础（全弱）+ 已诊断
        portrait_service.replace_portrait(db, uid, [
            {"key": "knowledge_base", "label": "知识基础", "value": "薄弱", "score": 20, "source": "diagnostic"},
        ])
        for kp, sc in {"ml": 22, "nn": 20, "dl": 18, "cnn": 16, "transformer": 14, "finetune": 12}.items():
            mastery_service.set_baseline(db, uid, kp, score=sc, confidence=0.45)
        j = db.get(Journey, uid) or Journey(user_id=uid)
        j.has_diagnosed = True
        db.add(j)
        db.commit()

        order_a = [l["topic"] for l in client.get(f"{API}/learning-path", headers=headers).json()["data"]["lessons"]]
        diff_a = client.get(f"{API}/learning-path", headers=headers).json()["data"]["lessons"][0]["difficulty"]

        # 画像 B：重做诊断改为「基础好 + 基础强/进阶弱」→ 顺序应改变（不再写死）
        portrait_service.replace_portrait(db, uid, [
            {"key": "knowledge_base", "label": "知识基础", "value": "扎实", "score": 82, "source": "diagnostic"},
        ])
        for kp, sc in {"ml": 88, "nn": 85, "dl": 80, "cnn": 38, "transformer": 30, "finetune": 25}.items():
            mastery_service.set_baseline(db, uid, kp, score=sc, confidence=0.45)
        db.commit()

        lessons_b = client.get(f"{API}/learning-path", headers=headers).json()["data"]["lessons"]
        order_b = [l["topic"] for l in lessons_b]
        assert order_a != order_b, "画像变 → 路径顺序应相应改变（不缓存写死）"
        # B：基础强点(机器学习基础)后置到薄弱进阶点(CNN/Transformer)之后
        assert order_b.index("CNN架构") < order_b.index("机器学习基础")
    finally:
        db.query(Mastery).filter(Mastery.user_id == uid).delete()
        for row in (db.get(StudentPortrait, uid), db.get(Journey, uid), db.get(User, uid)):
            if row is not None:
                db.delete(row)
        db.commit()
        db.close()


# ---- 会话三：在 78 点树上按目标聚焦裁剪 + 时间线（周期/时长/进度/节奏） -----------

def test_goal_focus_widens_over_78_tree(two_profiles):
    """在更大知识树上规划：有目标 → 引入目标板块细化点、路径长于 6；无目标 → 仅 6 核心。"""
    db = two_profiles
    # _USER_ZERO 目标「大模型应用」→ 聚焦 LLM 板块 → 纳入非核心 LLM 细化点
    plan_goal = plan_path(db, user_id=_USER_ZERO, target_job_id="llm-app")
    kp_ids = [l["kpId"] for l in plan_goal["lessons"]]
    assert len(kp_ids) > 6, "有明确目标 → 应在 78 点树上细化，路径长于 6 核心"
    assert len(kp_ids) <= 12, "路径裁剪到合理长度（≤ 12），不铺满 78 点"
    non_core_llm = [k for k in kp_ids if k not in _KP_IDS and k.startswith("LLM-")]
    assert non_core_llm, "目标聚焦 LLM → 路径应含 LLM 板块细化点（非核心）"
    assert len(kp_ids) == len(set(kp_ids)), "路径知识点不重复"

    # _USER_PRIOR 无学习目标 / 无目标岗位 → 回落 6 核心主干（零回归、路径仍 6 步）
    plan_none = plan_path(db, user_id=_USER_PRIOR)
    none_ids = {l["kpId"] for l in plan_none["lessons"]}
    assert len(plan_none["lessons"]) == 6 and none_ids == _KP_IDS


def test_timeline_fields_present(two_profiles, client):
    """时间线：每点预计时长 + 整条路径周期/总时长/进度百分比/节奏建议齐全、自洽。"""
    db = two_profiles
    # per-KP：入门较短、前沿较长（层级驱动，估算合理）
    plan = plan_path(db, user_id=_USER_ZERO, target_job_id="llm-app")
    mins = {l["kpId"]: l["estimatedMinutes"] for l in plan["lessons"]}
    assert all(45 <= m <= 240 for m in mins.values())
    assert mins["ml"] < mins["finetune"], "入门(机器学习概述) 应短于 前沿(参数高效微调)"

    # 整条路径 timeline（经 GET 端点聚合，画像含节奏偏好 → 影响每日节奏）
    res = client.post(f"{API}/auth/login", json={"username": "learner_001", "password": "123456"})
    headers = {"Authorization": f"Bearer {res.json()['data']['token']}"}
    summary = client.get(f"{API}/learning-path", headers=headers).json()["data"]["summary"]
    tl = summary["timeline"]
    for key in ("totalCount", "learnedCount", "totalMinutes", "totalHours",
                "spentMinutes", "completionPct", "pacePerDay", "cycleDays",
                "cycleWeeks", "pacing"):
        assert key in tl, f"timeline 缺字段 {key}"
    lessons = client.get(f"{API}/learning-path", headers=headers).json()["data"]["lessons"]
    assert tl["totalCount"] == len(lessons)
    assert tl["totalMinutes"] == sum(l["estimatedMinutes"] for l in lessons)
    assert 0 <= tl["completionPct"] <= 100
    assert tl["cycleWeeks"] >= 1 and isinstance(tl["pacing"], str) and tl["pacing"]
