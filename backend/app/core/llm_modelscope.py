"""魔搭 ModelScope API-Inference 真实调用通道（模型管理，OpenAI 兼容 SDK）。

魔搭推理 API 兼容 OpenAI 协议——与 llm_deepseek 同构：base_url 指向
``settings.modelscope_base_url``、Key 经 MODELSCOPE_API_KEY 注入、model 由
调用方（llm_transport 按注册表当前模型）逐次传入。

- 缺 Key / 超时 / 上游异常一律收敛为 ``LLMGenerationError``，由 llm_transport
  捕获后**回落默认 DeepSeek**（再不行走 llm.py 各方法 mock 兜底 / 路由 2001），绝不崩；
- Qwen3 系列在魔搭上默认开思考模式且非流式调用会被拒，按官方口径显式关闭
  （``extra_body={"enable_thinking": False}``），流式则无需处理。
"""
from __future__ import annotations

import logging

from app.core.config import settings
from app.core.llm_deepseek import LLMGenerationError  # 统一异常类（→ code 2001）

logger = logging.getLogger("app.core.llm_modelscope")

_client = None  # openai.OpenAI 单例（同 Key/base_url 下全模型共用；延迟创建）


def _get_client():
    global _client
    if _client is None:
        if not settings.modelscope_api_key:
            raise LLMGenerationError(
                "MODELSCOPE_API_KEY 未配置：请在 backend/.env 中设置魔搭访问令牌，"
                "或切回默认 DeepSeek 模型"
            )
        import httpx
        from openai import OpenAI

        _client = OpenAI(
            api_key=settings.modelscope_api_key,
            base_url=settings.modelscope_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=1,
            # 与 llm_deepseek 同款连接级重试：连接阶段自动换地址重试，保证可达性
            http_client=httpx.Client(
                transport=httpx.HTTPTransport(retries=2),
                timeout=settings.llm_timeout_seconds,
            ),
        )
    return _client


def reset_client() -> None:
    """重建客户端（Key/base_url 配置变更或测试隔离用）。"""
    global _client
    _client = None


def _build_messages(
    prompt: str, system: str | None, history: list[dict[str, str]] | None
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.extend(history or [])
    messages.append({"role": "user", "content": prompt})
    return messages


def _extra_body(model: str, stream: bool) -> dict:
    """Qwen3 非流式须显式关思考模式（魔搭 OpenAI 兼容层约定），其余模型无需附加参数。"""
    if not stream and "qwen3" in model.lower():
        return {"enable_thinking": False}
    return {}


def chat(
    prompt: str,
    system: str | None = None,
    history: list[dict[str, str]] | None = None,
    *,
    model: str,
) -> str:
    """chat 补全（model = 魔搭 model_id，如 ZhipuAI/GLM-4.6）。异常 → LLMGenerationError。"""
    client = _get_client()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=_build_messages(prompt, system, history),
            temperature=settings.llm_temperature,
            extra_body=_extra_body(model, stream=False),
        )
        content = (resp.choices[0].message.content or "").strip()
    except LLMGenerationError:
        raise
    except Exception as exc:  # noqa: BLE001 SDK 异常族繁多，统一收敛
        logger.warning("魔搭模型 %s 调用失败：%s", model, exc)
        raise LLMGenerationError(f"魔搭模型 {model} 调用失败：{exc}") from exc
    if not content:
        raise LLMGenerationError(f"魔搭模型 {model} 返回空内容")
    return content


def chat_stream(
    prompt: str,
    system: str | None = None,
    history: list[dict[str, str]] | None = None,
    *,
    model: str,
):
    """流式 chat 补全：逐 chunk 产出增量文本。异常（含流中断）→ LLMGenerationError。"""
    client = _get_client()
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=_build_messages(prompt, system, history),
            temperature=settings.llm_temperature,
            stream=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("魔搭模型 %s 流式调用失败：%s", model, exc)
        raise LLMGenerationError(f"魔搭模型 {model} 流式调用失败：{exc}") from exc
    got_content = False
    try:
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                got_content = True
                yield delta
    except LLMGenerationError:
        raise
    except Exception as exc:  # noqa: BLE001 流中断也收敛
        logger.warning("魔搭模型 %s 流式传输中断：%s", model, exc)
        raise LLMGenerationError(f"魔搭模型 {model} 流式传输中断：{exc}") from exc
    if not got_content:
        raise LLMGenerationError(f"魔搭模型 {model} 流式返回空内容")
