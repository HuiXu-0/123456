from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.core.tasks import _tasks, get_task, recover_tasks, submit
from app.models.entities import AsyncTaskRecord


def _delete_rows(*task_ids: str) -> None:
    db = SessionLocal()
    try:
        db.query(AsyncTaskRecord).filter(AsyncTaskRecord.task_id.in_(task_ids)).delete(
            synchronize_session=False
        )
        db.commit()
    finally:
        db.close()
    for task_id in task_ids:
        _tasks.pop(task_id, None)


def test_recover_terminal_and_reconcile_interrupted_tasks():
    terminal_id = "t_test_terminal"
    interrupted_id = "t_test_interrupted"
    now = datetime.now(timezone.utc)
    _delete_rows(terminal_id, interrupted_id)
    db = SessionLocal()
    try:
        db.add_all(
            [
                AsyncTaskRecord(
                    task_id=terminal_id,
                    status="succeeded",
                    progress=100,
                    result_json=json.dumps({"lessons": [1, 2]}),
                    created_at=now,
                    updated_at=now,
                ),
                AsyncTaskRecord(
                    task_id=interrupted_id,
                    status="running",
                    progress=45,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    try:
        summary = recover_tasks()
        terminal = get_task(terminal_id)
        interrupted = get_task(interrupted_id)

        assert summary["restored"] >= 2
        assert summary["interrupted"] >= 1
        assert terminal is not None
        assert terminal.status == "succeeded"
        assert terminal.result == {"lessons": [1, 2]}
        assert interrupted is not None
        assert interrupted.status == "failed"
        assert interrupted.progress == 45
        assert interrupted.error == {
            "code": 2001,
            "message": "服务重启导致任务中断，请重新提交",
        }

        _tasks.pop(interrupted_id)
        restored_again = get_task(interrupted_id)
        assert restored_again is not None
        assert restored_again.status == "failed"
    finally:
        _delete_rows(terminal_id, interrupted_id)


def test_submit_persists_successful_state_machine():
    async def scenario():
        async def worker(task):
            task.progress = 60
            return {"ok": True}

        task = submit(worker)
        for _ in range(50):
            if task.status in {"succeeded", "failed"}:
                break
            await asyncio.sleep(0.01)
        return task

    task = asyncio.run(scenario())
    try:
        assert task.status == "succeeded"
        assert task.progress == 100
        _tasks.pop(task.task_id)
        restored = get_task(task.task_id)
        assert restored is not None
        assert restored.status == "succeeded"
        assert restored.result == {"ok": True}
    finally:
        _delete_rows(task.task_id)
