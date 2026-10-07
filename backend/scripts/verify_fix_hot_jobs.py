# -*- coding: utf-8 -*-
"""fix-手动填写岗位列表 验证：/job-market/hot 与 /{id} 在线/离线两模式实测。

用法：python verify_fix_hot_jobs.py [port=8000]
"""
import json
import sys
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8000"
BASE = f"http://127.0.0.1:{PORT}/api/v1"
TOKEN = None


def call(method, path, body=None):
    """返回完整信封（不预设 code，便于观察 2002）。"""
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    with urllib.request.urlopen(req, data=data) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


_, env = call("POST", "/auth/login", {"username": "learner_001", "password": "123456"})
TOKEN = env["data"]["token"]

http, env = call("GET", "/job-market/hot")
jobs = env["data"] if isinstance(env["data"], list) else env["data"].get("jobs")
print(f"[hot  :{PORT}] http={http} code={env['code']} "
      f"offline={'offline' in env['data'] if isinstance(env['data'], dict) else False} "
      f"n={len(jobs) if jobs is not None else 0} ids={[j['id'] for j in jobs] if jobs else []}")

http, env = call("GET", "/job-market/llm-app")
print(f"[snap :{PORT}] http={http} code={env['code']} offline={env['data'].get('offline')}")
