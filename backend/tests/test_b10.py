"""B10 契约测试：打通「工作流产物 → 学习资源」闭环。

验收口径（执行方案 B10 + 接口文档 11.1 / 8.2）：
- /workflow/execute 支持 kpId + difficulty 入参（难度档非法 → 1001）；
- 工作流 complete 且未降级 → 产物覆盖写入该 kp+难度的讲义缓存（kind=lecture），
  并把本次 workflowId 写入讲义；/resource/lecture 缓存命中返回同一 workflowId；
- 降级（degraded）产物不覆盖缓存：旧讲义与其 workflowId 原样保留，仅留 WorkflowTrace；
- 全程 mock provider（conftest 基线，确定性、零网络）；工作流步进延迟归零提速。

mock 下工作流生成的 markdown 与直出讲义逐字一致（同 _lecture_markdown），故闭环
以 workflowId 一致 + sources 改写为判据，而非 markdown 差异。
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.llm import set_force_critic_low
from app.main import app
from app.models.entities import ResourceCache

API = "/api/v1"


@pytest.fixture(scope="module")
def client():
    original = settings.workflow_step_delay_ms
    settings.workflow_step_delay_ms = 0  # 步进延迟归零，工作流毫秒级跑完
    with TestClient(app) as c:
        yield c
    settings.workflow_step_delay_ms = original


@pytest.fixture(scope="module")
def learner(client) -> dict[str, str]:
    res = client.post(f"{API}/auth/login", json={"username": "learner_001", "password": "123456"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['data']['token']}"}


def _data(res, *, code: int = 0, status: int = 200):
    assert res.status_code == status, f"HTTP {res.status_code}: {res.text[:300]}"
    body = res.json()
    assert body["code"] == code, f"code {body['code']} != {code}: {body['message']}"
    return body["data"]


def _purge_lecture(kp_id: str, difficulty: str) -> None:
    """清掉某 kp+难度的 mock 讲义缓存（kind=lecture），保证走确定性新生成。"""
    db = SessionLocal()
    try:
        db.query(ResourceCache).filter(
            ResourceCache.kp_id == kp_id,
            ResourceCache.difficulty == difficulty,
            ResourceCache.kind == "lecture",
        ).delete()
        db.commit()
    finally:
        db.close()


def _wait_phase_complete(client, learner, wf_id: str, timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    data = None
    while time.time() < deadline:
        data = _data(client.get(f"{API}/workflow/{wf_id}", headers=learner))
        if data["phase"] == "complete":
            return data
        time.sleep(0.05)
    pytest.fail(f"工作流 {wf_id} 超时未达 complete：{data}")


def _lecture(client, learner, kp_id: str, difficulty: str) -> dict:
    return _data(
        client.post(f"{API}/resource/lecture", headers=learner,
                    json={"kpId": kp_id, "difficulty": difficulty})
    )


# ---- ① 入参 difficulty 校验（11.1，B10） ---------------------------------------

def test_execute_difficulty_invalid(client, learner):
    """难度档非法（「地狱」不在讲义五档 入门|初级|中级|高级|精通 内）→ 1001。"""
    _data(client.post(f"{API}/workflow/execute", headers=learner,
                      json={"kpId": "nn", "difficulty": "地狱"}),
          code=1001, status=400)


def test_execute_default_difficulty(client, learner):
    """不传 difficulty → 缺省「初级」，工作流正常起跑并返回 workflowId。"""
    data = _data(client.post(f"{API}/workflow/execute", headers=learner,
                             json={"kpId": "nn"}))
    assert isinstance(data["workflowId"], str) and data["workflowId"].startswith("wf_")
    _wait_phase_complete(client, learner, data["workflowId"])


# ---- ② 闭环：工作流产物覆盖讲义缓存，workflowId 一致 ---------------------------

def test_workflow_writes_back_lecture_cache(client, learner):
    """大屏实时跑完一轮 → 资源页该 kp 讲义 workflowId 与本次工作流一致。"""
    _purge_lecture("nn", "初级")

    # 直出 mock 讲义（未经工作流）→ workflowId 为 null
    before = _lecture(client, learner, "nn", "初级")
    assert before["workflowId"] is None
    assert before["markdown"].startswith("# ")

    # 触发工作流（kp=nn + 难度初级）→ complete
    wf_id = _data(client.post(f"{API}/workflow/execute", headers=learner,
                              json={"kpId": "nn", "difficulty": "初级"}))["workflowId"]
    snap = _wait_phase_complete(client, learner, wf_id)
    # 未降级（critic 非红灯）
    critic = next(a for a in snap["agents"] if a["id"] == "critic")
    assert critic["status"] == "success"

    # 轮询讲义直至缓存被工作流产物覆盖（后台线程 complete 后才提交回写）
    deadline = time.time() + 10
    after = before
    while time.time() < deadline:
        after = _lecture(client, learner, "nn", "初级")
        if after["workflowId"] == wf_id:
            break
        time.sleep(0.05)
    # 闭环判据：讲义 workflowId == 本次工作流 id
    assert after["workflowId"] == wf_id
    # markdown 仍以 # 开头（mock 下与直出逐字一致）；sources 改写为检索映射结果
    assert after["markdown"].startswith("# ")
    assert isinstance(after["sources"], list) and after["sources"]
    for s in after["sources"]:
        assert set(s) == {"title", "type", "confidence"}
        assert s["type"] in {"教材", "论文", "文档", "课程"}
        assert 0 <= s["confidence"] <= 1


# ---- ③ 降级产物不覆盖缓存：旧讲义与 workflowId 保留 ----------------------------

def test_degraded_workflow_keeps_old_cache(client, learner):
    """critic 强制低分 → 工作流降级 → 讲义缓存不被覆盖（旧 workflowId 保留）。"""
    _purge_lecture("nn", "高级")
    before = _lecture(client, learner, "nn", "高级")
    assert before["workflowId"] is None  # 直出 mock，未经工作流

    set_force_critic_low(True)
    try:
        wf_id = _data(client.post(f"{API}/workflow/execute", headers=learner,
                                  json={"kpId": "nn", "difficulty": "高级"}))["workflowId"]
        snap = _wait_phase_complete(client, learner, wf_id)
    finally:
        set_force_critic_low(False)

    # 降级终态：critic 红灯
    critic = next(a for a in snap["agents"] if a["id"] == "critic")
    assert critic["status"] == "error"

    # 讲义缓存未被覆盖：仍为降级前的直出产物，workflowId 仍为 null（非本次 wf_id）
    after = _lecture(client, learner, "nn", "高级")
    assert after["workflowId"] is None
    assert after["markdown"] == before["markdown"]
    assert after["sources"] == before["sources"]
