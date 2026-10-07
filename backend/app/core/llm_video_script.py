"""视频分镜确定性基线、主题 Mock 生成与契约清洗。"""
from __future__ import annotations

from typing import Any

from app.core.llm_deepseek import LLMGenerationError

# 神经网络分镜脚本（接口文档 8.3 scenes）：与前端 LectureVideo 原 5 场景逐字对齐，
# narration 与原 NARRATION 一致——保证 nn 视频不回归；其余知识点按相同结构参数化生成。
VIDEO_SCRIPT_NN: list[dict[str, Any]] = [
    {
        "title": "课程导入 · 神经网络基础",
        "points": ["三步运算：求和 → 偏置 → 激活", "前向传播与反向传播", "由领域知识生成智能体定制"],
        "narration": "欢迎学习神经网络基础。本视频由领域知识生成智能体为你定制。",
    },
    {
        "title": "神经元的三步运算",
        "points": ["① 加权求和 Σ wᵢ·xᵢ", "② 加上偏置 + b", "③ 激活函数 ReLU(z)"],
        "narration": "一个神经元完成三步运算：加权求和、加上偏置、再经过激活函数输出。",
    },
    {
        "title": "前向传播",
        "points": ["输入与权重相乘求和", "加偏置得到 z", "ReLU 激活得到输出 a"],
        "narration": "前向传播时，输入与权重相乘求和，加偏置得到 z，再用 ReLU 激活得到输出。",
    },
    {
        "title": "常见激活函数",
        "points": ["ReLU：max(0,x)，最常用", "Sigmoid：压缩到 (0,1)", "Tanh：范围 (-1,1)，零均值"],
        "narration": "常见激活函数有 ReLU、Sigmoid 和 Tanh，其中 ReLU 计算快、最常用。",
    },
    {
        "title": "学习闭环",
        "points": ["神经元 → 前向传播", "激活 → 反向传播更新", "完成测验巩固理解"],
        "narration": "神经元、前向传播、激活、反向传播更新，构成了神经网络学习的完整闭环。",
    },
]


def video_scene(title: str, points: list[str], narration: str) -> dict[str, Any]:
    """装配单个视频场景。"""
    return {"title": title, "points": points, "narration": narration}


def generate_mock_video_script(
    kp_id: str, kp_name: str, difficulty: str, description: str
) -> dict[str, Any]:
    """生成确定性主题分镜脚本，供 Mock 与真实失败回落共用。"""
    if kp_id == "nn":
        return {"title": "神经网络基础", "scenes": [dict(scene) for scene in VIDEO_SCRIPT_NN]}
    desc = (description or "").strip()
    desc_point = desc[:18] if desc else f"{kp_name}的核心要点"
    scenes = [
        video_scene(
            f"课程导入 · {kp_name}",
            [f"按「{difficulty}」难度定制", desc_point, "建立整体认知框架"],
            f"欢迎学习{kp_name}。本视频由领域知识生成智能体按「{difficulty}」难度为你定制。",
        ),
        video_scene(
            f"{kp_name}的核心构成",
            [f"拆解{kp_name}的关键概念", "理清各部分之间的关系", "形成整体认知框架"],
            f"我们先拆解{kp_name}的核心构成，建立整体认知框架。",
        ),
        video_scene(
            f"{kp_name}在实践中如何运作",
            [f"一个{difficulty}难度的典型示例", "跟随流程逐步理解", "对照输入与输出"],
            f"接着通过一个{difficulty}难度的典型示例，看看{kp_name}在实践中如何运作。",
        ),
        video_scene(
            "常见方法与适用场景",
            [f"对比{kp_name}的相关方法", "明确各自适用场景", "避开典型误区"],
            f"再对比{kp_name}相关的常见方法与适用场景，避免典型误区。",
        ),
        video_scene(
            "要点回顾",
            [f"回顾{kp_name}的核心要点", "纳入完整学习闭环", "建议完成测验巩固"],
            f"最后回顾要点，把{kp_name}纳入完整的学习闭环。建议完成测验巩固理解。",
        ),
    ]
    return {"title": kp_name, "scenes": scenes}


def clean_video_script(data: dict[str, Any], kp_name: str) -> dict[str, Any]:
    """清洗分镜：有效场景 3–5 个，每场景要点 1–4 条。"""
    raw_scenes = data.get("scenes")
    if not isinstance(raw_scenes, list):
        raise LLMGenerationError("视频分镜脚本缺 scenes 数组")
    scenes: list[dict[str, Any]] = []
    for scene in raw_scenes:
        if not isinstance(scene, dict):
            continue
        title = str(scene.get("title") or "").strip()
        narration = str(scene.get("narration") or "").strip()
        points = [
            str(point).strip()
            for point in (scene.get("points") or [])
            if isinstance(point, (str, int, float)) and str(point).strip()
        ][:4]
        if not title or not narration or not points:
            continue
        scenes.append({"title": title, "points": points, "narration": narration})
    if len(scenes) < 3:
        raise LLMGenerationError(f"视频分镜有效场景不足 3 个（得到 {len(scenes)}）")
    scenes = scenes[:5]
    title = str(data.get("title") or "").strip() or kp_name
    return {"title": title, "scenes": scenes}
