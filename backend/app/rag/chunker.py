"""文档切片（B3，需求文档 4.3.1 标题感知 + 重叠窗口增强版）。

在需求文档 4.3.1 基线（按段落累积到 chunk_size、带 overlap）之上补齐三点工程能力：
1. **标题感知**：识别 Markdown 标题（`#`~`######`）维护层级栈，切片不跨标题边界
   （遇到新标题先 flush 当前切片），并把当前标题路径作为 `source_location` 的章节部分；
2. **来源定位**：每个 chunk 记录 `source_document`（文档标题）与 `source_location`
   （`[base/]章节路径 / 段落 N`，base 可传页码如「第 6 页」），供检索测试展示溯源；
3. **边界鲁棒**：空文档返回 []；超长段落（单段 > chunk_size）按句子/硬切分裂为多片；
   无标题文档退化为「正文 / 段落 N」。

Token 估算沿用 4.3.1 口径：中文约 1.5 字符/token、其余约 4 字符/token。
"""
from __future__ import annotations

import re

# Markdown 标题：1-6 个 # + 空格 + 标题文本
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
# 段落分隔：一个及以上空行
_PARA_SPLIT_RE = re.compile(r"\n\s*\n")
# 句子切分（中英标点），用于超长段落硬切前的优先切点
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？；!?;\.])")
_CJK_RE = re.compile(r"[一-鿿]")


def count_tokens(text: str) -> int:
    """估算 token 数（中文 1.5 字符/token，其余 4 字符/token；至少 1 计 0）。"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return int(cjk / 1.5 + other / 4)


class DocumentChunker:
    """标题感知 + 重叠窗口切片器。"""

    def __init__(self, chunk_size: int = 512, overlap: int = 64) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap

    # ---- 对外主入口 -------------------------------------------------------
    def chunk_document(
        self,
        text: str,
        *,
        document_id: str,
        document_title: str,
        base_location: str = "",
    ) -> list[dict]:
        """切片单篇文档。

        Args:
            text: 文档纯文本（Markdown / 抽取后的 PDF 文本）。
            document_id / document_title: 回填到每个 chunk 的来源标识。
            base_location: 来源定位前缀（如 PDF 页码「第 6 页」）；为空则省略。

        Returns:
            chunk dict 列表，每项含 content / chunk_index / token_count /
            source_document / source_location / metadata（供向量库写入）。
        """
        if not text or not text.strip():
            return []

        chunks: list[dict] = []
        heading_stack: list[tuple[int, str]] = []  # (level, title)
        para_no = 0  # 当前章节内段落序号
        buf = ""  # 当前累积切片文本
        buf_tokens = 0
        buf_loc = ""  # 当前切片首段所在来源定位

        def flush() -> None:
            nonlocal buf, buf_tokens, buf_loc
            content = buf.strip()
            if content:
                chunks.append(
                    self._make_chunk(
                        content, len(chunks), document_id, document_title, buf_loc
                    )
                )
            buf, buf_tokens, buf_loc = "", 0, ""

        for raw_line_block in self._iter_blocks(text):
            heading = _HEADING_RE.match(raw_line_block.strip())
            if heading:
                # 标题边界：先收口当前切片，再更新标题栈、重置段落计数
                flush()
                level = len(heading.group(1))
                title = heading.group(2).strip()
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, title))
                para_no = 0
                continue

            # 普通段落（可能超长 → 先裂成 ≤chunk_size 的片段）
            for piece in self._split_oversized(raw_line_block.strip()):
                para_no += 1
                loc = self._build_location(base_location, heading_stack, para_no)
                piece_tokens = count_tokens(piece)
                if buf and buf_tokens + piece_tokens > self.chunk_size:
                    flush()
                    # 新切片带上一片末尾的重叠内容
                    overlap = self._tail_overlap(chunks)
                    buf = (overlap + "\n" + piece) if overlap else piece
                    buf_tokens = count_tokens(buf)
                    buf_loc = loc
                else:
                    if not buf:
                        buf_loc = loc
                    buf = (buf + "\n" + piece) if buf else piece
                    buf_tokens += piece_tokens

        flush()
        return chunks

    # ---- 内部工具 ---------------------------------------------------------
    @staticmethod
    def _iter_blocks(text: str):
        """把文本切成「标题行」或「段落块」序列，保持顺序。"""
        for para in _PARA_SPLIT_RE.split(text):
            para = para.strip("\n")
            if not para.strip():
                continue
            # 段落内可能首行是标题、其后跟正文：拆出连续标题行
            lines = para.split("\n")
            chunk_lines: list[str] = []
            for line in lines:
                if _HEADING_RE.match(line.strip()):
                    if chunk_lines:
                        yield "\n".join(chunk_lines)
                        chunk_lines = []
                    yield line
                else:
                    chunk_lines.append(line)
            if chunk_lines:
                yield "\n".join(chunk_lines)

    def _split_oversized(self, para: str) -> list[str]:
        """超长段落按句子聚合切分为 ≤chunk_size 的片段；仍超长则硬切。"""
        if count_tokens(para) <= self.chunk_size:
            return [para]
        pieces: list[str] = []
        cur, cur_tok = "", 0
        for sent in (s for s in _SENT_SPLIT_RE.split(para) if s):
            st = count_tokens(sent)
            if st > self.chunk_size:
                # 单句仍超长：按字符硬切
                if cur:
                    pieces.append(cur)
                    cur, cur_tok = "", 0
                pieces.extend(self._hard_split(sent))
                continue
            if cur and cur_tok + st > self.chunk_size:
                pieces.append(cur)
                cur, cur_tok = sent, st
            else:
                cur += sent
                cur_tok += st
        if cur:
            pieces.append(cur)
        return pieces or [para]

    def _hard_split(self, text: str) -> list[str]:
        """按字符上限硬切（中文 1 字符≈0.67 token，用 chunk_size 估算字符窗）。"""
        # chunk_size token 对应的大致字符数（保守取中文密度，避免越界）
        char_window = max(1, int(self.chunk_size * 1.5))
        return [text[i : i + char_window] for i in range(0, len(text), char_window)]

    def _tail_overlap(self, chunks: list[dict]) -> str:
        """取上一切片末尾约 overlap 个 token 的文本作为重叠窗口。"""
        if not self.overlap or not chunks:
            return ""
        prev = chunks[-1]["content"]
        # 从末尾按字符回取，直到累计 token 达到 overlap
        acc, tokens = "", 0
        for ch in reversed(prev):
            acc = ch + acc
            tokens = count_tokens(acc)
            if tokens >= self.overlap:
                break
        return acc

    @staticmethod
    def _build_location(
        base: str, heading_stack: list[tuple[int, str]], para_no: int
    ) -> str:
        """拼装 source_location：`[base / ]章节路径 / 段落 N`。"""
        parts: list[str] = []
        if base:
            parts.append(base)
        if heading_stack:
            parts.extend(title for _, title in heading_stack)
        else:
            parts.append("正文")
        parts.append(f"段落 {para_no}")
        return " / ".join(parts)

    @staticmethod
    def _make_chunk(
        content: str,
        index: int,
        document_id: str,
        document_title: str,
        location: str,
    ) -> dict:
        return {
            "content": content,
            "chunk_index": index,
            "token_count": count_tokens(content),
            "source_document": document_title,
            "source_location": location or "正文",
            "metadata": {
                "document_id": document_id,
                "document_title": document_title,
                "source_location": location or "正文",
                "chunk_index": index,
                "token_count": count_tokens(content),
            },
        }
