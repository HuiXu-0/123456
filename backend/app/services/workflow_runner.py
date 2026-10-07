"""工作流实时运行器（B7-a，接口文档 11 章）。

职责：
- POST /workflow/execute → start_workflow()：后台线程跑 B5 LangGraph 真实工作流，
  立即返回预分配 workflowId；
- 每个节点完成（含重试轮）经 run_learning_workflow 的 on_update 回调组装一帧
  11.2 快照（workflowId/phase/step/agents[]/messages[]/stats，严格无多余键），
  发布给 GET 轮询（最新快照）与 WS 订阅者（asyncio.Queue，跨线程经
  loop.call_soon_threadsafe 投递）；
- 运行结束后内存注册表保留最近 _MAX_RUNS 条；淘汰/重启后 GET 兜底消费
  WorkflowTrace 持久化行（B5-a 即按 11.2 渲染结构落库）。

轻量栈：进程内 OrderedDict 注册表 + threading（CLAUDE.md 内存取向）；
生产替换 Redis pub/sub + 任务队列，路径写 README。
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core import model_registry
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import WorkflowTrace
from app.workflows.graphs.router import run_router_workflow
from app.workflows.learning_workflow import (
    AGENT_ORDER,
    Retriever,
    WorkflowState,
    run_learning_workflow,
)

logger = logging.getLogger(__name__)

# 11.2 固定 3 Agent 的缺省展示名（与 PromptTemplate 种子逐字一致；
# 节点已执行时以 node_log 中的实际 name 为准——消费 B4-b 热更新）
_AGENT_NAMES = {
    "diagnosis": "学情诊断Agent",
    "generation": "领域知识生成Agent",
    "critic": "内容审核校验Agent",
}
_IDLE_ACTION = {
    "diagnosis": "等待诊断任务",
    "generation": "等待生成任务",
    "critic": "等待校验任务",
}
_RUNNING_ACTION = {
    "diagnosis": "正在分析学习者画像...",
    "generation": "正在生成学习资源...",
    "critic": "正在校验内容质量...",
}
# phase → progress %（11.2 stats.progress）
_PROGRESS = {"idle": 0, "diagnosis": 25, "generation": 50, "validation": 75, "complete": 100}

# 内存注册表：workflowId → WorkflowRun（FIFO 淘汰，淘汰后 GET 兜底 WorkflowTrace 行）
_runs: "OrderedDict[str, WorkflowRun]" = OrderedDict()
_MAX_RUNS = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- 11.2 快照组装 -------------------------------------------------------------

def _phase_step_active(state: WorkflowState) -> tuple[str, int, str | None]:
    """由「刚完成的节点」推导当前 phase/step 与正在运行的 Agent。

    state.current_node 为 stream 产出时刚执行完的节点；下一活动随拓扑确定
    （validation 的 retry 分支回 generation）。
    """
    node = state.get("current_node")
    if not node:
        return "diagnosis", 1, "diagnosis"  # 起跑帧：诊断进行中
    if node in ("diagnostic", "retrieval"):
        return "generation", 2, "generation"
    if node == "generation":
        return "validation", 3, "critic"
    if node == "validation":
        action = (state.get("validation_result") or {}).get("action")
        if action == "retry":
            return "generation", 2, "generation"  # 重试帧：生成 Agent 重新点亮
        return "validation", 3, None  # accept/fallback → decision 待落定
    return "complete", 4, None  # decision


def _agents_from_state(state: WorkflowState, active: str | None) -> list[dict[str, Any]]:
    """11.2 agents[]：固定 3 项 id 顺序；running/idle/success/error 四态。"""
    log = state.get("node_execution_log") or []
    degraded = state.get("workflow_status") == "degraded"
    finished = state.get("current_node") == "decision"
    action = (state.get("validation_result") or {}).get("action")
    agents: list[dict[str, Any]] = []
    for agent_id in AGENT_ORDER:
        entries = [n for n in log if n["agent"] == agent_id]
        name = entries[-1]["name"] if entries else _AGENT_NAMES[agent_id]
        if agent_id == active:
            status, last_action = "running", _RUNNING_ACTION[agent_id]
        elif not entries:
            status, last_action = "idle", _IDLE_ACTION[agent_id]
        else:
            status, last_action = "success", entries[-1]["outputSummary"]
            # critic 红灯：终态降级，或运行中校验未通过（retry/fallback 刚落定）
            if agent_id == "critic" and (
                degraded if finished else action in ("retry", "fallback")
            ):
                status = "error"
        agents.append(
            {"id": agent_id, "name": name, "status": status, "lastAction": last_action}
        )
    return agents


def _snapshot_from_state(workflow_id: str, state: WorkflowState) -> dict[str, Any]:
    """组装一帧 11.2 快照（GET 轮询与 WS 推送同结构）。"""
    phase, step, active = _phase_step_active(state)
    messages = [
        {"id": i + 1, **m} for i, m in enumerate(state.get("messages") or [])
    ]
    agents = _agents_from_state(state, active)
    return {
        "workflowId": workflow_id,
        "phase": phase,
        "step": step,
        "agents": agents,
        "messages": messages,
        "stats": {
            "completedAgents": sum(1 for a in agents if a["status"] == "success"),
            "messageCount": len(messages),
            "progress": _PROGRESS[phase],
        },
    }


def snapshot_from_trace(row: WorkflowTrace) -> dict[str, Any]:
    """WorkflowTrace 持久化行 → 11.2 完成态快照（注册表淘汰/重启后的兜底）。"""
    agents = row.agents or []
    messages = row.messages or []
    return {
        "workflowId": row.id,
        "phase": "complete",
        "step": 4,
        "agents": agents,
        "messages": messages,
        "stats": {
            "completedAgents": sum(1 for a in agents if a["status"] == "success"),
            "messageCount": len(messages),
            "progress": 100,
        },
    }


# ---- R3.5：Supervisor 双入口分派 ------------------------------------------------

def dispatch_workflow(
    db: Session,
    *,
    user_id: str,
    kp_id: str,
    difficulty: str,
    target_job: str,
    retriever: Retriever | None = None,
    workflow_id: str | None = None,
    on_update: Any | None = None,
) -> dict[str, Any]:
    """按 ``SUPERVISOR_ENABLED`` 分派到 Router / Legacy 入口（R3.5 灰度开关）。

    - **flag OFF（默认）** → ``run_learning_workflow``：返回结构与改造前**逐字相同**，
      调用方完全无感；
    - **flag ON** → ``run_router_workflow``（Supervisor → conditional edge → 子图）：
      返回多出 ``routing`` / ``routingTrace`` 两个**进程内**键，**11.2 trace 结构不变**
      （``project_legacy_state`` 不投影路由字段，见 R3.5 报告 §5）。

    灰度期 ``objective`` 恒为空 → Supervisor 走 fallback → ``resource_generation``，
    与旧入口行为等价（R3.5 报告 §4 Phase 2 的验证前提）。

    **本函数是 R3.5 唯一的生产分派点**；``run_learning_workflow`` /
    ``run_router_workflow`` 两者签名均未改动。
    """
    if settings.supervisor_enabled:
        result = run_router_workflow(
            db,
            user_id=user_id,
            kp_id=kp_id,
            difficulty=difficulty,
            target_job=target_job,
            retriever=retriever,
            workflow_id=workflow_id,
            on_update=on_update,
            objective="",  # 灰度期不传意图 → fallback，行为等价旧流程
        )
        # 路由可观测性：只记日志，不进 11.2、不落库
        trace = result.get("routingTrace")
        if trace:
            logger.info(
                "supervisor_routing_trace %s",
                json.dumps(trace, ensure_ascii=False, default=str),
            )
        return result
    return run_learning_workflow(
        db,
        user_id=user_id,
        kp_id=kp_id,
        difficulty=difficulty,
        target_job=target_job,
        retriever=retriever,
        workflow_id=workflow_id,
        on_update=on_update,
    )


# ---- 运行态与发布 ---------------------------------------------------------------

class WorkflowRun:
    """单次工作流运行态：最新快照 + WS 订阅队列（发布经事件循环线程投递）。"""

    def __init__(self, workflow_id: str, loop: asyncio.AbstractEventLoop) -> None:
        self.workflow_id = workflow_id
        self.loop = loop
        self.snapshot: dict[str, Any] = _snapshot_from_state(workflow_id, {})
        self.done = False
        self._subscribers: set[asyncio.Queue] = set()

    # WS 侧（事件循环线程）
    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    # 工作线程侧：更新快照并向所有订阅者投递一帧；final 帧后投 None 哨兵收尾
    def publish(self, snapshot: dict[str, Any], *, final: bool = False) -> None:
        self.snapshot = snapshot
        if final:
            self.done = True

        def _fanout() -> None:
            for queue in list(self._subscribers):
                queue.put_nowait(snapshot)
                if final:
                    queue.put_nowait(None)

        self.loop.call_soon_threadsafe(_fanout)


def get_run(workflow_id: str) -> WorkflowRun | None:
    return _runs.get(workflow_id)


def start_workflow(
    *,
    user_id: str,
    kp_id: str,
    target_job: str,
    difficulty: str = "初级",
    retriever: Retriever | None = None,
    loop: asyncio.AbstractEventLoop,
) -> str:
    """预分配 workflowId 并后台线程执行，立即返回（11.1）。

    retriever：B10 起由 execute 端按 provider 注入（真实模式传讲义检索器，使大屏
    工作流产物与 /resource/lecture 同源；mock 传 None → 工作流用确定性 mock 检索）。
    """
    workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
    run = WorkflowRun(workflow_id, loop)
    _runs[workflow_id] = run
    while len(_runs) > _MAX_RUNS:
        _runs.popitem(last=False)
    threading.Thread(
        target=_worker,
        args=(run, user_id, kp_id, difficulty, target_job, retriever),
        daemon=True,
        name=f"workflow-{workflow_id}",
    ).start()
    return workflow_id


def _worker(
    run: WorkflowRun,
    user_id: str,
    kp_id: str,
    difficulty: str,
    target_job: str,
    retriever: Retriever | None = None,
) -> None:
    """后台线程：自管 DB 会话，逐节点发布快照帧（演示延迟见 config）。

    工作流跑完且未降级时，把产物回写讲义缓存（B10 闭环）——回写失败不影响已
    实时交付的工作流结果（不向 WS 抛错帧）。
    """

    def _delay() -> None:
        if settings.workflow_step_delay_ms > 0:
            time.sleep(settings.workflow_step_delay_ms / 1000)

    def on_update(state: WorkflowState) -> None:
        final = state.get("current_node") == "decision"
        run.publish(_snapshot_from_state(run.workflow_id, state), final=final)
        if not final:
            _delay()

    # 后台线程上下文为空：显式绑定发起用户 → 生成经 llm_transport 时按其
    # 「当前模型」（含自建配置 overlay）分发（模型管理 21.3+；线程私有，无需恢复）
    model_registry.bind_user(user_id)
    db = SessionLocal()
    try:
        # 起跑帧（诊断 running）已在 __init__ 置入快照；留一个轮询观察窗
        _delay()
        result = dispatch_workflow(
            db,
            user_id=user_id,
            kp_id=kp_id,
            difficulty=difficulty,
            target_job=target_job,
            retriever=retriever,
            workflow_id=run.workflow_id,
            on_update=on_update,
        )
        # B10：工作流产物 → 学习资源闭环（complete 且未降级时覆盖讲义缓存）。
        # 失败仅记日志、不向 WS 抛错帧（工作流本身已成功交付实时结果）。
        try:
            from app.services import resource as resource_service

            resource_service.cache_lecture_from_workflow(
                db, kp_id=kp_id, difficulty=difficulty, result=result
            )
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).exception("工作流产物回写讲义缓存失败")
    except Exception as exc:  # noqa: BLE001 兜底失败帧（messages 追加 error）
        snapshot = {key: value for key, value in run.snapshot.items()}
        messages = list(snapshot["messages"])
        messages.append(
            {
                "id": len(messages) + 1,
                "from": "系统",
                "to": "用户",
                "message": f"工作流执行失败:{exc}",
                "type": "error",
                "timestamp": _now_iso(),
            }
        )
        snapshot["messages"] = messages
        snapshot["stats"] = {**snapshot["stats"], "messageCount": len(messages)}
        run.publish(snapshot, final=True)
    finally:
        db.close()
        if not run.done:  # 正常路径在 decision 帧已 final；此处仅兜底
            run.publish(run.snapshot, final=True)
