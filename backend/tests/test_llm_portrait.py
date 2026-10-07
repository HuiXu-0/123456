from app.core import llm as llm_module
from app.core.llm_portrait import (
    ABILITY_DIM_KEYS,
    PORTRAIT_DIMENSIONS,
    PORTRAIT_DIM_KINDS,
    PORTRAIT_KEYS,
    PORTRAIT_LABELS,
    PREFERENCE_DIM_KEYS,
    SUBJECTIVE_DIM_KEYS,
    extract_mock_portrait,
    sanitize_portrait_updates,
)


def test_llm_module_keeps_portrait_compatibility_exports():
    assert llm_module.PORTRAIT_DIMENSIONS is PORTRAIT_DIMENSIONS
    assert llm_module.PORTRAIT_DIM_KINDS is PORTRAIT_DIM_KINDS
    assert llm_module.ABILITY_DIM_KEYS is ABILITY_DIM_KEYS
    assert llm_module.PREFERENCE_DIM_KEYS is PREFERENCE_DIM_KEYS
    assert llm_module.SUBJECTIVE_DIM_KEYS is SUBJECTIVE_DIM_KEYS
    assert llm_module._PORTRAIT_KEYS is PORTRAIT_KEYS
    assert llm_module._PORTRAIT_LABELS is PORTRAIT_LABELS
    assert llm_module._sanitize_portrait_updates is sanitize_portrait_updates
    assert llm_module._mock_extract_portrait is extract_mock_portrait


def test_portrait_dimension_contract_is_fixed_and_partitioned():
    assert [key for key, _ in PORTRAIT_DIMENSIONS] == list(PORTRAIT_KEYS)
    assert set(ABILITY_DIM_KEYS) | set(PREFERENCE_DIM_KEYS) | set(SUBJECTIVE_DIM_KEYS) == set(
        PORTRAIT_KEYS
    )
    assert not set(ABILITY_DIM_KEYS) & set(PREFERENCE_DIM_KEYS)
    assert not set(ABILITY_DIM_KEYS) & set(SUBJECTIVE_DIM_KEYS)
    assert not set(PREFERENCE_DIM_KEYS) & set(SUBJECTIVE_DIM_KEYS)


def test_sanitize_portrait_updates_enforces_contract_and_last_value_wins():
    updates = sanitize_portrait_updates(
        [
            {"key": "invented", "value": "禁止编造", "confidence": 1, "source": "dialogue"},
            {
                "key": "learning_goal",
                "label": "错误标签",
                "value": "旧目标",
                "score": 90,
                "confidence": 2,
                "source": "invalid",
            },
            {
                "key": "knowledge_base",
                "value": "扎实",
                "score": 120.8,
                "confidence": -1,
                "source": "diagnostic",
                "basis": " 3/3 道题正确 ",
            },
            {
                "key": "learning_goal",
                "value": "转大模型应用方向",
                "score": 99,
                "confidence": 0.95,
                "source": "inferred",
            },
        ]
    )

    assert updates == [
        {
            "key": "learning_goal",
            "label": "学习目标",
            "kind": "subjective",
            "value": "转大模型应用方向",
            "confidence": 0.6,
            "source": "inferred",
        },
        {
            "key": "knowledge_base",
            "label": "知识基础",
            "kind": "ability",
            "value": "扎实",
            "confidence": 0.0,
            "source": "diagnostic",
            "score": 100,
            "basis": "3/3 道题正确",
        },
    ]


def test_mock_extraction_is_deterministic_and_uses_manual_context_only_on_first_turn():
    message = "我做过 Python 爬虫项目，想转大模型应用方向，时间紧，代码容易报错"
    first = extract_mock_portrait(message, {"goal": "技能认证", "major": "软件工程"}, True)
    repeated = extract_mock_portrait(message, {"goal": "技能认证", "major": "软件工程"}, True)
    assert first == repeated

    by_key = {item["key"]: item for item in first}
    assert by_key["prior_experience"]["value"] == "有Python工程实践(爬虫)"
    assert by_key["learning_goal"]["value"] == "转大模型应用方向"
    assert by_key["cognitive_style"]["kind"] == "preference"
    assert by_key["learning_pace"]["value"] == "偏快(集中突破)"
    assert by_key["error_preference"]["confidence"] <= 0.6
    assert "knowledge_base" in by_key  # 首轮表单专业背景补齐未命中的能力维

    later = extract_mock_portrait("没有可抽取信号", {"goal": "技能认证", "major": "软件工程"}, False)
    assert later == []


def test_mock_extraction_does_not_invent_dimensions_without_signal():
    assert extract_mock_portrait("今天心情不错", None, True) == []
