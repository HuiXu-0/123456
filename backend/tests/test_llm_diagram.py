import pytest

from app.core import llm as llm_module
from app.core.llm_diagram import DIAGRAM_TEMPLATES, generic_diagram


def test_llm_module_keeps_diagram_compatibility_aliases():
    assert llm_module._DIAGRAM_TEMPLATES is DIAGRAM_TEMPLATES
    assert llm_module._generic_diagram is generic_diagram


def test_fixed_template_catalog_is_complete():
    expected = {"nn", "ml", "dl", "cnn", "transformer", "finetune"}
    expected |= {f"GEN-{index}" for index in range(1, 14)}
    assert set(DIAGRAM_TEMPLATES) == expected
    assert all(value.endswith("\n") for value in DIAGRAM_TEMPLATES.values())


@pytest.mark.parametrize("kp_id", ["nn", "ml", "dl", "cnn"])
def test_core_pipeline_templates_keep_flowchart_contract(kp_id):
    assert DIAGRAM_TEMPLATES[kp_id].startswith("flowchart")


@pytest.mark.parametrize("kp_id", [f"GEN-{index}" for index in range(1, 14)])
def test_gen_templates_keep_renderable_mermaid_heads(kp_id):
    head = DIAGRAM_TEMPLATES[kp_id].splitlines()[0]
    assert head.startswith(("flowchart", "graph", "mindmap"))


def test_generic_taxonomy_diagram_is_deterministic_and_content_driven():
    description = "主要类型包括监督学习、无监督学习、强化学习"
    first = generic_diagram("机器学习", description)
    assert first == generic_diagram("机器学习", description)
    assert first.startswith("graph TD\n")
    assert 'ROOT["机器学习"]' in first
    assert "监督学习" in first


def test_generic_process_diagram_uses_default_step_and_feedback_loop():
    diagram = generic_diagram("新知识点", "")
    assert diagram.startswith("flowchart LR\n")
    assert "新知识点核心" in diagram
    assert "迭代优化" in diagram
