#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""依赖解析回归验证（R4.5.1）—— 锁住「requirements 不能再次出现无解组合」。

## 为什么需要它

R4.5 QA 验收发现：`langgraph~=1.0.0` 与 `langgraph-checkpoint-sqlite~=1.0.0`
要求的 `langgraph-checkpoint` 区间**无交集**，导致 `pip install -r requirements.txt`
抛 `ResolutionImpossible` → **整棵依赖树零安装 → 233 个测试全部无法执行**。

但现有测试 `test_requirements_locks_langgraph_and_langchain` **只检查声明文本**，
**证明不了 pip 真能解析成功**。本脚本补的就是这一环。

## 为什么不在 pytest 里跑

pip resolver 需要网络与数分钟元数据下载，塞进单元测试会：
- 拖垮测试套件速度、引入网络依赖、让"测试失败"和"网络失败"混在一起。
故按规格 §8 采用**独立 QA verification 脚本**（非第二套 Runtime，不碰业务代码）。

## 用法

    # 默认：只做解析（--dry-run），**不安装、不污染任何环境**
    python scripts/verify_dependency_resolution.py

    # 附加：在临时隔离 venv 中真装 + import + 图编译（约 1–3 分钟）
    python scripts/verify_dependency_resolution.py --full

退出码：0 = 通过；非 0 = 依赖组合无解 / 关键包缺失。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = BACKEND_ROOT / "requirements.txt"

# 必须能在解析结果中出现的直接依赖
TRACKED = ("langgraph", "langgraph-checkpoint-sqlite", "langchain-core")
# 关键的传递依赖（用于打印，不强制 pin）
TRANSITIVE = ("langgraph-checkpoint", "langgraph-prebuilt")


def _pip_env() -> dict[str, str]:
    env = dict(os.environ)
    # 中文 Windows 下 pip 读 UTF-8 中文注释会 GBK 解码失败（R0 已定位）
    env["PYTHONUTF8"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return env


def _run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=_pip_env(), timeout=timeout, cwd=str(BACKEND_ROOT),
    )


def _extract(text: str, name: str) -> str | None:
    r"""从 pip 输出里抓某个包的版本。

    - 用负向边界避免 ``langgraph-checkpoint`` 误匹配到 ``langgraph-checkpoint-sqlite``；
    - 只取 ``\d+.\d+...`` 数字点分段，避免把 ``1.2.12-py3-none-any.whl.metadata``
      这类文件名后缀当成版本号。
    """
    pattern = re.compile(rf"(?<![\w-]){re.escape(name)}-(\d+(?:\.\d+)+)")
    match = pattern.search(text or "")
    return match.group(1) if match else None


def check_resolution(verbose: bool = False) -> bool:
    """核心检查：requirements.txt 能否被 pip resolver 一次性解出。"""
    print("=" * 78)
    print("[1/2] 解析验证：pip install --dry-run -r requirements.txt")
    print("=" * 78)
    proc = _run(
        [sys.executable, "-m", "pip", "install", "--dry-run", "-r", str(REQUIREMENTS)]
    )
    combined = f"{proc.stdout}\n{proc.stderr}"
    if verbose:
        print(combined[-4000:])

    if proc.returncode != 0:
        print("  [FAIL] 解析失败（resolver 返回非 0）")
        for line in combined.splitlines():
            if "ERROR" in line or "conflict" in line.lower() or "depends on" in line:
                print(f"    {line.strip()}")
        return False

    print("  [PASS] resolver 解析成功（未安装任何包）")
    print("  解析出的版本：")
    ok = True
    for name in TRACKED:
        version = _extract(combined, name)
        if version:
            print(f"    {name:32s} {version}")
        else:
            print(f"    {name:32s} ** 未在解析结果中出现 **")
            ok = False
    for name in TRANSITIVE:
        version = _extract(combined, name)
        print(f"    {name:32s} {version or '(未解析到)'}  [transitive]")
    return ok


def check_lock_style() -> bool:
    """锁风格检查：三个直接依赖必须带显式版本约束（不能是裸包名）。"""
    print("=" * 78)
    print("[2/2] 声明检查：直接依赖必须带版本约束")
    print("=" * 78)
    lines = [
        raw.strip() for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if raw.strip() and not raw.strip().startswith("#")
    ]

    def requirement(name: str) -> str | None:
        for line in lines:
            base = line.split(";")[0].strip()
            for sep in ("==", "~=", ">=", "<=", "[", " "):
                if sep in base:
                    base = base.split(sep, 1)[0].strip()
            if base == name:
                return line
        return None

    ok = True
    for name in TRACKED:
        line = requirement(name)
        if line is None:
            print(f"  [FAIL] {name}: requirements 中缺失")
            ok = False
        elif not any(op in line for op in ("==", "~=", ">=")):
            print(f"  [FAIL] {name}: 未锁版本 -> {line}")
            ok = False
        else:
            print(f"  [PASS] {name}: {line}")
    return ok


def check_isolated_install() -> bool:
    """可选：临时隔离环境真装 + import + 图编译（不碰项目 .venv）。"""
    print("=" * 78)
    print("[full] 隔离环境实装 + import + graph compile")
    print("=" * 78)
    import venv as venv_module

    tmp = Path(tempfile.mkdtemp(prefix="r451_verify_"))
    venv_path = tmp / "venv"
    try:
        # 用 in-process venv.EnvBuilder 创建，避免直接 spawn 独立解释器
        # （部分受限环境下 `CreateProcess(<base_python>)` 会被拒绝）。
        venv_module.EnvBuilder(with_pip=True, clear=True).create(str(venv_path))
        python = venv_path / ("Scripts" if os.name == "nt" else "bin") / (
            "python.exe" if os.name == "nt" else "python"
        )
        if not python.exists():
            print(f"  [FAIL] venv 解释器未生成: {python}")
            return False
        print("  [PASS] 隔离 venv 创建成功")

        upgrade = _run(
            [str(python), "-m", "pip", "install", "-q", "--upgrade", "pip"], timeout=600
        )
        if upgrade.returncode != 0:
            print(f"  [WARN] pip 升级失败（继续）: {upgrade.stderr[-300:]}")

        # 慢镜像易出现 IncompleteRead；加重试而非判失败（网络问题 ≠ 依赖问题）
        install = _run(
            [
                str(python), "-m", "pip", "install", "-q",
                "--retries", "15", "--timeout", "120",
                "-r", str(REQUIREMENTS),
            ],
            timeout=3600,
        )
        if install.returncode != 0:
            print(f"  [FAIL] 隔离安装失败:\n{install.stderr[-2000:]}")
            return False
        print("  [PASS] 隔离环境安装成功（requirements.txt 全量可装）")

        smoke = r'''
import importlib.metadata as m
for p in ("langgraph","langgraph-checkpoint","langgraph-checkpoint-sqlite","langchain-core"):
    print(f"    {p:32s} {m.version(p)}")
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from pydantic import BaseModel
def n(s): return {"x": 1}
g = StateGraph(dict)
g.add_node("a", n); g.add_node("b", n)
g.add_edge(START, "a"); g.add_edge("b", END)
g.add_conditional_edges("a", lambda s: "b", {"b": "b"})
c = g.compile()
out = list(c.stream({"z": 1}, config={"recursion_limit": 50}, stream_mode="values"))
assert len(out) >= 3 and out[-1].get("x") == 1
assert {"a", "b"} <= set(c.get_graph().nodes)
import sqlite3, tempfile, os
saver = SqliteSaver(sqlite3.connect(os.path.join(tempfile.mkdtemp(), "c.sqlite"), check_same_thread=False))
c2 = g.compile(checkpointer=saver)
r = c2.invoke({"z": 2}, config={"configurable": {"thread_id": "t"}, "recursion_limit": 50})
assert isinstance(r, dict)
class S(BaseModel):
    user_id: str
payload = convert_to_openai_tool(S)
assert payload["type"] == "function" and payload["function"]["name"] == "S"
assert AIMessage(content="", tool_calls=[{"name":"t","args":{},"id":"c","type":"tool_call"}]).tool_calls
assert ToolMessage(content="{}", tool_call_id="c").tool_call_id == "c"
assert HumanMessage(content="x").content == "x"
assert BaseTool is not None
print("    graph compile / stream(values) / checkpointer / langchain schema+messages  全部 OK")
'''
        smoke_run = _run([str(python), "-c", smoke], timeout=600)
        if smoke_run.returncode != 0:
            print(f"  [FAIL] import / graph compile 失败:\n{smoke_run.stdout}\n{smoke_run.stderr}")
            return False
        print(smoke_run.stdout.rstrip())
        print("  [PASS] import + graph compile + checkpointer + langchain API 全部可用")
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="R4.5.1 依赖解析回归验证")
    parser.add_argument("--full", action="store_true", help="附带隔离环境真装 + 编译验证")
    parser.add_argument("--verbose", action="store_true", help="打印 pip 原始输出")
    args = parser.parse_args()

    if not REQUIREMENTS.exists():
        print(f"[FAIL] 找不到 {REQUIREMENTS}")
        return 2

    ok_lock = check_lock_style()
    ok_resolve = check_resolution(verbose=args.verbose)
    if args.full:
        ok_full: bool | None = check_isolated_install()
    else:
        ok_full = None  # 未执行，不能报成 PASS

    print("=" * 78)
    print(f"RESULT: lock={'PASS' if ok_lock else 'FAIL'} "
          f"resolve={'PASS' if ok_resolve else 'FAIL'} "
          f"full={'PASS' if ok_full else ('FAIL' if ok_full is False else 'SKIP')}")
    if not args.full:
        print("       （full=SKIP：未执行隔离实装；加 --full 可跑 import + graph compile）")
    print("=" * 78)
    if ok_full is False:
        return 1
    return 0 if (ok_lock and ok_resolve) else 1


if __name__ == "__main__":
    raise SystemExit(main())
