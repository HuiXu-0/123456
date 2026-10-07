"""多智能体工作流 API（接口文档 11 章，B7-a）。

- POST /workflow/execute：触发 B5 LangGraph 真实工作流，后台运行，立即返回 workflowId；
  请求体 { targetJobId?, kpId? } 均可选（11.1）。
- GET  /workflow/{workflowId}：轮询 11.2 快照（运行中读内存最新帧；注册表淘汰/
  重启后兜底 WorkflowTrace 持久化行）。
- WS   /api/v1/ws/workflow/{workflowId}：连接即先推一帧全量快照，之后节点进入/
  退出/重试/降级时推送同结构增量帧（messages 仅含新增，agents/stats/phase 为当前
  全量），消息同样套统一信封 {code,message,data,traceId}；complete 帧后服务端关闭。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.core.envelope import envelope, fail, success
from app.core.llm import LECTURE_DIFFICULTIES, get_llm
from app.core.security import get_current_user
from app.models.entities import JobSnapshot, KnowledgePoint, User, WorkflowTrace
from app.services import workflow_runner
from app.services.resource import _retrieve_for_lecture

router = APIRouter(tags=["workflow"])

# 11.1 请求体三字段均可选：kpId 缺省用演示主线知识点，目标岗位缺省与工作流默认一致，
# difficulty 缺省「初级」（B10：产物按该难度档回写讲义缓存，需为合法讲义难度档）
_DEFAULT_KP = "nn"
_DEFAULT_TARGET_JOB = "大模型应用工程师"
_DEFAULT_DIFFICULTY = "初级"


class WorkflowExecuteRequest(BaseModel):
    """POST /workflow/execute 请求体（接口文档 11.1，均可选）。"""

    targetJobId: str | None = None
    kpId: str | None = None
    difficulty: str | None = None  # 入门|初级|中级|高级|精通（B10：产物回写讲义缓存的难度档）


@router.post("/workflow/execute")
async def execute_workflow(
    body: WorkflowExecuteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """触发工作流执行（接口文档 11.1）。后台线程运行，立即返回 workflowId。

    B10：difficulty 入参（缺省「初级」）决定产物回写讲义缓存的难度档；工作流
    complete 且未降级时，产物覆盖该 kp+难度的讲义缓存（见 workflow_runner._worker）。
    """
    kp_id = body.kpId or _DEFAULT_KP
    if db.get(KnowledgePoint, kp_id) is None:
        return fail(code=1004, message="知识点不存在", status_code=404)
    difficulty = body.difficulty or _DEFAULT_DIFFICULTY
    if difficulty not in LECTURE_DIFFICULTIES:
        return fail(code=1001, message="难度档非法，应为 入门|初级|中级|高级|精通", status_code=400)
    target_job = _DEFAULT_TARGET_JOB
    if body.targetJobId:
        job = db.get(JobSnapshot, body.targetJobId)
        if job is None:
            return fail(code=1004, message="目标岗位不存在", status_code=404)
        target_job = job.name
    # 真实模式注入讲义检索器，使大屏工作流产物与 /resource/lecture 同源（同检索→同生成）；
    # mock 模式传 None → 工作流用确定性 mock 检索（演示快、可复现）。
    retriever = None if get_llm().is_mock else _retrieve_for_lecture
    workflow_id = workflow_runner.start_workflow(
        user_id=user.id,
        kp_id=kp_id,
        target_job=target_job,
        difficulty=difficulty,
        retriever=retriever,
        loop=asyncio.get_running_loop(),
    )
    return success({"workflowId": workflow_id})


@router.get("/workflow/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """工作流状态轮询（接口文档 11.2）。"""
    run = workflow_runner.get_run(workflow_id)
    if run is not None:
        return success(run.snapshot)
    row = db.get(WorkflowTrace, workflow_id)
    if row is None:
        return fail(code=1004, message="工作流不存在", status_code=404)
    return success(workflow_runner.snapshot_from_trace(row))


@router.websocket("/ws/workflow/{workflow_id}")
async def ws_workflow(websocket: WebSocket, workflow_id: str) -> None:
    """工作流状态推送（接口文档 11.2）。

    帧协议：首帧全量快照（messages 含全部历史），后续帧同结构、messages 仅含
    新增条目（按 id 截断）；每帧套统一信封。complete/失败帧发出后服务端关闭。
    """
    await websocket.accept()
    run = workflow_runner.get_run(workflow_id)
    if run is None:
        # 注册表无运行态：兜底持久化 trace（推一帧完成态快照后关闭）
        db = SessionLocal()
        try:
            row = db.get(WorkflowTrace, workflow_id)
        finally:
            db.close()
        if row is None:
            await websocket.send_json(envelope(None, code=1004, message="工作流不存在"))
            await websocket.close(code=4404)
            return
        await websocket.send_json(envelope(workflow_runner.snapshot_from_trace(row)))
        await websocket.close()
        return

    queue = run.subscribe()  # 先订阅再发快照，避免漏帧（重复消息由 id 截断去重）
    try:
        snapshot = run.snapshot
        await websocket.send_json(envelope(snapshot))  # 全量快照帧
        last_msg_id = len(snapshot["messages"])
        if run.done:
            await websocket.close()
            return
        while True:
            item = await queue.get()
            if item is None:  # 哨兵：final 帧已发出
                break
            delta = {
                **item,
                "messages": [m for m in item["messages"] if m["id"] > last_msg_id],
            }
            last_msg_id = max(last_msg_id, len(item["messages"]))
            await websocket.send_json(envelope(delta))
        await websocket.close()
    except WebSocketDisconnect:
        pass  # 客户端断开（前端回退演示模式），服务端静默清理
    finally:
        run.unsubscribe(queue)
