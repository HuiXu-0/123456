import pytest

from app.core.llm_deepseek import LLMGenerationError
from app.core.llm_output import clean_mermaid, extract_json, strip_markdown_fence


def test_extract_json_accepts_plain_and_embedded_objects():
    assert extract_json('{"score": 90}') == {"score": 90}
    assert extract_json('模型说明：\n```json\n{"score": 90}\n```') == {"score": 90}


def test_extract_json_returns_none_for_invalid_output():
    assert extract_json("not-json") is None
    assert extract_json("") is None


def test_strip_markdown_fence_preserves_unfenced_content():
    assert strip_markdown_fence("```markdown\n# 标题\n```") == "# 标题"
    assert strip_markdown_fence("  正文  ") == "正文"


@pytest.mark.parametrize("head", ["flowchart TD", "graph LR", "mindmap", "sequenceDiagram"])
def test_clean_mermaid_accepts_supported_diagrams(head):
    assert clean_mermaid(f"```mermaid\n{head}\n  A --> B\n```").startswith(head)


@pytest.mark.parametrize("raw", ["", "```mermaid\n```", "pie\n  title 不支持"])
def test_clean_mermaid_rejects_empty_or_unsupported_output(raw):
    with pytest.raises(LLMGenerationError):
        clean_mermaid(raw)
