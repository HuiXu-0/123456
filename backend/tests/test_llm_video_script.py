import pytest

from app.core import llm as llm_module
from app.core.llm_deepseek import LLMGenerationError
from app.core.llm_video_script import (
    VIDEO_SCRIPT_NN,
    clean_video_script,
    generate_mock_video_script,
    video_scene,
)


def test_llm_module_keeps_video_script_compatibility_alias():
    assert llm_module._VIDEO_SCRIPT_NN is VIDEO_SCRIPT_NN


def test_nn_video_script_baseline_keeps_five_complete_scenes():
    assert len(VIDEO_SCRIPT_NN) == 5
    assert VIDEO_SCRIPT_NN[0]["title"] == "课程导入 · 神经网络基础"
    assert VIDEO_SCRIPT_NN[-1]["title"] == "学习闭环"
    for scene in VIDEO_SCRIPT_NN:
        assert set(scene) == {"title", "points", "narration"}
        assert scene["points"]
        assert scene["narration"]


def test_video_scene_and_llm_compatibility_wrapper_match():
    expected = {"title": "标题", "points": ["要点"], "narration": "旁白"}
    assert video_scene("标题", ["要点"], "旁白") == expected
    assert llm_module.LLMClient._video_scene("标题", ["要点"], "旁白") == expected


def test_mock_video_script_uses_nn_baseline_and_topic_specific_fallback():
    nn = generate_mock_video_script("nn", "神经网络", "初级", "")
    assert nn == {"title": "神经网络基础", "scenes": VIDEO_SCRIPT_NN}
    nn["scenes"][0]["title"] = "已修改"
    assert VIDEO_SCRIPT_NN[0]["title"] == "课程导入 · 神经网络基础"

    custom = generate_mock_video_script(
        "custom", "图搜索", "高级", "图上的路径搜索、状态更新与终止条件"
    )
    assert custom["title"] == "图搜索"
    assert len(custom["scenes"]) == 5
    assert all(scene["title"] and scene["points"] and scene["narration"] for scene in custom["scenes"])
    assert "高级" in custom["scenes"][0]["narration"]
    assert custom["scenes"][0]["points"][1] == "图上的路径搜索、状态更新与终止条件"


def test_clean_video_script_normalizes_filters_and_truncates():
    data = {
        "title": " 主题视频 ",
        "scenes": [
            {
                "title": f" 场景{index} ",
                "points": [" 一 ", 2, 3.5, "四", "五", None],
                "narration": f" 旁白{index} ",
            }
            for index in range(6)
        ]
        + [{"title": "无旁白", "points": ["要点"]}],
    }
    result = clean_video_script(data, "默认标题")
    assert result["title"] == "主题视频"
    assert len(result["scenes"]) == 5
    assert result["scenes"][0] == {
        "title": "场景0",
        "points": ["一", "2", "3.5", "四"],
        "narration": "旁白0",
    }


def test_clean_video_script_uses_default_title_and_rejects_invalid_scenes():
    valid_scenes = [
        {"title": f"场景{index}", "points": ["要点"], "narration": "旁白"}
        for index in range(3)
    ]
    assert clean_video_script({"scenes": valid_scenes}, "默认标题")["title"] == "默认标题"

    with pytest.raises(LLMGenerationError, match="缺 scenes 数组"):
        clean_video_script({}, "标题")
    with pytest.raises(LLMGenerationError, match="有效场景不足 3 个"):
        clean_video_script({"scenes": valid_scenes[:2]}, "标题")
