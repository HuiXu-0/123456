"""Prompt 统一解析包（R1：统一 Agent Framework）。

对外暴露 ``PromptProvider`` / ``PromptResolution`` / ``render_template``；
语义与来源策略见 ``provider`` 模块文档。
"""
from __future__ import annotations

from app.agents.prompts.provider import (
    PromptProvider,
    PromptResolution,
    get_prompt_provider,
    render_template,
)

__all__ = [
    "PromptProvider",
    "PromptResolution",
    "get_prompt_provider",
    "render_template",
]
