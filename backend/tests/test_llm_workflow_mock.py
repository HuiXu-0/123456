import json

import pytest

from app.core import llm as llm_module
from app.core.llm_workflow_mock import diagnose, generate, review, set_force_critic_low


@pytest.fixture(autouse=True)
def _reset_critic_hook():
    set_force_critic_low(False)
    yield
    set_force_critic_low(False)


def test_llm_module_keeps_critic_hook_compatibility_alias():
    assert llm_module.set_force_critic_low is set_force_critic_low


def test_diagnosis_uses_profile_and_mastery_deterministically():
    variables = {
        "kpId": "nn",
        "kpName": "神经网络",
        "profileSummary": "零基础，目标是转岗",
        "masteryStatus": json.dumps(
            {"ml": "passed", "nn": "learning", "transformer": "learning"},
            ensure_ascii=False,
        ),
    }
    first = diagnose(variables)
    assert first == diagnose(variables)
    assert first["weakKpIds"] == ["nn", "transformer", "finetune"]
    assert "零基础，目标是转岗" in first["reasoning"]
    assert "已通过 1 项、待巩固 2 项" in first["reasoning"]


@pytest.mark.parametrize("mastery_status", ["not-json", "[]", None])
def test_diagnosis_invalid_mastery_falls_back_without_error(mastery_status):
    result = diagnose({"masteryStatus": mastery_status})
    assert result["weakKpIds"] == ["attention", "transformer", "finetune"]
    assert "画像尚未采集（按通用基线）" in result["reasoning"]


def test_generation_forwards_defaults_and_depth_tier_to_lecture_builder():
    calls = []

    def builder(kp_name, difficulty, description, tier):
        calls.append((kp_name, difficulty, description, tier))
        return f"# {kp_name}\n{difficulty}/{tier}"

    result = generate({"depthTier": "beginner"}, builder)
    assert calls == [("神经网络", "初级", "", "beginner")]
    assert result == {"markdown": "# 神经网络\n初级/beginner"}


def test_critic_default_and_forced_low_outputs_are_stable():
    normal = review({}, 0.021)
    assert normal == {
        "passed": True,
        "validationScore": 0.93,
        "hallucinationRate": 0.021,
        "issues": [],
    }

    set_force_critic_low(True)
    low = review({}, 0.021)
    assert low["passed"] is False
    assert low["validationScore"] == 0.42
    assert low["hallucinationRate"] == 0.18
    assert low["issues"]
