"""可恢复的轻量异步任务基建。

契约依据：接口文档 1.4 异步任务约定 + 15.2 任务响应结构。

状态机：`pending → running → succeeded / failed`。
- 提交耗时操作返回 taskId；
- `GET /tasks/{taskId}` 轮询 `{ taskId, status, progress?, result?, error? }`。

运行态保留在进程内字典，状态快照同时写 SQLite。服务重启后终态任务可继续查询；
无法安全重放的 pending/running 闭包任务会被明确标记为 failed，避免永久轮询。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.database import SessionLocal
from app.models.entities import AsyncTaskRecord

# worker 接收 Task（可写 progress），返回值落入 task.result
TaskWorker = Callable[["Task"], Awaitable[Any]]


@dataclass
class Task:
    """单个异步任务的运行态。"""

    task_id: str
    user_id: str | None = None
    task_type: str | None = None
    scope_key: str | None = None
    status: str = "pending"  # pending | running | succeeded | failed
    progress: int | None = None
    result: Any = None
    error: dict[str, Any] | None = None

    def to_data(self) -> dict[str, Any]:
        """转接口文档 15.2 响应结构；progress 不支持时省略。"""
        data: dict[str, Any] = {
            "taskId": self.task_id,
            "status": self.status,
            "result": self.result,
            "error": self.error,
        }
        if self.progress is not None:
            data["progress"] = self.progress
        return data

    def to_summary(self) -> dict[str, Any]:
        """轻量摘要（任务列表用），不含 result 体。"""
        data: dict[str, Any] = {
            "taskId": self.task_id,
            "taskType": self.task_type,
            "scopeKey": self.scope_key,
            "status": self.status,
            "createdAt": None,
        }
        if self.progress is not None:
            data["progress"] = self.progress
        return data


# 进程内任务表
_tasks: dict[str, Task] = {}


def _json_dump(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


def _json_load(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def _persist(task: Task) -> None:
    """幂等写入任务快照；调用方不复用请求级会话。"""
    db = SessionLocal()
    try:
        row = db.get(AsyncTaskRecord, task.task_id)
        if row is None:
            row = AsyncTaskRecord(
                task_id=task.task_id,
                user_id=task.user_id,
                task_type=task.task_type,
                scope_key=task.scope_key,
                status=task.status,
            )
            db.add(row)
        row.status = task.status
        row.progress = task.progress
        row.result_json = _json_dump(task.result)
        row.error_json = _json_dump(task.error)
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


def _from_record(row: AsyncTaskRecord) -> Task:
    return Task(
        task_id=row.task_id,
        user_id=row.user_id,
        task_type=row.task_type,
        scope_key=row.scope_key,
        status=row.status,
        progress=row.progress,
        result=_json_load(row.result_json),
        error=_json_load(row.error_json),
    )


def recover_tasks() -> dict[str, int]:
    """启动时恢复任务快照，并收敛因重启中断的非终态任务。"""
    restored = 0
    interrupted = 0
    interrupted_tasks: list[Task] = []
    _tasks.clear()
    db = SessionLocal()
    try:
        rows = db.query(AsyncTaskRecord).all()
        for row in rows:
            task = _from_record(row)
            if task.status in {"pending", "running"}:
                task.status = "failed"
                task.error = {"code": 2001, "message": "服务重启导致任务中断，请重新提交"}
                interrupted_tasks.append(task)
                interrupted += 1
            _tasks[task.task_id] = task
            restored += 1
        db.close()
        db = None
        for task in interrupted_tasks:
            _persist(task)
    finally:
        if db is not None:
            db.close()
    return {"restored": restored, "interrupted": interrupted}


def _new_task_id() -> str:
    return "t_" + uuid.uuid4().hex[:12]


def get_task(task_id: str) -> Task | None:
    task = _tasks.get(task_id)
    if task is not None:
        # 轮询时顺带保存 worker 直接更新的 progress。
        _persist(task)
        return task
    db = SessionLocal()
    try:
        row = db.get(AsyncTaskRecord, task_id)
        if row is None:
            return None
        task = _from_record(row)
        _tasks[task_id] = task
        return task
    finally:
        db.close()


async def _runner(task: Task, worker: TaskWorker) -> None:
    """驱动任务状态机：running → succeeded/failed。"""
    task.status = "running"
    _persist(task)
    try:
        task.result = await worker(task)
        task.progress = 100
        task.status = "succeeded"
    except Exception as exc:  # noqa: BLE001 兜底为 failed，错误码对齐 2001
        task.error = {"code": 2001, "message": str(exc) or "任务执行失败"}
        task.status = "failed"
    finally:
        _persist(task)


def submit(
    worker: TaskWorker,
    *,
    user_id: str | None = None,
    task_type: str | None = None,
    scope_key: str | None = None,
) -> Task:
    """登记并后台调度一个异步任务，立即返回 pending 态 Task。

    必须在运行中的事件循环内调用（FastAPI async 端点天然满足）。
    """
    task = Task(
        task_id=_new_task_id(),
        user_id=user_id,
        task_type=task_type,
        scope_key=scope_key,
    )
    _tasks[task.task_id] = task
    _persist(task)
    asyncio.create_task(_runner(task, worker))
    return task


def list_user_tasks(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """查询指定用户的任务列表（按创建时间倒序）。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(AsyncTaskRecord)
            .filter(AsyncTaskRecord.user_id == user_id)
            .order_by(AsyncTaskRecord.created_at.desc())
            .limit(limit)
            .all()
        )
        result = []
        for row in rows:
            task = _from_record(row)
            summary = task.to_summary()
            summary["createdAt"] = row.created_at.isoformat() if row.created_at else None
            result.append(summary)
        return result
    finally:
        db.close()
