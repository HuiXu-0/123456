# -*- coding: utf-8 -*-
"""清理 dev 库累积的测试账号（保留种子 admin/learner_001）。

背景：测试套件曾直接跑在持久化 dev 库（backend/zhixue.db）上，注册类用例
（对话画像 dlg_*、改密 cpw_*、e2e_*/verify_*/curluser_* 等）逐次累积用户。
conftest 已做测试库隔离根治"未来累积"；本脚本一次性清掉"历史累积"。

策略：
- 保留种子用户 u_10000(admin) / u_10001(learner_001)，删除其余全部用户；
- 连带删除所有含 user_id 列的表中属于这些用户的行（mastery/journeys/
  workflow_traces/student_portraits，按 PRAGMA 动态识别，对未来新表也安全）；
- 共享缓存（resource_cache，按 kp+难度而非 user_id）与种子数据不动；
- 删除前自动备份 zhixue-<ts>.db.bak（仓库 .gitignore 已忽略 db 备份）。

安全热备份：用 SQLite 在线备份 API（`Connection.backup()`）而非 `shutil.copy2`
裸拷文件——后者在服务仍持有 db 时拷到的可能是不一致快照（WAL 模式下更会漏掉
未 checkpoint 的内容），且与运行中句柄相互干扰可触发 `disk I/O error`。在线备份
对运行中的库生成一致快照、互不干扰；本脚本所有写连接亦设 busy_timeout，锁竞争
时重试而非直接报错。服务运行时可安全执行本脚本。

用法：python scripts/cleanup_dev_test_accounts.py
"""
import os
import sqlite3
import sys
import time

DB = os.path.join(os.path.dirname(__file__), "..", "zhixue.db")
DB = os.path.abspath(DB)
SEED_IDS = ("u_10000", "u_10001")  # admin / learner_001

if not os.path.exists(DB):
    sys.exit(f"找不到 dev 库：{DB}")

# 1) 在线备份（一致快照，服务运行时安全；命名命中 .gitignore 的 *.db.bak）
ts = time.strftime("%Y%m%d-%H%M%S")
bak = os.path.join(os.path.dirname(DB), f"zhixue-{ts}.db.bak")
_src = sqlite3.connect(DB, timeout=30)
_dst = sqlite3.connect(bak)
try:
    with _dst:
        _src.backup(_dst)  # SQLite 原生在线备份，不裸拷文件
finally:
    _dst.close()
    _src.close()
print(f"[backup] {bak}")

# timeout=30：锁竞争时重试至多 30s 而非立即报错（服务并发访问同库时更稳）
con = sqlite3.connect(DB, timeout=30)
con.execute("PRAGMA busy_timeout=30000")
con.execute("PRAGMA foreign_keys=OFF")  # 手动按表清理，避免 FK 报错
cur = con.cursor()

# 2) 目标用户 = 非种子
targets = [uid for (uid,) in cur.execute(
    "SELECT id FROM users WHERE id NOT IN (?, ?)", SEED_IDS
)]
total_users = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
print(f"[scan] 用户总数 {total_users}，种子保留 {len(SEED_IDS)}，待删 {len(targets)}")
if not targets:
    print("[done] 无可清理账号（已干净）")
    con.close()
    sys.exit(0)

ph = ",".join("?" * len(targets))

# 3) 连带删除所有含 user_id 列的业务表（动态识别）
tables = [r[0] for r in cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
)]
deleted: dict[str, int] = {}
for t in tables:
    if t == "users":
        continue
    cols = [r[1] for r in cur.execute(f"PRAGMA table_info('{t}')")]
    if "user_id" not in cols:
        continue
    n = cur.execute(
        f"DELETE FROM {t} WHERE user_id IN ({ph})", targets
    ).rowcount
    if n:
        deleted[t] = n

# 4) 删用户
n_users = cur.execute(f"DELETE FROM users WHERE id IN ({ph})", targets).rowcount
con.commit()

# 5) 报告 + 校验种子完好
for t, n in sorted(deleted.items()):
    print(f"[del] {t:<22} {n} 行")
print(f"[del] {'users':<22} {n_users} 行")

remain = cur.execute("SELECT id, username, role FROM users ORDER BY id").fetchall()
print(f"[verify] 剩余用户 {len(remain)}：")
for uid, u, role in remain:
    print(f"         {uid}  {u}  ({role})")
seeds_ok = {uid for uid, _, _ in remain} == set(SEED_IDS)
con.close()
print("[OK] 种子完好、测试账号已清空" if seeds_ok else "[WARN] 剩余集合与种子不符，请核对")
sys.exit(0 if seeds_ok else 1)
