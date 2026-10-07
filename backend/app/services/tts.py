"""语音合成服务（edge-tts，Web 模式）。

移植自某桌面项目 voice.py 的**合成逻辑**，剥离桌宠端的 pygame 播放 / 状态机 /
队列 / 优先级，只保留"文字 → MP3 字节"这一纯函数能力，由前端负责播放：

- edge-tts 微软神经语音（默认 zh-CN-XiaoyiNeural，语速 -10%、音调 +2Hz），
  通过 ``edge_tts.Communicate`` 流式收集 audio 分片并合并为完整 MP3 bytes；
- 重试兜底：单次合成失败重试 ``settings.tts_max_retries`` 次再放弃；
- 本地缓存：以 ``md5(text|voice|rate|pitch)`` 为键把 MP3 落到 ``settings.tts_cache_dir``，
  命中直接读盘返回（避免重复联网合成，第二次明显更快）；缓存目录已入 .gitignore；
- 优雅降级：``tts_provider=none`` 或 edge-tts 联网失败 → 抛 ``TTSUnavailable``，
  上层 API 转 2002 降级信封，前端可回落浏览器 speechSynthesis。无网/无密钥不崩。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger("app.services.tts")


class TTSUnavailable(Exception):
    """TTS 不可用（provider=none / 文本为空 / 合成多次失败）→ 上层降级。"""


def _cache_dir() -> Path:
    d = Path(settings.tts_cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(text: str, voice: str, rate: str, pitch: str) -> str:
    raw = "|".join((text, voice, rate, pitch)).encode("utf-8")
    return hashlib.md5(raw).hexdigest()  # noqa: S324 仅作缓存键，非安全用途


def _cache_path(key: str) -> Path:
    return _cache_dir() / f"{key}.mp3"


async def _edge_once(text: str, voice: str, rate: str, pitch: str) -> bytes:
    """调用 edge-tts 流式收集一次完整 MP3（单次尝试，失败抛异常由上层重试）。"""
    import edge_tts  # 延迟导入：tts_provider=none 时无需安装亦可启动

    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    chunks: list[bytes] = []
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            chunks.append(chunk["data"])
    audio = b"".join(chunks)
    if not audio:
        raise RuntimeError("edge-tts 未返回任何音频分片")
    return audio


async def synthesize(
    text: str,
    *,
    voice: str | None = None,
    rate: str | None = None,
    pitch: str | None = None,
) -> tuple[bytes, bool]:
    """文字 → MP3 字节。

    返回 ``(mp3_bytes, from_cache)``。
    - ``tts_provider=none`` 或文本为空 → ``TTSUnavailable``；
    - 命中缓存 → 直接读盘返回，``from_cache=True``；
    - 否则联网合成（重试兜底）后写缓存返回，``from_cache=False``；多次失败 → ``TTSUnavailable``。
    """
    if (settings.tts_provider or "edge").lower() == "none":
        raise TTSUnavailable("TTS 已禁用（tts_provider=none）")

    text = (text or "").strip()
    if not text:
        raise TTSUnavailable("合成文本为空")
    # 折叠多余空白，约束长度（防超长滥用，超出截断而非报错）
    text = re.sub(r"\s+", " ", text)[: settings.tts_max_chars]

    voice = voice or settings.tts_voice
    rate = rate or settings.tts_rate
    pitch = pitch or settings.tts_pitch

    key = _cache_key(text, voice, rate, pitch)
    path = _cache_path(key)
    if path.exists():
        try:
            data = path.read_bytes()
            if data:
                return data, True
        except OSError:  # 缓存读失败不致命，继续重新合成
            logger.warning("TTS 缓存读取失败，重新合成：%s", path)

    last_exc: Exception | None = None
    for attempt in range(1, max(1, settings.tts_max_retries) + 1):
        try:
            audio = await asyncio.wait_for(
                _edge_once(text, voice, rate, pitch),
                timeout=settings.tts_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 联网/超时失败 → 重试后降级
            last_exc = exc
            logger.warning("edge-tts 合成失败（第 %d/%d 次）：%s", attempt, settings.tts_max_retries, exc)
            continue
        try:
            path.write_bytes(audio)  # 写缓存（失败不致命）
        except OSError:
            logger.warning("TTS 缓存写入失败：%s", path)
        return audio, False

    raise TTSUnavailable(f"edge-tts 合成失败（已重试 {settings.tts_max_retries} 次）：{last_exc}")
