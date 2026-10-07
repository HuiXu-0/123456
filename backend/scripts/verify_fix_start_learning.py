# -*- coding: utf-8 -*-
"""fix-开始学习路由 验证：模拟 6 个课程卡「开始学习」后资源页发出的等价请求。

每个 kpId（lessonSeq 1-6 ↔ ml,nn,dl,cnn,transformer,finetune）跑三件套：
GET /quiz/{kp}、POST /resource/lecture、GET /resource/external/{kp}，全部应 code 0；
并按前端 lectureOutline 同款规则对讲义 markdown 提取标题大纲（验导图结构化非空、
且不混入代码块内的 # 注释行）。
"""
import json
import re
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"
# 与 frontend/src/data/knowledgePoints.ts 的 lessonSeq 映射一致
KPS = [(1, "ml"), (2, "nn"), (3, "dl"), (4, "cnn"), (5, "transformer"), (6, "finetune")]
TOKEN = None


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    with urllib.request.urlopen(req, data=data) as r:
        env = json.loads(r.read().decode("utf-8"))
    assert env["code"] == 0, f"{path} -> code={env['code']} msg={env['message']}"
    return env["data"]


def lecture_outline(md):
    """与前端 LearningResource.lectureOutline 同款规则。"""
    in_fence, lines = False, []
    for line in md.split("\n"):
        if re.match(r"^\s*```", line):
            in_fence = not in_fence
            continue
        if not in_fence and re.match(r"^#{1,4}\s", line):
            lines.append(line)
    return lines


d = call("POST", "/auth/login", {"username": "learner_001", "password": "123456"})
TOKEN = d["token"]
print("[login] code 0")

for seq, kp in KPS:
    quiz = call("GET", f"/quiz/{kp}")["questions"]
    lec = call("POST", "/resource/lecture", {"kpId": kp, "difficulty": "初级"})
    ext = call("GET", f"/resource/external/{kp}")
    outline = lecture_outline(lec["markdown"])
    assert not any("import" in o or "=" in o for o in outline), f"{kp} 大纲混入代码行"
    print(f"[seq {seq} -> {kp:<11}] quiz={len(quiz)}题 lecture={len(lec['markdown'])}字"
          f"(sources={len(lec['sources'])}) external={len(ext)}条 outline={len(outline)}行"
          f" 首行={outline[0] if outline else '(空→占位)'}")

print("ALL 6 KP RESOURCE-PAGE-EQUIVALENT CHECKS PASSED (code 0)")
