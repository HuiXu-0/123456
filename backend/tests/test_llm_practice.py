import pytest

from app.core.llm import audit_practice as compatibility_audit_practice
from app.core.llm_practice import audit_practice


def _practice(**overrides):
    value = {
        "question_type": "single",
        "question_text": "激活函数的主要作用是什么？",
        "options": [
            {"option_id": "a", "option_text": "引入非线性"},
            {"option_id": "b", "option_text": "增加样本数"},
        ],
        "correct_answer": "a",
        "explanation": "激活函数为网络引入非线性表达能力。",
    }
    value.update(overrides)
    return value


def test_llm_module_keeps_audit_practice_compatibility_export():
    assert compatibility_audit_practice is audit_practice
    assert audit_practice(_practice()) == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"question_type": "essay"}, "question_type 非法"),
        ({"question_text": "  "}, "question_text 为空"),
        ({"options": [{"option_id": "a"}]}, "options 少于 2 个"),
        (
            {"options": [{"option_id": "a"}, {"option_id": "a"}]},
            "option_id 重复",
        ),
        ({"correct_answer": "missing"}, "不在 options 中"),
        ({"explanation": ""}, "explanation 为空"),
    ],
)
def test_audit_practice_reports_each_contract_violation(overrides, expected):
    assert any(expected in issue for issue in audit_practice(_practice(**overrides)))


def test_audit_practice_validates_multiple_answer_shape_and_membership():
    valid = _practice(question_type="multiple", correct_answer=["a", "b"])
    assert audit_practice(valid) == []

    wrong_type = _practice(question_type="single", correct_answer=["a"])
    assert "correct_answer 为数组但题型不是 multiple" in audit_practice(wrong_type)

    missing = _practice(question_type="multiple", correct_answer=["a", "missing"])
    assert any("未全部出现在 options 中" in issue for issue in audit_practice(missing))
