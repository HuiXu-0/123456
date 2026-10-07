"""语音合成 TTS（接口文档第 8.9 节，新增）。

接口 45：
- POST /tts/synthesize  文字 → 自然中文神经语音 MP3（讲解视频/导学旁白配音）

成功：直接返回 ``audio/mpeg`` 二进制流（前端 ``fetch → blob → Audio`` 即可播放），
响应头 ``X-TTS-Cache: hit|miss`` 标识是否命中缓存、``X-TTS-Voice`` 标识音色；
失败/禁用：套用 1.2 统一信封返回 ``code 2002 / data.offline=true``（HTTP 200），
前端据此回落浏览器 speechSynthesis。无网/无密钥不崩（CLAUDE.md Mock-first 纪律）。

均需登录。文本为空 → 1001。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.core.config import settings
from app.core.envelope import fail
from app.core.security import get_current_user
from app.models.entities import User
from app.schemas.resource import TTSSynthesizeRequest
from app.services import tts as tts_service

router = APIRouter(tags=["tts"])


@router.post("/tts/synthesize")
async def tts_synthesize(
    body: TTSSynthesizeRequest,
    user: User = Depends(get_current_user),
):
    """文字 → MP3 语音流（接口文档 8.9）。"""
    if not (body.text or "").strip():
        return fail(code=1001, message="合成文本不能为空", status_code=400)
    try:
        audio, from_cache = await tts_service.synthesize(
            body.text, voice=body.voice, rate=body.rate, pitch=body.pitch
        )
    except tts_service.TTSUnavailable as exc:
        # 优雅降级：返回降级信封（HTTP 200 + offline 标记），前端回落浏览器 TTS
        return fail(
            code=2002,
            message=f"语音合成不可用，已降级：{exc}",
            data={"offline": True},
            status_code=200,
        )
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={
            "X-TTS-Cache": "hit" if from_cache else "miss",
            "X-TTS-Voice": body.voice or settings.tts_voice,
            "Cache-Control": "no-store",
        },
    )
