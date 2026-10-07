# -*- coding: utf-8 -*-
"""B7-b 验证脚本：经 Vite dev 代理（浏览器等价路径）实测前端实时通道三条链路。

前置：
  1) 后端 mock provider 起 :8000；
  2) 前端 dev server 起（端口经 VITE_PORT 环境变量传入，默认 3004）。

覆盖：
  [workflow]  11.1 execute → 11.2 WS（经 Vite 代理，验证 ws:true 升级）：
              首帧全量 + 增量帧、信封 code 0、消息 id 不重不漏、phase 推进至 complete；
  [tutor]     8.7/15.4 SSE（经代理）：delta 逐条 + done 携带 sessionId/suggestions，
              第二轮带回 sessionId 验证多轮会话保持（与 services/tutor.ts 同款解析）；
  [video]     8.3：narration 5 段、起始帧 0/90/300/540/720（前端 services 层映射 frame→from）；
  [vite]      本次改动的 6 个前端模块 transform 200；
  [fallback]  （脚本末提示）停掉后端后 execute 经代理报错 → 前端 fallbackToDemo 条件成立。
"""
import json
import os
import sys
import urllib.request

import asyncio
import websockets

PORT = os.environ.get("VITE_PORT", "3004")
# Vite dev 在 Windows 上监听 ::1（IPv6 localhost），统一用 localhost 访问
BASE = f"http://localhost:{PORT}"
API = BASE + "/api/v1"
TOKEN = None


def call(method, path, body=None, headers=None):
    req = urllib.request.Request(API + path, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    return urllib.request.urlopen(req, data=data)


def call_json(method, path, body=None):
    with call(method, path, body) as r:
        env = json.loads(r.read().decode("utf-8"))
    assert env["code"] == 0, f"{path} -> code={env['code']} msg={env['message']}"
    return env["data"]


# 0. 登录（经 Vite 代理，等价浏览器 fetch('/api/v1/...')）
TOKEN = call_json("POST", "/auth/login", {"username": "learner_001", "password": "123456"})["token"]
print(f"[login]    code 0  via Vite proxy :{PORT}")

# 1. workflow：execute → WS 帧序列（经代理 ws://:PORT，验证 vite ws:true）
wid = call_json("POST", "/workflow/execute", {})["workflowId"]
print(f"[execute]  code 0  workflowId={wid}")


async def collect_frames():
    frames = []
    async with websockets.connect(f"ws://localhost:{PORT}/api/v1/ws/workflow/{wid}") as ws:
        try:
            while True:
                env = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                assert env["code"] == 0, f"WS 信封 code={env['code']}"
                assert set(env["data"].keys()) == {
                    "workflowId", "phase", "step", "agents", "messages", "stats"
                }, f"帧非 11.2 六键: {sorted(env['data'].keys())}"
                frames.append(env["data"])
        except websockets.ConnectionClosed:
            pass
    return frames


frames = asyncio.get_event_loop().run_until_complete(collect_frames())
assert frames, "未收到任何 WS 帧"
ids = [m["id"] for f in frames for m in f["messages"]]
assert len(ids) == len(set(ids)), f"增量帧消息 id 重复: {ids}"
assert frames[-1]["phase"] == "complete" and frames[-1]["stats"]["progress"] == 100
# 不重不漏：全程增量消息累计数 == 终帧 stats.messageCount（前端按 id 去重追加后的渲染数）
assert len(ids) == frames[-1]["stats"]["messageCount"], \
    f"消息有漏: 收到 {len(ids)} != stats {frames[-1]['stats']['messageCount']}"
statuses = {a["id"]: a["status"] for a in frames[-1]["agents"]}
for i, f in enumerate(frames):
    agents = " ".join(f"{a['id']}:{a['status']}" for a in f["agents"])
    print(f"[ws 帧{i+1}]   phase={f['phase']:<10} step={f['step']} "
          f"progress={f['stats']['progress']:<3} msgs(+{len(f['messages'])}) | {agents}")
print(f"[ws]       {len(frames)} 帧经 Vite 代理收齐，消息 id 不重不漏，终帧 {statuses}")

# 2. tutor SSE（与 services/tutor.ts 同款 \n\n 分块解析）
def sse_chat(body):
    deltas, done = [], None
    with call("POST", "/resource/tutor/chat", body, {"Accept": "text/event-stream"}) as r:
        ctype = r.headers.get("Content-Type", "")
        assert "text/event-stream" in ctype, f"非 SSE 回包: {ctype}"
        buf = r.read().decode("utf-8")
    for block in buf.split("\n\n"):
        if not block.strip():
            continue
        event, data_lines = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        payload = json.loads("\n".join(data_lines))
        if event == "done":
            done = payload
        elif event == "message":
            deltas.append(payload["delta"])
    return deltas, done


deltas, done = sse_chat({"kpId": "nn", "message": "为什么需要激活函数？"})
assert deltas and done and done.get("sessionId"), "SSE 未收到 delta/done"
print(f"[sse 轮1]  deltas={len(deltas)} 拼接前 18 字「{''.join(deltas)[:18]}…」")
print(f"[sse done] sessionId={done['sessionId']} suggestions={done['suggestions']}")
sid = done["sessionId"]
deltas2, done2 = sse_chat({"kpId": "nn", "sessionId": sid, "message": "等价于线性变换"})
assert done2["sessionId"] == sid, "多轮会话 sessionId 未保持"
print(f"[sse 轮2]  deltas={len(deltas2)} 会话保持 sessionId 一致={done2['sessionId'] == sid} "
      f"拼接前 18 字「{''.join(deltas2)[:18]}…」")

# 3. video：narration 帧位与前端 Remotion 5 个 Sequence 对齐
v = call_json("POST", "/resource/video", {"kpId": "nn", "difficulty": "初级"})
fr = [n["frame"] for n in v["narration"]]
assert fr == [0, 90, 300, 540, 720], f"帧位不符: {fr}"
assert (v["fps"], v["width"], v["height"], v["durationInFrames"]) == (30, 1280, 720, 900)
print(f"[video]    code 0  videoUrl={v['videoUrl']} narration={len(v['narration'])} 段 "
      f"frames={fr} fps={v['fps']} {v['width']}x{v['height']} dur={v['durationInFrames']}")

# 4. Vite transform：本次改动的前端模块均可正常转译
for mod in [
    "/src/services/workflow.ts",
    "/src/services/tutor.ts",
    "/src/services/resource.ts",
    "/src/pages/AgentWorkflow.tsx",
    "/src/components/SocraticTutor.tsx",
    "/src/components/VideoLecture.tsx",
]:
    with urllib.request.urlopen(BASE + mod) as r:
        assert r.status == 200, f"{mod} -> {r.status}"
print("[vite]     6 个改动模块 transform 200")

print("ALL B7-B BROWSER-EQUIVALENT CHECKS PASSED (code 0)")
