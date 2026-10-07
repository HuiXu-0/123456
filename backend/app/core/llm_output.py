"""LLM 自由文本的解析与展示格式清洗。

本模块只处理模型输出边界，不负责调用 Provider、生成 Prompt 或业务兜底。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.core.llm_deepseek import LLMGenerationError

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.S)

# 前端 Mermaid v11 支持的图型白名单。
MERMAID_HEADS: tuple[str, ...] = (
    "flowchart",
    "graph",
    "mindmap",
    "classdiagram",
    "sequencediagram",
    "statediagram",
    "erdiagram",
    "journey",
    "timeline",
    "quadrantchart",
    "gitgraph",
    "requirementdiagram",
)


def extract_json(text: str) -> Any | None:
    """从模型自由文本中提取 JSON 对象；解析失败返回 ``None``。"""
    for candidate in (text, *_JSON_BLOCK_RE.findall(text or "")):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def strip_markdown_fence(text: str) -> str:
    """剥离模型偶发的 Markdown 代码围栏，保留正文。"""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if len(lines) >= 2 and lines[-1].strip() == "```":
            cleaned = "\n".join(lines[1:-1]).strip()
    return cleaned


def clean_mermaid(raw: str) -> str:
    """剥离围栏并校验 Mermaid 首行图型，非法输出抛统一生成异常。"""
    text = strip_markdown_fence(raw or "").strip()
    if not text:
        raise LLMGenerationError("知识图解输出为空")
    head = text.splitlines()[0].strip().lower()
    if not any(head.startswith(supported) for supported in MERMAID_HEADS):
        raise LLMGenerationError("知识图解输出非合法 Mermaid 图（首行图型不受支持）")
    return text
