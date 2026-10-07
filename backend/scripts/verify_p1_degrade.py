# -*- coding: utf-8 -*-
"""联调-P1 降级验证：JOB_MARKET_OFFLINE=true（8001）下 5.2 应回 HTTP 200 + code 2002 + offline:true。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8001/api/v1"

req = urllib.request.Request(BASE + "/auth/login", method="POST")
req.add_header("Content-Type", "application/json")
body = json.dumps({"username": "learner_001", "password": "123456"}).encode("utf-8")
with urllib.request.urlopen(req, body) as r:
    token = json.loads(r.read())["data"]["token"]

req = urllib.request.Request(BASE + "/job-market/llm-app")
req.add_header("Authorization", "Bearer " + token)
with urllib.request.urlopen(req) as r:
    env = json.loads(r.read().decode("utf-8"))
    print(f"[degrade] http={r.status} code={env['code']} offline={env['data'].get('offline')} "
          f"id={env['data']['id']} fetchedAt={env['data']['fetchedAt']} msg={env['message']}")
assert r.status == 200 and env["code"] == 2002 and env["data"]["offline"] is True
print("DEGRADE CHECK PASSED")
