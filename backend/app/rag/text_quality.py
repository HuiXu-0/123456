"""RAG文本编码与乱码质量门禁（TASK-003-C2）。"""
from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path


class TextEncodingError(UnicodeError):
    """源文件不是合法UTF-8。"""


class TextQualityError(ValueError):
    """文本包含乱码、异常控制字符、空文本或重复内容。"""


_MOJIBAKE_MARKERS = (
    "锟斤拷",
    "ï¿½",
    "Ã",
    "Â",
    "ÊÇ",
    "µÄ",
    "£¬",
    "¡£",
    "Ð",
    "Ñ",
)


@dataclass(frozen=True)
class TextQualityReport:
    valid: bool
    issues: list[str] = field(default_factory=list)
    replacement_count: int = 0
    control_count: int = 0
    non_printable_ratio: float = 0.0
    mojibake_hits: list[str] = field(default_factory=list)


def read_utf8_strict(path: str | Path) -> str:
    target = Path(path)
    raw = target.read_bytes()
    try:
        text = raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise TextEncodingError(
            f"非UTF-8文件：{target}，byte={exc.start}，reason={exc.reason}"
        ) from exc
    return text


def inspect_text_quality(text: str) -> TextQualityReport:
    issues: list[str] = []
    if not text or not text.strip():
        issues.append("empty_text")
    replacement_count = text.count("\ufffd")
    if replacement_count:
        issues.append("replacement_character")
    controls = [
        char
        for char in text
        if unicodedata.category(char) == "Cc" and char not in "\n\r\t"
    ]
    if controls:
        issues.append("unexpected_control_character")
    non_printable = sum(
        1 for char in text if not char.isprintable() and char not in "\n\r\t"
    )
    ratio = non_printable / max(1, len(text))
    if ratio > 0.01:
        issues.append("high_non_printable_ratio")
    hits = [marker for marker in _MOJIBAKE_MARKERS if marker in text]
    if hits:
        issues.append("mojibake_signature")
    return TextQualityReport(
        valid=not issues,
        issues=issues,
        replacement_count=replacement_count,
        control_count=len(controls),
        non_printable_ratio=round(ratio, 6),
        mojibake_hits=hits,
    )


def validate_text_quality(text: str, *, context: str) -> None:
    report = inspect_text_quality(text)
    if not report.valid:
        raise TextQualityError(f"{context}文本质量不合格：{','.join(report.issues)}")


def find_duplicate_chunks(chunks: list[dict]) -> list[list[str]]:
    by_hash: dict[str, list[str]] = {}
    for chunk in chunks:
        content = str(chunk.get("content") or "").strip()
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        by_hash.setdefault(digest, []).append(str(chunk.get("id") or ""))
    return [ids for ids in by_hash.values() if len(ids) > 1]
