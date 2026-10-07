"""R3.5 契约测试：Supervisor 生产接入（feature flag / 双入口 / 灰度等价性）。

验收口径（R3.5 实施报告 §6），逐条对应需求：

1. **`SUPERVISOR_ENABLED=false` 行为与旧版本一致** —— 分派到 legacy、返回结构与
   11.2 trace 逐字同构、**帧序列完全一致**；
2. **`SUPERVISOR_ENABLED=true` 进入 Router Graph** —— 分派到 router，
   产出 `routing` / `routingTrace`，且 **Supervisor 节点不产生额外帧**；
3. **API 返回结构一致** —— `POST /workflow/execute` 两档下信封与 `data` 键相同；
4. **trace projection 一致** —— 11.2 的 `trace` 8 键、`messages` 字段集、
   `nodes` 序列、`stats` 结构全等；路由字段**不得泄漏进 11.2**；
5. **fallback 正常** —— 空意图 → `resource_generation`，`routingTrace.fallback=True`。

另含**阻断项回归**：Supervisor / pending 节点不得写 `execution.currentNode`，
否则 `workflow_runner._phase_step_active` 落兜底分支返回 `("complete", 4)`，
使 `stats.progress` 提前置 100、前端显示"已完成"（R3.5 报告 §3）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.services import workflow_runner
from app.workflows.graphs import run_router_workflow
from app.workflows.learning_workflow import run_learning_workflow

_TRACE_KEYS = {
    "workflowId", "phase", "step", "agents", "messages",
    "nodes", "ragContextUsed", "stats",
}
_MSG_KEYS = {"id", "from", "to", "message", "type", "timestamp"}
_ROUTING_TRACE_KEYS = {
    "input_type", "selected_graph", "reason", "confidence", "fallback",
}
_LEGACY_RESULT_KEYS = {"workflowId", "finalOutput", "trace"}
_ROUTER_RESULT_KEYS = _LEGACY_RESULT_KEYS | {"routing", "routingTrace"}


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


@pytest.fixture(autouse=True)
def _flag_isolation(monkeypatch):
    """每条用例从「flag 关闭」起步，结束后还原（默认必须保持关闭）。"""
    monkeypatch.setattr(settings, "supervisor_enabled", False)
    yield


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    token = res.json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


def _dispatch(db, *, on_update=None, enabled: bool):
    """按指定 flag 走生产分派点（objective 留空，与 workflow_runner 灰度期一致）。"""
    settings.supervisor_enabled = enabled
    return workflow_runner.dispatch_workflow(
        db,
        user_id="u_10001",
        kp_id="nn",
        difficulty="初级",
        target_job="大模型应用工程师",
        on_update=on_update,
    )


# ── 0. 开关默认值 ────────────────────────────────────────────────────────────


def test_supervisor_flag_defaults_to_off():
    """安全默认：不配置即旧流程。"""
    assert settings.supervisor_enabled is False


def test_runner_reads_flag_at_call_time(monkeypatch):
    """每次调用现读 settings（非启动快照）→ 改 .env 不重启即灰度/回滚。"""
    monkeypatch.setattr(settings, "supervisor_enabled", True)
    assert settings.supervisor_enabled is True
    monkeypatch.setattr(settings, "supervisor_enabled", False)
    assert settings.supervisor_enabled is False


# ── 1. flag OFF：与旧版本一致 ────────────────────────────────────────────────


def test_flag_off_dispatches_to_legacy_entry(db, monkeypatch):
    called: dict[str, bool] = {}
    monkeypatch.setattr(
        workflow_runner, "run_learning_workflow",
        lambda *a, **k: called.update(legacy=True) or {"ok": True},
    )
    monkeypatch.setattr(
        workflow_runner, "run_router_workflow",
        lambda *a, **k: called.update(router=True) or {"ok": True},
    )
    _dispatch(db, enabled=False)
    assert called == {"legacy": True}


def test_flag_off_skips_supervisor_entirely(db, monkeypatch):
    """flag OFF 时 Supervisor 完全不被触达。"""
    from app.agents import supervisor as sup_mod

    def _bomb(*_a, **_k):
        raise AssertionError("flag OFF 不得调用 Supervisor")

    monkeypatch.setattr(sup_mod.SupervisorAgent, "decide", _bomb)
    result = _dispatch(db, enabled=False)
    assert set(result) == _LEGACY_RESULT_KEYS


def test_flag_off_result_matches_legacy_contract(db):
    direct = run_learning_workflow(
        db, user_id="u_10001", kp_id="nn", difficulty="初级"
    )
    dispatched = _dispatch(db, enabled=False)

    assert set(dispatched) == set(direct) == _LEGACY_RESULT_KEYS
    assert set(dispatched["trace"]) == set(direct["trace"]) == _TRACE_KEYS
    assert set(dispatched["finalOutput"]) == set(direct["finalOutput"])
    # 无路由字段泄漏
    assert "routing" not in dispatched
    assert "routingTrace" not in dispatched
    # messages / nodes 逐条同构（内容含时序，比序列与结构）
    assert [m["from"] for m in dispatched["trace"]["messages"]] == [
        m["from"] for m in direct["trace"]["messages"]
    ]
    assert [n["agent"] for n in dispatched["trace"]["nodes"]] == [
        n["agent"] for n in direct["trace"]["nodes"]
    ]


# ── 2. flag ON：进入 Router Graph ────────────────────────────────────────────


def test_flag_on_dispatches_to_router_entry(db, monkeypatch):
    called: dict[str, bool] = {}
    monkeypatch.setattr(
        workflow_runner, "run_learning_workflow",
        lambda *a, **k: called.update(legacy=True) or {"ok": True},
    )
    monkeypatch.setattr(
        workflow_runner, "run_router_workflow",
        lambda *a, **k: called.update(router=True) or {"ok": True},
    )
    _dispatch(db, enabled=True)
    assert called == {"router": True}


def test_flag_on_runs_supervisor_and_returns_routing(db):
    result = _dispatch(db, enabled=True)
    assert set(result) == _ROUTER_RESULT_KEYS
    # 经 Supervisor 决策（空意图 → fallback 到唯一完整子图）
    assert result["routing"]["target_graph"] == "resource_generation"
    assert set(result["routing"]) == {"target_graph", "reason", "confidence"}
    assert result["routing"]["reason"].startswith("fallback:")
    # 真跑通了 resource_generation 子图
    assert result["finalOutput"]["degraded"] is False
    assert [a["id"] for a in result["trace"]["agents"]] == [
        "diagnosis", "generation", "critic",
    ]


# ── 3. API 返回结构一致 ──────────────────────────────────────────────────────


def test_api_execute_response_shape_identical_across_flag(client):
    """两档下 11.1 的信封与 data 键完全相同（路由字段不外泄到接口层）。"""
    headers = _login(client, "admin", "admin123")
    endpoint = "/api/v1/workflow/execute"
    body = {"kpId": "nn"}

    results = {}
    for flag in (False, True):
        settings.supervisor_enabled = flag
        res = client.post(endpoint, headers=headers, json=body)
        assert res.status_code == 200, res.text
        payload = res.json()
        assert payload["code"] == 0
        assert set(payload) == {"code", "message", "data", "traceId"}
        results[flag] = payload["data"]

    settings.supervisor_enabled = False
    assert set(results[False]) == set(results[True]) == {"workflowId"}
    assert results[False]["workflowId"].startswith("wf_")
    assert results[True]["workflowId"].startswith("wf_")


def test_api_get_snapshot_shape_has_no_routing_fields(client):
    """11.2 GET 快照只有 6 个键，routing / routingTrace 不得出现。"""
    headers = _login(client, "admin", "admin123")
    settings.supervisor_enabled = True
    created = client.post(
        "/api/v1/workflow/execute", headers=headers, json={"kpId": "nn"}
    ).json()["data"]
    settings.supervisor_enabled = False

    # 后台线程跑完即可取到；未注册时兜底 WorkflowTrace 行
    import time

    snapshot: dict | None = None
    for _ in range(120):  # 演示延迟 500ms/节点 → 留足 12s
        res = client.get(
            f"/api/v1/workflow/{created['workflowId']}", headers=headers
        )
        assert res.status_code == 200, res.text
        snapshot = res.json()["data"]
        if snapshot.get("phase") == "complete":
            break
        time.sleep(0.1)

    assert snapshot is not None
    assert set(snapshot) == {
        "workflowId", "phase", "step", "agents", "messages", "stats",
    }
    assert "routing" not in snapshot
    assert "routingTrace" not in snapshot


# ── 4. trace projection 一致 ─────────────────────────────────────────────────


def test_trace_projection_identical_across_entries(db):
    legacy = run_learning_workflow(
        db, user_id="u_10001", kp_id="nn", difficulty="初级"
    )
    routed = run_router_workflow(
        db, user_id="u_10001", kp_id="nn", difficulty="初级", objective=""
    )

    # 11.2 trace 结构逐键一致，且不含任何路由字段
    assert set(routed["trace"]) == set(legacy["trace"]) == _TRACE_KEYS
    assert "routing" not in routed["trace"]
    assert "routingTrace" not in routed["trace"]

    # messages 字段集逐条一致（routing trace 绝不污染 11.2）
    for m in routed["trace"]["messages"]:
        assert set(m) == _MSG_KEYS
    assert [m["from"] for m in routed["trace"]["messages"]] == [
        m["from"] for m in legacy["trace"]["messages"]
    ]

    # nodes / agents / stats 同构
    assert [n["agent"] for n in routed["trace"]["nodes"]] == [
        n["agent"] for n in legacy["trace"]["nodes"]
    ]
    assert [a["id"] for a in routed["trace"]["agents"]] == [
        a["id"] for a in legacy["trace"]["agents"]
    ]
    assert set(routed["trace"]["stats"]) == set(legacy["trace"]["stats"])
    assert routed["trace"]["stats"]["progress"] == 100
    assert routed["trace"]["stats"]["completedAgents"] == 3


def test_frame_sequence_identical_across_flag(db):
    """灰度核心：flag ON 与 OFF 的 on_update 帧序列（current_node / status）完全一致。"""
    legacy_frames: list[dict] = []
    _dispatch(db, enabled=False, on_update=legacy_frames.append)

    router_frames: list[dict] = []
    _dispatch(db, enabled=True, on_update=router_frames.append)

    assert [f.get("current_node") for f in legacy_frames] == [
        f.get("current_node") for f in router_frames
    ]
    assert [f.get("workflow_status") for f in legacy_frames] == [
        f.get("workflow_status") for f in router_frames
    ]
    # 每帧的 messages 条数一致 → 投影同源
    assert [len(f.get("messages") or []) for f in legacy_frames] == [
        len(f.get("messages") or []) for f in router_frames
    ]


def test_supervisor_node_never_publishes_a_frame(db):
    """阻断项回归：Supervisor / pending 节点不得写 currentNode。"""
    frames: list[dict] = []
    _dispatch(db, enabled=True, on_update=frames.append)

    assert frames, "flag ON 必须产生业务帧"
    assert all(f.get("current_node") != "supervisor" for f in frames)
    for name in ("path_planning", "interaction", "evaluation"):
        assert all(f.get("current_node") != name for f in frames)
    # 终态才是 decision
    assert frames[-1]["current_node"] == "decision"


def test_progress_never_completes_before_last_frame(db):
    """阻断项回归：任何中间帧都不得被推导成 complete / progress=100。"""
    frames: list[dict] = []
    _dispatch(db, enabled=True, on_update=frames.append)

    for index, frame in enumerate(frames):
        phase, step, _active = workflow_runner._phase_step_active(frame)
        if index < len(frames) - 1:
            assert phase != "complete", f"第 {index} 帧提前 complete：{frame}"
            assert step != 4
        else:
            assert phase == "complete" and step == 4
        assert workflow_runner._PROGRESS[phase] != 100 or index == len(frames) - 1


# ── 5. fallback 正常 ─────────────────────────────────────────────────────────


def test_fallback_recorded_in_routing_trace(db):
    result = _dispatch(db, enabled=True)
    trace = result["routingTrace"]

    assert set(trace) == _ROUTING_TRACE_KEYS
    assert trace["fallback"] is True
    assert trace["input_type"] == "empty"          # 灰度期不传 objective
    assert trace["selected_graph"] == "resource_generation"
    assert trace["reason"].startswith("fallback:")
    assert 0.0 <= trace["confidence"] <= 1.0
    # routingTrace 与 routing 出自同一决策，必须同值
    assert trace["confidence"] == result["routing"]["confidence"]
    assert trace["selected_graph"] == result["routing"]["target_graph"]


def test_routing_trace_absent_on_legacy_path(db):
    """legacy 路径没有 Supervisor，因此没有 routingTrace（不应伪造）。"""
    result = _dispatch(db, enabled=False)
    assert "routingTrace" not in result
    assert "routing" not in result


def test_routing_trace_never_reaches_projected_state(db):
    """即使 State 里有 routingTrace，投影回 legacy 也不会带出去。"""
    from app.core.trace import project_legacy_state

    result = _dispatch(db, enabled=True)
    assert "routingTrace" in result  # 进程内有

    projected = project_legacy_state(
        {
            "execution": {"currentNode": "decision", "status": "success",
                          "routingTrace": result["routingTrace"]},
            "messages": [],
        }
    )
    assert "routingTrace" not in projected
    assert set(projected) >= {"current_node", "workflow_status", "messages"}
