import json

from app.core import llm as llm_module
from app.core.llm_practice import audit_practice
from app.core.llm_reinforcement import (
    REINFORCE_BANK,
    clean_reinforcement,
    generate_mock_card,
)


def _question(question_id="custom_q1"):
    return {
        "question_id": question_id,
        "question_type": "single",
        "question_text": "哪个选项正确？",
        "options": [
            {"option_id": "a", "option_text": "选项 A"},
            {"option_id": "b", "option_text": "选项 B"},
            {"option_id": "c", "option_text": "选项 C"},
        ],
        "correct_answer": "b",
        "explanation": "B 是正确答案。",
    }


def test_llm_module_keeps_reinforcement_bank_compatibility_alias():
    assert llm_module._REINFORCE_BANK is REINFORCE_BANK


def test_reinforcement_bank_keeps_three_nn_cards():
    assert set(REINFORCE_BANK) == {"nn_q1", "nn_q2", "nn_q3"}
    for question_id, card in REINFORCE_BANK.items():
        assert card["point"]
        assert card["recap"]
        assert card["practice"]["question_id"] == f"{question_id}-r"
        assert audit_practice(card["practice"]) == []


def test_mock_card_uses_bank_without_mutating_it():
    original_text = REINFORCE_BANK["nn_q1"]["practice"]["question_text"]
    card = generate_mock_card(_question("nn_q1"))
    assert card["questionId"] == "nn_q1"
    assert card["point"] == "神经元运算顺序"
    card["practice"]["question_text"] = "已修改"
    assert REINFORCE_BANK["nn_q1"]["practice"]["question_text"] == original_text


def test_mock_card_generic_fallback_rotates_options_and_stays_self_consistent():
    question = _question()
    card = generate_mock_card(question)
    assert card["practice"]["question_id"] == "custom_q1-r"
    assert card["practice"]["question_text"].startswith("【强化·变式】")
    assert [option["option_id"] for option in card["practice"]["options"]] == ["b", "c", "a"]
    assert card["practice"]["correct_answer"] == "b"
    assert audit_practice(card["practice"]) == []


def test_clean_reinforcement_rejects_missing_items_contract():
    assert clean_reinforcement("not-json", [_question()]) == (
        [],
        ["输出无法解析为契约 JSON（缺 items 数组）"],
    )


def test_clean_reinforcement_reports_missing_items_and_invalid_practice():
    raw = json.dumps({"items": [{"questionId": "custom_q1"}]}, ensure_ascii=False)
    cards, issues = clean_reinforcement(raw, [_question(), _question("custom_q2")])
    assert cards == []
    assert issues == ["items 数量 1 少于错题数 2", "items[0] 缺 practice 对象"]


def test_clean_reinforcement_rewrites_drifted_id_and_audits_practice():
    raw = json.dumps(
        {
            "items": [
                {
                    "questionId": "invented",
                    "point": " 核心考点 ",
                    "recap": " 回顾内容 ",
                    "practice": {
                        "question_type": "single",
                        "question_text": "新题",
                        "options": [
                            {"option_id": "a", "option_text": "A"},
                            {"option_id": "b", "option_text": "B"},
                        ],
                        "correct_answer": "b",
                        "explanation": "答案是 B",
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    cards, issues = clean_reinforcement(raw, [_question()])
    assert issues == []
    assert cards[0]["questionId"] == "custom_q1"
    assert cards[0]["practice"]["question_id"] == "custom_q1-r"
    assert cards[0]["practice"]["options"][0] == {"option_id": "a", "option_text": "A"}
