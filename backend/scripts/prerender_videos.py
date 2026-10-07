"""讲解视频批量预渲染（一次性运维脚本，不改任何接口签名）。

把**学习路径核心知识点 × 关键难度**的讲解视频提前服务端渲染成一体 mp4 并落盘 +
回写 ResourceCache(kind=video[@provider]).payload.videoUrl，使前端打开即命中「秒出」，
不必现场等 10-15s。复用 video_render.start_render（与在线接口完全同一条渲染管线、同一缓存键
(kp_id, difficulty, kind)），因此预渲染产物与前端 POST /resource/video/render 命中的是同一份。

用法（在 backend/ 下，激活同一环境）：
  python scripts/prerender_videos.py inspect            # 只读：列出各 (kp×难度) 视频缓存命中情况
  python scripts/prerender_videos.py run                # 渲染默认覆盖集（核心 6 KP × 初级）
  python scripts/prerender_videos.py run --diffs all    # 核心 6 KP × 入门/初级/高级（全档）
  python scripts/prerender_videos.py run --diffs 初级,高级
  python scripts/prerender_videos.py run --kps nn,dl    # 仅指定知识点

降级纪律：无渲染能力（总开关关 / Node / @remotion/renderer / 无头浏览器 / mutagen 任一缺失）
时打印 unavailable 即退出，不报错——前端仍回落实时 Player + TTS。渲染串行执行（每次起一个 node
子进程），避免并发把机器压垮；跑完请确认无残留 node 进程。
"""
from __future__ import annotations

import asyncio
import io
import sys
import time

from app.core.database import SessionLocal
from app.core.llm import get_llm
from app.core.tasks import get_task
from app.models.entities import ResourceCache
from app.services import video_render as vr

# 学习路径核心知识点（与 frontend/src/data/knowledgePoints.ts 的 6 个 lessonSeq 对齐）
CORE_KPS = ["ml", "nn", "dl", "cnn", "transformer", "finetune"]
# 关键难度：默认仅「初级」（前端讲义/视频默认档）；--diffs all 扩到全三档
DEFAULT_DIFFS = ["初级"]
ALL_DIFFS = ["入门", "初级", "高级"]
# 空画像基线用户（tier=None）：与在线请求一致地走基线渲染键
BASE_USER = "u_10001"


def _cache_kind() -> str:
    llm = get_llm()
    return "video" if llm.is_mock else f"video@{llm.provider}"


def _hit(db, kp_id: str, difficulty: str, kind: str) -> str | None:
    row = (
        db.query(ResourceCache)
        .filter(
            ResourceCache.kp_id == kp_id,
            ResourceCache.difficulty == difficulty,
            ResourceCache.kind == kind,
        )
        .one_or_none()
    )
    if row is None:
        return None
    return (row.payload or {}).get("videoUrl")


def cmd_inspect(kps: list[str], diffs: list[str]) -> None:
    kind = _cache_kind()
    db = SessionLocal()
    try:
        print(f"渲染键 kind={kind}  覆盖集 {len(kps)}×{len(diffs)}={len(kps) * len(diffs)}")
        print(f"{'kp':12} {'diff':6} | videoUrl")
        print("-" * 70)
        hit = 0
        for kp_id in kps:
            for diff in diffs:
                url = _hit(db, kp_id, diff, kind)
                if url:
                    hit += 1
                print(f"{kp_id:12} {diff:6} | {url or '— 未渲染'}")
        print(f"\n已命中 {hit}/{len(kps) * len(diffs)}")
    finally:
        db.close()


async def _render_one(kp_id: str, difficulty: str) -> tuple[str, str | None]:
    """触发一次渲染并等待完成。返回 (状态, videoUrl)。"""
    db = SessionLocal()
    try:
        res = vr.start_render(db, BASE_USER, kp_id, difficulty)
    finally:
        db.close()

    status = res["status"]
    if status == "ready":
        return "ready", res["videoUrl"]
    if status == "unavailable":
        return "unavailable", None

    # rendering：轮询后台任务直至 succeeded/failed（单次渲染 ~10-15s，留足超时余量）
    task_id = res["taskId"]
    for _ in range(180):  # 180 × 2s = ~6min 上限
        t = get_task(task_id)
        if t is None:
            return "lost", None
        if t.status == "succeeded":
            return "rendered", (t.result or {}).get("videoUrl")
        if t.status == "failed":
            msg = (t.error or {}).get("message", "")
            return f"failed: {msg}", None
        await asyncio.sleep(2)
    return "timeout", None


async def cmd_run(kps: list[str], diffs: list[str]) -> None:
    ok, reason = vr.render_available()
    if not ok:
        print(f"渲染不可用（{reason}）→ 跳过预渲染，前端回落实时 Player+TTS。")
        return
    kind = _cache_kind()
    total = len(kps) * len(diffs)
    print(f"渲染键 kind={kind}  覆盖集 {len(kps)}×{len(diffs)}={total}  浏览器={vr.render_browser()}")
    print("-" * 70)
    done = skipped = failed = 0
    t_all = time.time()
    for kp_id in kps:
        for diff in diffs:
            t0 = time.time()
            try:
                status, url = await _render_one(kp_id, diff)
            except Exception as exc:  # noqa: BLE001
                status, url = f"error: {type(exc).__name__}: {exc}", None
            dt = round(time.time() - t0, 1)
            if status == "ready":
                skipped += 1
                tag = "命中(已存在)"
            elif status == "rendered":
                done += 1
                tag = "已渲染"
            else:
                failed += 1
                tag = status
            print(f"{kp_id:12} {diff:6} | {tag:14} {dt:>5}s  {url or ''}")
            sys.stdout.flush()
    print("-" * 70)
    print(
        f"完成：新渲染 {done}  已存在 {skipped}  失败 {failed}  / 共 {total}  "
        f"耗时 {round(time.time() - t_all, 1)}s"
    )


def _parse_diffs(raw: str | None) -> list[str]:
    if not raw or raw == "default":
        return DEFAULT_DIFFS
    if raw == "all":
        return ALL_DIFFS
    picked = [d.strip() for d in raw.split(",") if d.strip()]
    bad = [d for d in picked if d not in ALL_DIFFS]
    if bad:
        raise SystemExit(f"非法难度 {bad}，应为 {ALL_DIFFS} 或 all")
    return picked


def _parse_kps(raw: str | None) -> list[str]:
    if not raw:
        return CORE_KPS
    picked = [k.strip() for k in raw.split(",") if k.strip()]
    bad = [k for k in picked if k not in CORE_KPS]
    if bad:
        raise SystemExit(f"非核心知识点 {bad}，应在 {CORE_KPS} 内")
    return picked


def main() -> None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    args = sys.argv[1:]
    cmd = args[0] if args else "inspect"
    # 极简参数解析：--diffs X / --kps Y
    diffs_raw = kps_raw = None
    for i, a in enumerate(args):
        if a == "--diffs" and i + 1 < len(args):
            diffs_raw = args[i + 1]
        elif a == "--kps" and i + 1 < len(args):
            kps_raw = args[i + 1]
    kps = _parse_kps(kps_raw)
    diffs = _parse_diffs(diffs_raw)

    if cmd == "inspect":
        cmd_inspect(kps, diffs)
    elif cmd == "run":
        asyncio.run(cmd_run(kps, diffs))
    else:
        raise SystemExit(f"未知命令 {cmd}，应为 inspect | run")


if __name__ == "__main__":
    main()
