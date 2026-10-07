# -*- coding: utf-8 -*-
"""联调-P1 验证脚本：以浏览器等价的 UTF-8 JSON 请求实测 P1 六接口（同 P0 约定，避开 Windows shell 编码坑）。"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"
TOKEN = None


def call(method, path, body=None, expect_codes=(0,)):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    with urllib.request.urlopen(req, data=data) as r:
        env = json.loads(r.read().decode("utf-8"))
    assert env["code"] in expect_codes, f"{path} -> code={env['code']} msg={env['message']}"
    return env


# 0. 登录取 token
env = call("POST", "/auth/login", {"username": "learner_001", "password": "123456"})
TOKEN = env["data"]["token"]
print(f"[login]     code 0  role={env['data']['user']['role']}")

# 1. 热门岗位列表（5.1）
env = call("GET", "/job-market/hot")
print(f"[hot]       code 0  {[j['id'] for j in env['data']]}")

# 2. 岗位快照（5.2）
env = call("GET", "/job-market/llm-app")
d = env["data"]
print(f"[snapshot]  code 0  id={d['id']} heatPct={d['heatPct']} openings={d['openings']} "
      f"offline_key={'offline' in d} skills={len(d['skills'])} radar_dims={len(d['radar'])}")

# 3. 知识图谱（26）
env = call("GET", "/knowledge-graph")
g = env["data"]
nn = next(n for n in g["nodes"] if n["id"] == "nn")
print(f"[graph]     code 0  nodes={len(g['nodes'])} links={len(g['links'])} "
      f"categories={[c['name'] for c in g['categories']]} nn={{category:{nn['category']},value:{nn['value']}}}")

# 4. 学情概览（29）
env = call("GET", "/dashboard/overview")
o = env["data"]
print(f"[overview]  code 0  score={o['overall_score']} level={o['overall_level']} "
      f"coverage={o['knowledge_graph_coverage']} learned={o['learned_resources']} "
      f"betterThan={o['comparison']['betterThanPct']} radar_dims={len(o['radar']['dimensions'])} "
      f"strong={[(t['name'], t['mastery']) for t in o['strong_topics']]} "
      f"target={o['targetSummary']}")

# 5. 外部资源（21）
env = call("GET", "/resource/external/nn")
rs = env["data"]
print(f"[external]  code 0  n={len(rs)} top={{id:{rs[0]['id']},type:{rs[0]['type']},"
      f"relevance:{rs[0]['relevance']},credibility:{rs[0]['credibility']}}} "
      f"desc_order={all(rs[i]['relevance'] >= rs[i+1]['relevance'] for i in range(len(rs)-1))}")

# 6. 错题强化（25）——上报后端真实题库 id（联调时测验题来自 GET /quiz/nn）
env = call("POST", "/reinforce", {"kpId": "nn", "wrongQuestionIds": ["nn_q1", "nn_q3"]})
cards = env["data"]
ok = all(
    c["practice"]["correct_answer"] in [op["option_id"] for op in c["practice"]["options"]]
    if isinstance(c["practice"]["correct_answer"], str) else True
    for c in cards
)
print(f"[reinforce] code 0  cards={len(cards)} points={[c['point'] for c in cards]} "
      f"practice_ids={[c['practice']['question_id'] for c in cards]} answers_self_consistent={ok}")

print("ALL P1 BROWSER-EQUIVALENT CHECKS PASSED (code 0)")
sys.exit(0)
