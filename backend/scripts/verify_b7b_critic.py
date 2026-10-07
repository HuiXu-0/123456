# -*- coding: utf-8 -*-
"""B7-b 强制 critic 低分实测：in-process 服务端（钩子仅进程内可置）跑通 WS error 帧序列。

set_force_critic_low(True) 后 mock critic 恒返回 0.42 低分 → 重试 2 次 → 降级交付。
打印每帧 agents 状态与 error 消息：critic 红灯（status=error）与重试过程正是前端
AgentStatusCard 指示灯 / 消息日志将消费的同一帧流（applyFrame 对 error 帧无特殊分支，
红灯由 agents[].status 数据直接驱动）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("JOB_MARKET_OFFLINE", "false")

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.llm import set_force_critic_low
from app.main import app

settings.workflow_step_delay_ms = 150  # 留出观察节奏，确保增量帧逐帧推送

with TestClient(app) as client:
    res = client.post("/api/v1/auth/login", json={"username": "learner_001", "password": "123456"})
    headers = {"Authorization": f"Bearer {res.json()['data']['token']}"}

    set_force_critic_low(True)
    try:
        wid = client.post("/api/v1/workflow/execute", headers=headers, json={}).json()["data"]["workflowId"]
        frames = []
        with client.websocket_connect(f"/api/v1/ws/workflow/{wid}") as ws:
            while True:
                env = ws.receive_json()
                assert env["code"] == 0
                frames.append(env["data"])
                if env["data"]["phase"] == "complete":
                    break
    finally:
        set_force_critic_low(False)

error_msgs = [m for f in frames for m in f["messages"] if m["type"] == "error"]
critic_error_frames = [
    f for f in frames
    if any(a["id"] == "critic" and a["status"] == "error" for a in f["agents"])
]
for i, f in enumerate(frames):
    agents = " ".join(f"{a['id']}:{a['status']}" for a in f["agents"])
    err = "; ".join(m["message"] for m in f["messages"] if m["type"] == "error")
    print(f"帧{i+1}  phase={f['phase']:<10} step={f['step']} | {agents}" + (f"  ⚠ {err}" if err else ""))

assert error_msgs, "未出现 error 消息"
assert critic_error_frames, "未出现 critic 红灯帧"
assert frames[-1]["phase"] == "complete"
assert any(a["id"] == "critic" and a["status"] == "error" for a in frames[-1]["agents"]), "降级终态红灯未保持"
print(f"CRITIC-LOW CHECK PASSED: {len(frames)} 帧，error 消息 {len(error_msgs)} 条，"
      f"critic 红灯帧 {len(critic_error_frames)} 个，降级终态红灯保持")
