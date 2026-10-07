# -*- coding: utf-8 -*-
"""fix-手动填写岗位列表 验证②：手动填写收尾等价请求——选岗后完成诊断（POST /profile/diagnosis-complete）。

用法：python verify_fix_manual_finish.py [port=8000]
"""
import json
import sys
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8000"
BASE = f"http://127.0.0.1:{PORT}/api/v1"
TOKEN = None


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    with urllib.request.urlopen(req, data=data) as r:
        return json.loads(r.read().decode("utf-8"))


env = call("POST", "/auth/login", {"username": "learner_001", "password": "123456"})
TOKEN = env["data"]["token"]

# 手动填写路径：选岗（大模型应用工程师）→ 完成诊断（matchPct 由前端 Gap 计算，这里取演示值）
env = call("POST", "/profile/diagnosis-complete", {"targetJobName": "大模型应用工程师", "matchPct": 33})
assert env["code"] == 0 and env["data"]["hasDiagnosed"] is True
j = call("GET", "/journey")["data"]
print(f"[finish :{PORT}] diagnosis-complete code 0 hasDiagnosed=True -> "
      f"journey.targetJob={j.get('targetJobName')} matchPct={j.get('matchPct')}")
