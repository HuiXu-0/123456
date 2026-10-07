"""生成模型统一分发通道（模型管理：按注册表「当前模型」路由真实调用）。

llm.py（及 content_safety 模型级校验）的所有真实生成路径统一经本模块
``chat`` / ``chat_stream``，按 ``model_registry.current()`` 分发：

- **deepseek**（默认）→ 既有 llm_deepseek 通道，与切换功能上线前行为逐字一致；
- **modelscope** → llm_modelscope；调用失败/超时/未配 Key → 记 warning 后
  **优雅回落默认 DeepSeek**；DeepSeek 亦不可用 → 抛 ``LLMGenerationError``，
  由 llm.py 各方法既有 mock 兜底 / 路由 2001 承接（绝不崩）；
- **mock** → 正常不会走到本模块（LLMClient.is_mock 分支在前拦截）；若被强制
  以真实 provider 触达（如测试直构 LLMClient("deepseek")），与旧行为逐字一致地
  走 llm_deepseek（缺 Key 时由其抛 LLMGenerationError，兜底链不变）。

签名与 llm_deepseek.chat / chat_stream 完全一致——llm.py 调用点只换模块名，零语义变化。
"""
from __future__ import annotations

import logging

from app.core import generation_provenance, llm_deepseek, llm_modelscope, llm_userconf, model_registry
from app.core.config import settings
from app.core.llm_deepseek import LLMGenerationError

logger = logging.getLogger("app.core.llm_transport")


def _optional_kwargs(system: str | None, history: list[dict[str, str]] | None) -> dict:
    """system/history 仅在显式传入时透传——与旧调用形状逐字一致
    （多数调用点只传 system；既有测试的 fake 也按该签名打桩）。"""
    kwargs: dict = {}
    if system is not None:
        kwargs["system"] = system
    if history is not None:
        kwargs["history"] = history
    return kwargs


def chat(
    prompt: str,
    system: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> str:
    """按当前模型分发的 chat 补全。魔搭/自建配置失败自动回落默认 DeepSeek（优雅降级）。"""
    spec = model_registry.current()
    kwargs = _optional_kwargs(system, history)
    if spec.source == "custom":
        try:
            result = llm_userconf.chat(prompt, spec=spec, **kwargs)
            generation_provenance.mark_execution(
                provider=spec.provider, model=spec.model_id, source="custom"
            )
            return result
        except LLMGenerationError as exc:
            # exc 信息已在 llm_userconf 做 key 脱敏清洗
            logger.warning("自建模型 %s 调用失败，回落默认 DeepSeek：%s", spec.id, exc)
    elif spec.provider == "modelscope":
        try:
            result = llm_modelscope.chat(prompt, model=spec.model_id, **kwargs)
            generation_provenance.mark_execution(
                provider=spec.provider, model=spec.model_id, source="builtin"
            )
            return result
        except LLMGenerationError as exc:
            logger.warning("魔搭模型 %s 调用失败，回落默认 DeepSeek：%s", spec.id, exc)
    if spec.source == "custom" or spec.provider == "modelscope":
        generation_provenance.mark_provider_fallback()
    else:
        generation_provenance.mark_execution(
            provider="deepseek", model=settings.deepseek_model, source="builtin"
        )
    return llm_deepseek.chat(prompt, **kwargs)


def chat_stream(
    prompt: str,
    system: str | None = None,
    history: list[dict[str, str]] | None = None,
):
    """按当前模型分发的流式补全。

    魔搭/自建配置**开流失败 / 未产出任何内容前失败** → 回落默认 DeepSeek 流；
    已产出部分内容后中断 → 原样抛 LLMGenerationError（不能重复回放，
    由既有 SSE error 事件兜底），绝不静默截断。
    """
    spec = model_registry.current()
    kwargs = _optional_kwargs(system, history)
    if spec.source == "custom":
        got_content = False
        try:
            for delta in llm_userconf.chat_stream(prompt, spec=spec, **kwargs):
                generation_provenance.mark_execution(
                    provider=spec.provider, model=spec.model_id, source="custom"
                )
                got_content = True
                yield delta
            return
        except LLMGenerationError as exc:
            if got_content:
                raise
            logger.warning("自建模型 %s 流式失败（未产出内容），回落默认 DeepSeek：%s", spec.id, exc)
    elif spec.provider == "modelscope":
        got_content = False
        try:
            for delta in llm_modelscope.chat_stream(prompt, model=spec.model_id, **kwargs):
                generation_provenance.mark_execution(
                    provider=spec.provider, model=spec.model_id, source="builtin"
                )
                got_content = True
                yield delta
            return
        except LLMGenerationError as exc:
            if got_content:
                raise
            logger.warning("魔搭模型 %s 流式失败（未产出内容），回落默认 DeepSeek：%s", spec.id, exc)
    if spec.source == "custom" or spec.provider == "modelscope":
        generation_provenance.mark_provider_fallback()
    else:
        generation_provenance.mark_execution(
            provider="deepseek", model=settings.deepseek_model, source="builtin"
        )
    yield from llm_deepseek.chat_stream(prompt, **kwargs)
