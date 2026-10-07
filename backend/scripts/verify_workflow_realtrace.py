# -*- coding: utf-8 -*-
"""端到端坐实：工作流大屏由真实 LangGraph 执行轨迹驱动（接口文档 11.1/11.2）。

用法：
  - 真实模式（读 backend/.env 的 deepseek provider）：python scripts/verify_workflow_realtrace.py
  - mock 兜底模式（无密钥）：              LLM_PROVIDER=mock python scripts/verify_workflow_realtrace.py

证据链（in-process TestClient，与浏览器经 /api 代理同路径，仅省去 Vite 一跳）：
  1. POST /workflow/execute → WS /ws/workflow/{id}：逐帧打印 phase/step/agents 状态 +
     真实节点消息（检索片段数 / 生成字符数 / Critic 评分+幻觉率），证明帧由真实
     execution-trace 驱动，节点状态来自 current_node / validation_result.action；
  4. GET /resource/lecture（同 kp+难度）→ 断言 workflowId == 本次 wid，证明完成弹窗
     列出的讲义产物正是本次真实工作流回写（B10 闭环）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# provider 由环境/.env 决定；重置单例确保按本进程 provider 实例化
import app.core.llm as llm_mod
from app.core.config import settings

PROVIDER = os.environ.get("LLM_PROVIDER", settings.llm_provider)
settings.llm_provider = PROVIDER
llm_mod._client = None  # 重置 LLMClient 单例

# 真实模式不加人工节点延迟（看真实耗时）；mock 模式留 120ms 观察增量帧逐帧推送
settings.workflow_step_delay_ms = 0 if PROVIDER != "mock" else 120

from fastapi.testclient import TestClient  # noqa: E402

from app.core.llm import get_llm  # noqa: E402
from app.main import app  # noqa: E402

KP = os.environ.get("VERIFY_KP", "nn")
DIFF = os.environ.get("VERIFY_DIFF", "初级")

print(f"=== provider={PROVIDER} is_mock={get_llm().is_mock} kp={KP} diff={DIFF} "
      f"step_delay_ms={settings.workflow_step_delay_ms} ===")

with TestClient(app) as client:
    tok = client.post("/api/v1/auth/login",
                      json={"username": "learner_001", "password": "123456"}
                      ).json()["data"]["token"]
    H = {"Authorization": f"Bearer {tok}"}

    wid = client.post("/api/v1/workflow/execute", headers=H,
                      json={"kpId": KP, "difficulty": DIFF}).json()["data"]["workflowId"]
    print(f"[execute] code 0  workflowId={wid}")

    frames = []
    with client.websocket_connect(f"/api/v1/ws/workflow/{wid}") as ws:
        while True:
            env = ws.receive_json()
            assert env["code"] == 0, f"信封 code={env['code']} msg={env.get('message')}"
            d = env["data"]
            assert set(d.keys()) == {
                "workflowId", "phase", "step", "agents", "messages", "stats"
            }, f"非 11.2 六键: {sorted(d.keys())}"
            frames.append(d)
            if d["phase"] == "complete":
                break

# ---- 帧逐帧打印（agents 状态 + 真实节点消息） ----
print(f"\n--- {len(frames)} 帧 WS 推送序列 ---")
all_ids = []
for i, f in enumerate(frames):
    agents = " ".join(f"{a['id']}:{a['status']}" for a in f["agents"])
    print(f"帧{i+1}  phase={f['phase']:<10} step={f['step']} progress={f['stats']['progress']:<3} "
          f"+{len(f['messages'])}msg | {agents}")
    for m in f["messages"]:
        all_ids.append(m["id"])
        tag = "⚠ERR" if m["type"] == "error" else m["type"][:3]
        print(f"        #{m['id']:<2} [{tag}] {m['from']} → {m['to']}: {m['message']}")

# ---- 断言（真实轨迹驱动的硬证据） ----
assert frames[-1]["phase"] == "complete" and frames[-1]["stats"]["progress"] == 100
assert len(all_ids) == len(set(all_ids)), f"消息 id 重复: {all_ids}"
assert len(all_ids) == frames[-1]["stats"]["messageCount"], "消息不重不漏校验失败"
# 至少经历 诊断→生成→审核 三个 phase（顺序由真实 current_node 推导）
phases = [f["phase"] for f in frames]
for need in ("generation", "validation", "complete"):
    assert need in phases, f"缺少 phase={need}，phases={phases}"
crit = next(a for a in frames[-1]["agents"] if a["id"] == "critic")
degraded = crit["status"] == "error"
print(f"\n[终态] agents={ {a['id']: a['status'] for a in frames[-1]['agents']} } degraded={degraded}")

# ---- item 4：完成产物 → 讲义缓存闭环（非降级时 workflowId 必等于本次 wid） ----
with TestClient(app) as client:
    tok = client.post("/api/v1/auth/login",
                      json={"username": "learner_001", "password": "123456"}
                      ).json()["data"]["token"]
    H = {"Authorization": f"Bearer {tok}"}
    lec = client.post("/api/v1/resource/lecture", headers=H,
                      json={"kpId": KP, "difficulty": DIFF}).json()["data"]
print(f"[lecture] workflowId={lec.get('workflowId')}  markdown={len(lec.get('markdown') or '')}字符 "
      f"sources={len(lec.get('sources') or [])} hallucinationRate={lec.get('hallucinationRate')}")
if not degraded:
    assert lec.get("workflowId") == wid, \
        f"闭环断裂：讲义 workflowId={lec.get('workflowId')} != 本次 {wid}"
    print(f"[闭环✓] 资源页讲义产物 workflowId 与本次工作流一致 = {wid}")
else:
    print("[闭环] 本次降级 → 讲义缓存按设计保留上一版本（不覆盖）")

print(f"\nPASSED ({PROVIDER}): {len(frames)} 帧真实轨迹驱动，phases={phases}")
