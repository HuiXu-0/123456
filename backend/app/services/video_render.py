"""讲解视频服务端渲染（mp4）——增强项，非必需（CC-video-mp4 第二步）。

把现有 8.3 分镜（scenes/narration）+ 每段 edge-tts 旁白，经 Remotion server-side
rendering 烧录成「画面 + 旁白」一体的真 mp4，供前端原生 ``<video>`` 播放 / 下载。

降级纪律（保命）：渲染是增强不是替换——总开关关闭 / 无渲染能力（Node、@remotion/renderer
依赖、无头浏览器任一缺失）/ 渲染失败时，``videoUrl`` 维持 null，前端回落现有
@remotion/player + edge-tts 实时播放，不破坏「无 Key 全链路跑通」。

机制（复用现有缓存 + 异步任务设施）：
- 渲染产物 URL 回写进 ``ResourceCache(kind=video[@provider])`` 的 payload.videoUrl
  （该键本就有 videoUrl 字段，仅由 null → mp4 URL，不改任何接口字段）；命中即「秒出」；
- 单次渲染 ~10-15s，**不入请求**：``POST /resource/video/render`` 起 taskId
  （pending→running→succeeded/failed），后台合成各段 TTS → node 子进程 renderMedia
  → 落盘静态目录 → 回写 videoUrl；前端轮询 ``GET /tasks/{taskId}`` 拿 result.videoUrl。

音画同步：渲染前对每段 narration 调 ``services.tts`` 合成 MP3，用 mutagen 量出时长，
换算帧数写入 ``scenes[].durationInFrames``，并把该段音频以 data:URI 经 inputProps 传给
渲染合成（每段一条 <Audio>）——画面随各段旁白逐段对齐，长旁白不被固定时间轴截断。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import math
import shutil
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.llm import get_llm
from app.core.tasks import Task, get_task, submit
from app.models.entities import ResourceCache
from app.services import resource as resource_service

logger = logging.getLogger("app.services.video_render")

# 与前端 LectureVideo / 后端 resource 对齐
_FPS = 30
_WIDTH = 1280
_HEIGHT = 720
_FALLBACK_SCENE_FRAMES = 180  # 旁白缺失/量不出时长时的兜底段时长

# 渲染键 → 进行中的 taskId（去重：同一视频重复触发复用同一渲染任务）
_inflight: dict[str, str] = {}


# ---- 路径与能力探测 ---------------------------------------------------------
def _root() -> Path:
    return Path(settings.video_render_dir).resolve()


def _out_dir() -> Path:
    """mp4 产物目录（静态可访问、可下载）。"""
    d = _root() / "out"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _work_dir() -> Path:
    d = _root() / "work"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bundle_dir() -> Path:
    return _root() / "bundle"


def _frontend_dir() -> Path:
    return Path(settings.video_frontend_dir).resolve()


def _render_script() -> Path:
    return _frontend_dir() / "scripts" / "render-lecture.mjs"


def _detect_headless_shell() -> str | None:
    """显式配置优先；否则自动探测复用本机 Playwright chrome-headless-shell。"""
    if settings.video_render_browser:
        return settings.video_render_browser if Path(settings.video_render_browser).exists() else None
    import os

    base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if not base.exists():
        return None
    # chromium_headless_shell-<rev>/chrome-headless-shell-win64/chrome-headless-shell.exe
    hits = sorted(base.glob("chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell*"))
    for p in hits:
        if p.suffix in ("", ".exe"):
            return str(p)
    return str(hits[-1]) if hits else None


def render_browser() -> str | None:
    return _detect_headless_shell()


def render_available() -> tuple[bool, str]:
    """是否具备服务端渲染能力。返回 (可用, 原因)；不可用时原因用于日志/降级说明。"""
    if not settings.video_render_enabled:
        return False, "video_render_enabled=false"
    if shutil.which(settings.video_node_bin) is None:
        return False, f"node not found ({settings.video_node_bin})"
    if not (_frontend_dir() / "node_modules" / "@remotion" / "renderer").exists():
        return False, "@remotion/renderer not installed"
    if not _render_script().exists():
        return False, "render-lecture.mjs missing"
    if render_browser() is None:
        return False, "headless browser not found"
    try:
        import mutagen  # noqa: F401
    except Exception:  # noqa: BLE001
        return False, "mutagen not installed"
    return True, "ok"


def static_mount() -> tuple[str, str] | None:
    """供 main.py 挂载静态目录：(url_path, directory)。挂在 api_prefix 下以复用前端 /api 代理。"""
    return (f"{settings.api_prefix}/media/video", str(_out_dir()))


# ---- 缓存键 / URL -----------------------------------------------------------
def _cache_kind() -> str:
    llm = get_llm()
    return "video" if llm.is_mock else f"video@{llm.provider}"


def _file_stem(kp_id: str, difficulty: str, kind: str) -> str:
    raw = f"{kp_id}|{difficulty}|{kind}".encode("utf-8")
    return "v_" + hashlib.md5(raw).hexdigest()[:16]  # noqa: S324 仅作文件名键


def _url_for(filename: str) -> str:
    return f"{settings.api_prefix}/media/video/{filename}"


def _cache_row(db, kp_id: str, difficulty: str, kind: str) -> ResourceCache | None:
    return (
        db.query(ResourceCache)
        .filter(
            ResourceCache.kp_id == kp_id,
            ResourceCache.difficulty == difficulty,
            ResourceCache.kind == kind,
        )
        .one_or_none()
    )


# ---- 对外入口 ---------------------------------------------------------------
def start_render(db, user_id: str, kp_id: str, difficulty: str) -> dict[str, Any]:
    """触发/复用一次讲解视频渲染。返回 { status, taskId, videoUrl }。

    status：ready（已有 mp4，直接播放/下载）| rendering（后台渲染中，轮询 taskId）|
    unavailable（无渲染能力/降级，前端回落实时 Player+TTS）。

    幂等：先确保 8.3 分镜缓存就绪（复用 resource.generate_video），命中已渲染 mp4 → 秒出；
    否则在具备能力时起后台渲染任务（同视频进行中则复用）。任何异常路径均回 unavailable，
    绝不阻断「拿不到 mp4 也能看」的兜底。
    """
    # 先确保分镜脚本就绪（也校验 kp/难度；不存在/非法由上层捕获对应异常）。
    payload = resource_service.generate_video(db, user_id, kp_id, difficulty)
    kind = _cache_kind()
    stem = _file_stem(kp_id, difficulty, kind)
    filename = f"{stem}.mp4"
    out_path = _out_dir() / filename

    # 命中已渲染产物（缓存回写过 videoUrl 且文件在）→ 秒出
    if payload.get("videoUrl") and out_path.exists():
        return {"status": "ready", "taskId": None, "videoUrl": payload["videoUrl"]}
    if out_path.exists():
        # 文件在但缓存未回写（如重启）→ 回写并秒出
        _write_back_url(kp_id, difficulty, kind, _url_for(filename))
        return {"status": "ready", "taskId": None, "videoUrl": _url_for(filename)}

    ok, reason = render_available()
    if not ok:
        logger.info("视频渲染不可用，降级实时播放：%s", reason)
        return {"status": "unavailable", "taskId": None, "videoUrl": None}

    # 进行中的同一渲染任务 → 复用
    existing = _inflight.get(stem)
    if existing:
        t = get_task(existing)
        if t is not None and t.status in ("pending", "running"):
            return {"status": "rendering", "taskId": existing, "videoUrl": None}

    title = payload["title"]
    scenes = payload["scenes"]
    browser = render_browser()

    async def _worker(task: Task) -> dict[str, Any]:
        try:
            return await _do_render(task, kp_id, difficulty, kind, title, scenes, out_path, filename, browser)
        finally:
            _inflight.pop(stem, None)

    task = submit(_worker)
    _inflight[stem] = task.task_id
    return {"status": "rendering", "taskId": task.task_id, "videoUrl": None}


def _write_back_url(kp_id: str, difficulty: str, kind: str, url: str) -> None:
    """把渲染产物 URL 回写进 ResourceCache(kind=video[@provider]).payload.videoUrl。"""
    db = SessionLocal()
    try:
        row = _cache_row(db, kp_id, difficulty, kind)
        if row is not None:
            row.payload = {**row.payload, "videoUrl": url}
            db.commit()
    finally:
        db.close()


async def _do_render(
    task: Task,
    kp_id: str,
    difficulty: str,
    kind: str,
    title: str,
    scenes: list[dict[str, Any]],
    out_path: Path,
    filename: str,
    browser: str | None,
) -> dict[str, Any]:
    """实际渲染：各段 TTS→量时长→data:URI → 写配置 → node 子进程 renderMedia → 回写。"""
    from mutagen.mp3 import MP3  # 延迟导入（能力探测已确保可用）
    from app.services import tts

    task.progress = 5
    render_scenes: list[dict[str, Any]] = []
    n = len(scenes)
    for i, s in enumerate(scenes):
        narration = (s.get("narration") or "").strip()
        audio_uri: str | None = None
        frames = _FALLBACK_SCENE_FRAMES
        if narration:
            mp3, _from_cache = await tts.synthesize(narration)  # 失败抛 TTSUnavailable → 任务 failed → 前端兜底
            import io

            seconds = float(MP3(io.BytesIO(mp3)).info.length)
            frames = max(1, math.ceil(seconds * _FPS))
            audio_uri = "data:audio/mpeg;base64," + base64.b64encode(mp3).decode("ascii")
        render_scenes.append(
            {
                "title": s["title"],
                "points": list(s["points"]),
                "narration": narration,
                "durationInFrames": frames,
                "audio": audio_uri,
            }
        )
        task.progress = 5 + int(35 * (i + 1) / max(1, n))  # 旁白合成占 ~40%

    cfg = {
        "title": title,
        "scenes": render_scenes,
        "fps": _FPS,
        "width": _WIDTH,
        "height": _HEIGHT,
        "outputPath": str(out_path),
        "bundleDir": str(_bundle_dir()),
        "browserExecutable": browser or "",
    }
    cfg_path = _work_dir() / f"cfg_{out_path.stem}.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    task.progress = 45
    rc, tail = await _run_node(cfg_path)
    try:
        cfg_path.unlink(missing_ok=True)
    except OSError:
        pass
    if rc != 0 or not out_path.exists():
        raise RuntimeError(f"renderMedia 失败（exit={rc}）：{tail}")

    url = _url_for(filename)
    _write_back_url(kp_id, difficulty, kind, url)
    task.progress = 100
    logger.info("讲解视频渲染完成：%s -> %s", kp_id, url)
    return {"videoUrl": url}


def _run_node_blocking(cfg_path: Path) -> tuple[int, str]:
    """同步跑 node 渲染脚本，返回 (returncode, 末尾输出)。在工作线程中调用，勿直接 await。"""
    import subprocess

    try:
        proc = subprocess.run(
            [settings.video_node_bin, str(_render_script()), str(cfg_path)],
            cwd=str(_frontend_dir()),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=settings.video_render_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return 124, f"render timeout after {settings.video_render_timeout_seconds}s"
    text = (proc.stdout or b"").decode("utf-8", errors="replace")
    # 末尾若干行（含 RENDER_OK/RENDER_ERR 与 webpack/remotion 报错）便于诊断
    tail = "\n".join(text.strip().splitlines()[-12:])
    return proc.returncode, tail


async def _run_node(cfg_path: Path) -> tuple[int, str]:
    """跑 node 渲染脚本，返回 (returncode, 末尾输出)。

    渲染是 CPU/IO 重的阻塞子进程：放到工作线程跑（asyncio.to_thread），既不阻塞事件循环，
    又规避 Windows 上 SelectorEventLoop 不支持 asyncio 子进程（NotImplementedError）的坑。
    """
    return await asyncio.to_thread(_run_node_blocking, cfg_path)
