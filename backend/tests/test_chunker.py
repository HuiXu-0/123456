"""切片器边界测试（B3 验收要求 3：空文档 / 超长段落 / 无标题文档）。

运行：cd backend && pytest -q tests/test_chunker.py
"""
from __future__ import annotations

from app.rag.chunker import DocumentChunker, count_tokens

DOC_KW = {"document_id": "doc_test", "document_title": "测试文档"}


def _chunk(text: str, **kw):
    return DocumentChunker(chunk_size=64, overlap=16).chunk_document(text, **{**DOC_KW, **kw})


# ---- 边界 1：空文档 -------------------------------------------------------
def test_empty_document_returns_empty():
    assert _chunk("") == []
    assert _chunk("   \n\n  \t ") == []


# ---- 边界 2：超长段落（单段 > chunk_size 必须被裂成多片，且每片不超限太多） ----
def test_oversized_paragraph_is_split():
    # 单个超长中文段落，无空行（不会被段落分隔切开）
    long_para = "反向传播算法通过链式法则逐层计算梯度。" * 60
    chunks = _chunk(long_para)
    assert len(chunks) > 1, "超长段落应被裂分为多个切片"
    # 每个切片 token 数不应远超 chunk_size（允许重叠带来的小幅超出）
    for c in chunks:
        assert c["token_count"] <= 64 * 2
    # 内容完整性：所有切片应覆盖原文关键词
    assert any("反向传播" in c["content"] for c in chunks)


def test_single_giant_sentence_hard_split():
    # 没有任何句子标点的超长串：必须走硬切而非死循环
    giant = "深度学习" * 500  # 无标点
    chunks = _chunk(giant)
    assert len(chunks) > 1
    assert all(c["content"] for c in chunks)


# ---- 边界 3：无标题文档（退化为「正文 / 段落 N」来源定位） ----------------
def test_no_heading_document_uses_body_location():
    text = "第一段内容介绍机器学习。\n\n第二段内容介绍神经网络。"
    chunks = _chunk(text)
    assert len(chunks) >= 1
    for c in chunks:
        assert c["source_document"] == "测试文档"
        assert "正文" in c["source_location"]
        assert "段落" in c["source_location"]


# ---- 标题感知：source_location 含章节路径，且切片不跨标题 ------------------
def test_heading_aware_location_and_boundary():
    text = (
        "# 大标题\n\n"
        "## 第一章 神经网络\n\n前向传播很重要。\n\n"
        "## 第二章 反向传播\n\n梯度通过链式法则计算。"
    )
    chunks = _chunk(text)
    # 至少两个章节各自成片
    locs = [c["source_location"] for c in chunks]
    assert any("第一章 神经网络" in loc for loc in locs)
    assert any("第二章 反向传播" in loc for loc in locs)
    # 不同章节的内容不应混入同一切片
    for c in chunks:
        if "前向传播" in c["content"]:
            assert "梯度通过链式法则" not in c["content"]


# ---- base_location（PDF 页码）前缀注入 ------------------------------------
def test_base_location_prefix():
    chunks = _chunk("正文内容一二三。", base_location="第 6 页")
    assert chunks
    assert chunks[0]["source_location"].startswith("第 6 页")


# ---- chunk_index 连续、metadata 完整 --------------------------------------
def test_chunk_index_and_metadata():
    text = "段落甲。\n\n段落乙。\n\n段落丙。"
    chunks = _chunk(text)
    for i, c in enumerate(chunks):
        assert c["chunk_index"] == i
        assert c["metadata"]["document_id"] == "doc_test"
        assert c["metadata"]["source_location"] == c["source_location"]


# ---- token 估算口径（中文 1.5 字符/token） --------------------------------
def test_count_tokens_chinese_density():
    assert count_tokens("") == 0
    # 6 个中文字 ≈ 4 token
    assert count_tokens("神经网络反向") == int(6 / 1.5)
