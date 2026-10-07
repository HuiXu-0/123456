# -*- coding: utf-8 -*-
"""B8 差异化画像种子（答辩演示素材）：3 组背景 → 画像 → 叙述 → 推荐难度 → 讲义。

三组种子（覆盖典型学习者谱系）：
  A. 零基础转行 —— 行政岗自学 Python，无任何 ML 项目经验；
  B. 科班进阶 —— 计算机本科应届，修过机器学习课程、做过 CNN 课设；
  C. 资深补强 —— 5 年算法工程师，ML/DL/CNN 扎实，需补 Transformer 与大模型微调。

链路（与线上接口同源的 service 调用）：
  profile.parse_profile（4.1 简历解析）→ profile.generate_narrative（4.2 两段叙述）
  → 推荐难度推导（演示口径：6 维平均 level <45 入门 / 45–70 初级 / >70 高级）
  → resource.generate_lecture（8.2，对薄弱主线 transformer 按推荐难度生成讲义）。

运行（backend 目录）：python scripts/demo_profiles.py
- LLM_PROVIDER=deepseek：真实抽取/生成，三组画像与讲义差异显著（答辩演示形态）；
- LLM_PROVIDER=mock：确定性基线 + 关键词抬升，差异幅度有限（仅链路演示）。
脚本会写入演示账号 learner_001 的讲义缓存与掌握度（transformer→learning），
均为演示数据，可随时经 init_db 重置。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.entities import JobSnapshot, User  # noqa: E402
from app.schemas.profile import NarrativeRequest  # noqa: E402
from app.services import profile as profile_service  # noqa: E402
from app.services import resource as resource_service  # noqa: E402

# 薄弱主线知识点（前端演示主线同款）与目标岗位
TARGET_KP = "transformer"
TARGET_JOB_ID = "llm-app"

# 简历对 6 个能力维度逐一给出明确信号（机器学习基础/神经网络/深度学习/注意力机制/
# Transformer/大模型微调）——画像清洗对「材料未体现的维度」按基线回填（4.1 防幻觉
# 约定），演示种子若留空维度会被基线抹平差异，故逐维写明。
SEEDS: list[dict[str, str]] = [
    {
        "key": "A",
        "label": "零基础转行",
        "resume": (
            "个人简历\n姓名：林晓\n学历：本科（工商管理）\n"
            "工作经历：某制造企业行政专员 4 年，负责会议组织、档案与流程管理。\n"
            "技能：Office 办公自动化；2025 年起自学 Python 基础语法，"
            "完成过 Excel 报表自动化小脚本。\n"
            "AI 相关基础：机器学习基础、神经网络、深度学习、注意力机制、Transformer、"
            "大模型微调均为零基础，从未接触过相关课程或项目。\n"
            "目标：转行人工智能方向，从零开始系统学习机器学习。"
        ),
        "description": "完全没有机器学习基础，六个能力维度全部从零开始，希望从最基础的概念学起。",
    },
    {
        "key": "B",
        "label": "科班进阶",
        "resume": (
            "个人简历\n姓名：陈航\n学历：本科（计算机科学与技术，应届）\n"
            "课程：数据结构、概率统计、机器学习（课程设计：基于 CNN 的手写数字识别，"
            "准确率 99.1%）。\n"
            "技能自评：机器学习基础扎实（课程 92 分）；神经网络基础较好，熟悉反向传播"
            "与梯度下降；深度学习入门水平，完成过 CNN 课设；注意力机制只了解概念；"
            "Transformer 尚未系统学习；大模型微调没有接触。\n"
            "目标：求职算法岗，系统补齐深度学习进阶内容。"
        ),
        "description": "计算机科班应届生，机器学习与神经网络基础好，注意力机制及之后的内容是短板。",
    },
    {
        "key": "C",
        "label": "资深补强",
        "resume": (
            "个人简历\n姓名：吴桐\n学历：硕士（人工智能）\n"
            "工作经历：某互联网公司算法工程师 5 年。主导推荐系统排序模型迭代"
            "（GBDT→DNN→多任务学习），深度学习与 CNN 工程经验扎实；"
            "熟悉特征工程、损失设计、分布式训练与线上 AB。\n"
            "技能自评：机器学习基础、神经网络、深度学习均为生产级精通水平；"
            "注意力机制读过论文有概念性理解；Transformer 仅停留在论文阅读，"
            "未做过工程实现；大模型微调（LoRA/RLHF）完全未实践。\n"
            "目标：转向大模型应用方向，补强 Transformer 架构与微调技术。"
        ),
        "description": "资深算法工程师，传统深度学习生产级水平，需要定向补强 Transformer 和大模型微调实战。",
    },
]


def recommend_difficulty(skills: list[dict[str, Any]]) -> str:
    """演示口径：6 维平均 level → 讲义难度档（8.2 资源页三档）。

    阈值 35/60：低于 35 为零基础（入门），35–60 为有基础待进阶（初级），
    高于 60 表示整体功底扎实、可直接吸收高密度内容（高级）。
    """
    avg = sum(s["level"] for s in skills) / max(len(skills), 1)
    if avg < 35:
        return "入门"
    if avg <= 60:
        return "初级"
    return "高级"


def narrative_text(paragraphs: list[list[dict[str, Any]]]) -> list[str]:
    return ["".join(seg["text"] for seg in para) for para in paragraphs]


def main() -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "learner_001").one()
        job = db.get(JobSnapshot, TARGET_JOB_ID)
        target_job = {"name": job.payload["name"], "radar": job.payload["radar"]}
        print(f"== B8 差异化画像种子演示（provider={settings.llm_provider}，"
              f"目标岗位：{target_job['name']}）==")

        summary: list[dict[str, Any]] = []
        for seed in SEEDS:
            print(f"\n---- 种子 {seed['key']}：{seed['label']} ----")
            parsed = profile_service.parse_profile(
                user,
                uploads=[(f"简历-{seed['label']}.txt", seed["resume"].encode("utf-8"))],
                description=seed["description"],
            )
            skills = parsed["skills"]
            levels = "  ".join(f"{s['name']}={s['level']}" for s in skills)
            print(f"  画像：{parsed['education']['value']} · {parsed['major']['value']}"
                  f" · 目标 {parsed['goal']['value']}")
            print(f"  6 维：{levels}")

            narrative = profile_service.generate_narrative(
                NarrativeRequest(
                    draft={k: parsed[k] for k in ("education", "major", "goal", "skills")},
                    materials=parsed["materials"],
                    targetJob=target_job,
                )
            )
            for i, text in enumerate(narrative_text(narrative["paragraphs"]), 1):
                print(f"  叙述{i}：{text[:80]}")

            difficulty = recommend_difficulty(skills)
            lecture = resource_service.generate_lecture(
                db, user.id, TARGET_KP, difficulty
            )
            title = lecture["markdown"].split("\n", 1)[0]
            print(f"  推荐难度：{difficulty} → 讲义《{title.lstrip('# ')}》"
                  f"（幻觉率 {lecture['hallucinationRate']}）")
            weakest = min(skills, key=lambda s: s["level"])
            summary.append({
                "label": seed["label"],
                "avg": round(sum(s["level"] for s in skills) / len(skills), 1),
                "weakest": f"{weakest['name']}({weakest['level']})",
                "difficulty": difficulty,
                "title": title.lstrip("# "),
            })

        print("\n== 三组对照（画像 → 路径侧重 → 讲义难度可感知差异）==")
        print(f"  {'组别':<8}{'6维均值':>8}  {'最弱维':<20}{'推荐难度':<6}讲义")
        for row in summary:
            print(f"  {row['label']:<8}{row['avg']:>8}  {row['weakest']:<20}"
                  f"{row['difficulty']:<6}{row['title']}")
        distinct = len({row["difficulty"] for row in summary})
        print(f"\n  难度档区分：{distinct}/3 组互异"
              + ("（mock 模式差异有限属预期，演示请用 deepseek）"
                 if settings.llm_provider == "mock" and distinct < 3 else ""))
    finally:
        db.close()


if __name__ == "__main__":
    main()
