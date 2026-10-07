"""DeepSeek 真实调用通道（B5-b，OpenAI 兼容 SDK）。

- `chat(prompt, system=None)`：单轮补全，返回纯文本；超时/限流/网络/鉴权等
  一切上游异常统一包装为 `LLMGenerationError`（路由层映射 code 2001 / HTTP 500，
  见接口文档 1.3），不向上层泄漏 SDK 异常类型；
- Key 经 settings.deepseek_api_key（backend/.env 的 DEEPSEEK_API_KEY）注入，
  缺省为空——此时调用即抛 LLMGenerationError，mock provider 不经过本模块
  （无任何 Key 必须能跑通全链路的纪律由 llm.py 的 provider 分支保证）。
"""
from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger("app.core.llm_deepseek")


class LLMGenerationError(Exception):
    """LLM/Agent 生成失败（→ code 2001 / HTTP 500，接口文档 1.3）。"""


_client = None  # openai.OpenAI 单例（延迟创建，避免无 Key 环境 import 即报错）


def _get_client():
    global _client
    if _client is None:
        if not settings.deepseek_api_key:
            raise LLMGenerationError(
                "DEEPSEEK_API_KEY 未配置：请在 backend/.env 中设置，"
                "或将 LLM_PROVIDER 切回 mock"
            )
        import httpx
        from openai import OpenAI

        _client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=1,
            # 连接级重试：api.deepseek.com 多 A 记录中个别 IP 偶发 TLS 重置，
            # httpx 默认单次连接会直接失败（curl 因多 IP 回退成功）；
            # transport retries 在连接阶段自动换地址重试，保证可达性。
            http_client=httpx.Client(
                transport=httpx.HTTPTransport(retries=2),
                timeout=settings.llm_timeout_seconds,
            ),
        )
    return _client


def _build_messages(
    prompt: str, system: str | None, history: list[dict[str, str]] | None
) -> list[dict[str, str]]:
    """组装 messages：system + 既往多轮 history（B7-a tutor 会话）+ 本轮 user。"""
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.extend(history or [])
    messages.append({"role": "user", "content": prompt})
    return messages


def chat(
    prompt: str,
    system: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> str:
    """chat 补全（history 可选，B7-a 多轮）。任何上游异常 → LLMGenerationError（含超时）。"""
    client = _get_client()
    messages = _build_messages(prompt, system, history)
    try:
        resp = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=messages,
            temperature=settings.llm_temperature,
        )
        content = (resp.choices[0].message.content or "").strip()
    except LLMGenerationError:
        raise
    except Exception as exc:  # noqa: BLE001 SDK 异常族繁多，统一收敛到 2001
        logger.warning("DeepSeek 调用失败：%s", exc)
        raise LLMGenerationError(f"DeepSeek 调用失败：{exc}") from exc
    if not content:
        raise LLMGenerationError("DeepSeek 返回空内容")
    return content


def chat_stream(
    prompt: str,
    system: str | None = None,
    history: list[dict[str, str]] | None = None,
):
    """流式 chat 补全（B7-a tutor SSE）：逐 chunk 产出增量文本。

    生成器：调用方逐 delta 透传给 SSE；上游异常（含流中断）统一
    LLMGenerationError，由调用方转 event: error（15.4）。
    """
    client = _get_client()
    messages = _build_messages(prompt, system, history)
    try:
        stream = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=messages,
            temperature=settings.llm_temperature,
            stream=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("DeepSeek 流式调用失败：%s", exc)
        raise LLMGenerationError(f"DeepSeek 流式调用失败：{exc}") from exc
    got_content = False
    try:
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                got_content = True
                yield delta
    except LLMGenerationError:
        raise
    except Exception as exc:  # noqa: BLE001 流中断也收敛到 2001
        logger.warning("DeepSeek 流式传输中断：%s", exc)
        raise LLMGenerationError(f"DeepSeek 流式传输中断：{exc}") from exc
    if not got_content:
        raise LLMGenerationError("DeepSeek 流式返回空内容")
