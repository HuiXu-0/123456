"""逐句接地校验（B5-b，接口文档 15.3 幻觉率统一口径）。

口径：生成内容按句切分，每句与本次 RAG 检索命中的来源切片做 embedding 相似度
比对取最大值；低于阈值（settings.grounding_threshold，默认 0.75）的句子视为
「未接地（疑似幻觉）」，hallucinationRate = 未接地句数 / 总句数（0-1）。
无任何 RAG 来源（纯模板/兜底）时按约定置 0，不参与质量统计。

工程细节：
- Markdown 预处理：代码块与数学公式（$$ 块 / \\[ 块 / LaTeX 命令行 / 行内 $…$）
  整体剔除——均非自然语言句，文本 embedding 无法有意义比对，正确性由 critic 与
  人工评审负责；标题行整行剔除（章节标签非事实断言）；引用/列表/加粗等语法符号
  剥离后保留正文；
- 表格处理（B8）：分隔行（|---|---|）剔除，数据行剥离管道符仅留单元格文本；
- 结构行过滤（B8）：以冒号结尾的短引导行（如「数学表达为：」）与不含任何
  句内标点的超短行（小节标题残文）属文档结构而非事实断言，不计入句子；
- 比对单元 = 来源切片全文 + 切片内逐句（细粒度子片段提升长切片的召回保真，
  仍属「与来源切片比对」口径）；
- 向量经 Embedder（bge 或降级哈希嵌入）均已 L2 归一化 → 点积即余弦相似度。
"""
from __future__ import annotations

import re
from typing import Any

from app.core.config import settings
from app.rag.embeddings import get_embedder

# 代码块（``` 围栏）整体剔除
_CODE_FENCE_RE = re.compile(r"```.*?```", re.S)
# 展示型数学公式块（$$…$$ / \[…\]）整体剔除：公式非自然语言句，文本 embedding
# 无法有意义地比对（公式正确性由 critic / 评审负责），与代码块同口径（B8）
_MATH_BLOCK_RE = re.compile(r"\$\$.*?\$\$|\\\[.*?\\\]", re.S)
# 行内数学（$…$）剔除片段、保留前后正文
_INLINE_MATH_RE = re.compile(r"\$[^$\n]+\$")
# LaTeX 命令（\frac、\leftarrow 等）：命中即视为公式行，整行剔除
_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]{2,}")
# Markdown 标题行（#{1,6} 开头）：章节标签属文档结构，整行剔除（B8）
_HEADING_LINE_RE = re.compile(r"^\s*#{1,6}\s")
# 行内 Markdown 语法符号：引用/列表前缀、小节编号（3.1 / 3.1.2 等）
_MD_PREFIX_RE = re.compile(r"^\s*(?:>\s*|[-*+]\s+|\d+(?:\.\d+)*[.、]?\s+)")
_MD_INLINE_RE = re.compile(r"[*_`]+")
# 句子切分：中英句末标点 / 换行
_SENT_SPLIT_RE = re.compile(r"[。！？；!?;\n]+")
# 子句切分（来源侧比对单元）：逗号/顿号/冒号
_CLAUSE_SPLIT_RE = re.compile(r"[，、：,:]+")
# 过短片段（标题残文、连接词等）不计为句子
_MIN_SENT_CHARS = 6
# Markdown 表格分隔行（| --- | :---: | …），整行剔除
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")
# 句内标点（含顿号/逗号等）：超短行若完全不含则视为小节标题残文
_INNER_PUNCT_RE = re.compile(r"[，、,：:（）()]")
_BARE_TITLE_MAX_CHARS = 12
# 内容字符（汉字/字母/数字）：剥离语法后内容不足者为结构残壳（如「其中 ，，且」）
_CONTENT_CHAR_RE = re.compile(r"[\w一-鿿]")
_MIN_CONTENT_CHARS = 6


def _normalize_lines(text: str) -> list[str]:
    """逐行预处理：剔结构行（标题/表头/引导行/公式行），表格数据行留单元格文本。"""
    lines: list[str] = []
    last_was_table_row = False
    for raw in text.split("\n"):
        if _HEADING_LINE_RE.match(raw):
            last_was_table_row = False
            continue  # 标题行：章节标签，整行剔除
        line = _MD_PREFIX_RE.sub("", raw)
        if len(_INLINE_MATH_RE.findall(line)) >= 2:
            last_was_table_row = False
            continue  # 公式注释行（「其中 $x$ 是…，$y$ 是…」）：随公式同口径剔除
        line = _INLINE_MATH_RE.sub("", line)
        line = _MD_INLINE_RE.sub("", line)
        stripped = line.strip()
        if not stripped:
            last_was_table_row = False
            continue
        if _TABLE_SEP_RE.match(stripped):
            # 表格分隔行：纯语法；其上一行（若为表格行）即表头，回收剔除
            if last_was_table_row and lines:
                lines.pop()
            last_was_table_row = False
            continue
        if _LATEX_CMD_RE.search(stripped):
            last_was_table_row = False
            continue  # LaTeX 公式行：非自然语言句
        is_table_row = "|" in stripped
        if is_table_row:
            # 表格数据行：剥离管道符，单元格文本以逗号连接成可比对句
            cells = [c.strip() for c in stripped.strip("|").split("|") if c.strip()]
            stripped = "，".join(cells)
        if stripped.endswith(("：", ":")):
            last_was_table_row = False
            continue  # 冒号结尾的引导行：事实在后续列表/代码中，本行为结构行
        if (
            len(stripped) <= _BARE_TITLE_MAX_CHARS
            and not _INNER_PUNCT_RE.search(stripped)
        ):
            last_was_table_row = False
            continue  # 无任何句内标点的超短行：小节标题残文
        if len(_CONTENT_CHAR_RE.findall(stripped)) < _MIN_CONTENT_CHARS:
            last_was_table_row = False
            continue  # 内容字符不足：行内公式剥离后的标点残壳等
        lines.append(stripped)
        last_was_table_row = is_table_row
    return lines


def split_sentences(text: str) -> list[str]:
    """Markdown → 自然语言句子列表（剔代码块/表格语法/结构行、滤过短片段）。"""
    if not text:
        return []
    cleaned = _CODE_FENCE_RE.sub("", text)
    cleaned = _MATH_BLOCK_RE.sub("", cleaned)
    cleaned = _MD_INLINE_RE.sub("", "\n".join(_normalize_lines(cleaned)))
    sentences = []
    for raw in _SENT_SPLIT_RE.split(cleaned):
        s = raw.strip()
        if len(s) >= _MIN_SENT_CHARS:
            sentences.append(s)
    return sentences


def sentence_grounding(
    text: str,
    contexts: list[str],
    threshold: float | None = None,
) -> dict[str, Any]:
    """逐句接地校验（15.3）。

    Args:
        text: 生成内容（Markdown）。
        contexts: 本次 RAG 检索命中的来源切片内容列表。
        threshold: 接地阈值，缺省取 settings.grounding_threshold。

    Returns:
        {hallucinationRate, totalSentences, ungroundedSentences}
    """
    if threshold is None:
        threshold = settings.grounding_threshold
    sentences = split_sentences(text)
    contexts = [c for c in contexts if c and c.strip()]
    if not sentences or not contexts:
        # 15.3：无来源（纯模板/兜底）或无可判句子 → 置 0，不参与质量统计
        return {
            "hallucinationRate": 0.0,
            "totalSentences": len(sentences),
            "ungroundedSentences": [],
        }

    # 比对单元：切片全文 + 切片内逐句 + 相邻句对 + 句内子句（去重）。
    # 多粒度单元（B8）：生成句既可能融合来源相邻两句（组合改写 → 句对单元），
    # 也可能只取来源长句中的一个分句（短句 vs 长句相似度被稀释 → 子句单元），
    # 仍属「与来源切片比对」口径，仅改变比对粒度。
    units: list[str] = []
    seen: set[str] = set()
    for ctx in contexts:
        ctx_sentences = split_sentences(ctx)
        pairs = [
            f"{a}，{b}" for a, b in zip(ctx_sentences, ctx_sentences[1:])
        ]
        clauses = [
            c.strip()
            for s in ctx_sentences
            for c in _CLAUSE_SPLIT_RE.split(s)
            if len(c.strip()) >= _MIN_SENT_CHARS
        ]
        for unit in [ctx, *ctx_sentences, *pairs, *clauses]:
            u = unit.strip()
            if u and u not in seen:
                seen.add(u)
                units.append(u)

    embedder = get_embedder()
    sent_vecs = embedder.embed_texts(sentences)
    unit_vecs = embedder.embed_texts(units)

    ungrounded: list[str] = []
    for sentence, sv in zip(sentences, sent_vecs):
        best = max(sum(a * b for a, b in zip(sv, uv)) for uv in unit_vecs)
        if best < threshold:
            ungrounded.append(sentence)

    return {
        "hallucinationRate": round(len(ungrounded) / len(sentences), 4),
        "totalSentences": len(sentences),
        "ungroundedSentences": ungrounded,
    }
