"""用户自建模型配置的真实调用通道（模型管理 21.3+，OpenAI 兼容 SDK）。

自建配置（魔搭 / deepseek / 其他 openai 兼容端点）一律走本通道：base_url 与
解密后的 api_key 来自 ModelSpec（source="custom"），逐配置建客户端并按
(base_url, key指纹, 超时) 缓存复用。

安全红线：上游 SDK 异常串可能回显请求头等内容——统一经 crypto.redact 把完整
key 清洗为脱敏形后再抛 / 打日志（key 不出现在任何日志与错误信息）。
失败一律收敛 ``LLMGenerationError``，由 llm_transport 捕获后**回落默认 DeepSeek**
（再不行走 llm.py 各方法 mock 兜底 / 路由 2001），绝不崩。
"""
from __future__ import annotations

import hashlib
import logging
import time

from app.core import crypto
from app.core.config import settings
from app.core.llm_deepseek import LLMGenerationError  # 统一异常类（→ code 2001）
from app.core.model_registry import ModelSpec

logger = logging.getLogger("app.core.llm_userconf")

# (base_url, key指纹, timeout) -> openai.OpenAI；小容量缓存，超限整体重建（配置数极小）
_clients: dict[tuple[str, str, float], object] = {}
_MAX_CLIENTS = 16


def reset_clients() -> None:
    """清空客户端缓存（配置变更/测试隔离用）。"""
    _clients.clear()


def _client_for(base_url: str, api_key: str, timeout: float):
    """按配置取/建 OpenAI 兼容客户端（缓存键用 key 指纹，不留明文）。"""
    if not api_key:
        raise LLMGenerationError("该模型配置未提供 API Key，请在模型管理页补填")
    fp = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    cache_key = (base_url, fp, timeout)
    client = _clients.get(cache_key)
    if client is None:
        import httpx
        from openai import OpenAI

        if len(_clients) >= _MAX_CLIENTS:
            _clients.clear()
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=1,
            # 与 llm_deepseek 同款连接级重试：连接阶段自动换地址重试，保证可达性
            http_client=httpx.Client(
                transport=httpx.HTTPTransport(retries=2),
                timeout=timeout,
            ),
        )
        _clients[cache_key] = client
    return client


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


def _safe_error(prefix: str, exc: Exception, api_key: str) -> LLMGenerationError:
    """收敛异常并清洗 key（脱敏红线），日志留痕后返回统一异常。"""
    detail = crypto.redact(str(exc), api_key)
    logger.warning("%s：%s", prefix, detail)
    return LLMGenerationError(f"{prefix}：{detail}")


def chat(
    prompt: str,
    system: str | None = None,
    history: list[dict[str, str]] | None = None,
    *,
    spec: ModelSpec,
) -> str:
    """chat 补全（spec = 用户自建配置解析出的 ModelSpec）。异常 → LLMGenerationError。"""
    client = _client_for(spec.base_url, spec.api_key, settings.llm_timeout_seconds)
    try:
        resp = client.chat.completions.create(
            model=spec.model_id,
            messages=_build_messages(prompt, system, history),
            temperature=settings.llm_temperature,
            extra_body=_extra_body(spec.model_id, stream=False),
        )
        content = (resp.choices[0].message.content or "").strip()
    except LLMGenerationError:
        raise
    except Exception as exc:  # noqa: BLE001 SDK 异常族繁多，统一收敛
        raise _safe_error(f"自建模型 {spec.label} 调用失败", exc, spec.api_key) from exc
    if not content:
        raise LLMGenerationError(f"自建模型 {spec.label} 返回空内容")
    return content


def chat_stream(
    prompt: str,
    system: str | None = None,
    history: list[dict[str, str]] | None = None,
    *,
    spec: ModelSpec,
):
    """流式 chat 补全：逐 chunk 产出增量文本。异常（含流中断）→ LLMGenerationError。"""
    client = _client_for(spec.base_url, spec.api_key, settings.llm_timeout_seconds)
    try:
        stream = client.chat.completions.create(
            model=spec.model_id,
            messages=_build_messages(prompt, system, history),
            temperature=settings.llm_temperature,
            stream=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise _safe_error(f"自建模型 {spec.label} 流式调用失败", exc, spec.api_key) from exc
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
        raise _safe_error(f"自建模型 {spec.label} 流式传输中断", exc, spec.api_key) from exc
    if not got_content:
        raise LLMGenerationError(f"自建模型 {spec.label} 流式返回空内容")


def probe(
    *, base_url: str, api_key: str, model_id: str, label: str = ""
) -> dict:
    """测试连通性（21.4）：用给定配置发一次轻量补全，返回 {ok, latencyMs, message}。

    永不抛异常（测试动作本身总是成功执行）；失败信息已做 key 脱敏清洗。
    """
    started = time.perf_counter()
    name = label or model_id
    try:
        client = _client_for(base_url, api_key, settings.model_test_timeout_seconds)
        resp = client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": "连通性测试：请只回复 OK"}],
            temperature=0,
            max_tokens=8,
            extra_body=_extra_body(model_id, stream=False),
        )
        content = (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - started) * 1000)
        detail = crypto.redact(str(exc), api_key)
        logger.info("模型连通性测试失败（%s）：%s", name, detail)
        return {"ok": False, "latencyMs": latency, "message": f"连接失败：{detail[:300]}"}
    latency = int((time.perf_counter() - started) * 1000)
    return {
        "ok": True,
        "latencyMs": latency,
        "message": f"连接成功（{latency}ms）" + (f"，模型回复：{content[:60]}" if content else ""),
    }
