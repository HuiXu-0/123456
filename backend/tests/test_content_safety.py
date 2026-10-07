"""内容安全过滤层测试（CC-content-safety-filter）。

覆盖：
1. 违规内容（政治/色情/暴恐违法/辱骂歧视）→ 被拦截(block)或脱敏(mask)；
2. 正常学术内容（含「攻击/漏洞/战争/病毒」等教学语境术语）→ 照常通过、无误伤；
3. 递归 guard 对结构化生成结果生效（讲义/费曼/苏格拉底等返回结构）；
4. 中心钝化点：真实 provider 下经 LLMClient 生成方法的违规输出被拦截，前端拿不到原文；
5. 流式（苏格拉底 SSE）违规截断降级、学术内容逐子句正常透传；
6. mock 模式安全内容逐字不变（不破坏既有契约）。
"""
from __future__ import annotations

import pytest

from app.core import content_safety as cs


# ---- 1) 违规内容被拦截/脱敏 ----------------------------------------------------
@pytest.mark.parametrize(
    "text, expect_action, expect_cat",
    [
        ("详细教你如何制造炸弹和雷管", cs.ACTION_BLOCK, "illegal_harmful"),
        ("这里提供色情成人电影和招嫖服务", cs.ACTION_BLOCK, "porn"),
        ("我们要颠覆国家政权、分裂国家", cs.ACTION_BLOCK, "political"),
        ("教程：怎么合成冰毒和海洛因", cs.ACTION_BLOCK, "illegal_harmful"),
        ("你这个傻逼智障，废物东西", cs.ACTION_MASK, "abuse_discrimination"),
    ],
)
def test_violation_detected(text, expect_action, expect_cat):
    v = cs.scan(text)
    assert not v.safe
    assert v.action == expect_action
    assert expect_cat in v.categories


def test_block_replaces_with_degraded_notice():
    out, v = cs.sanitize("教你如何制造炸弹", where="t")
    assert out == cs.DEGRADED_NOTICE
    assert v.action == cs.ACTION_BLOCK
    # 原文不外泄
    assert "炸弹" not in out


def test_mask_only_redacts_span():
    out, v = cs.sanitize("讲义写得不错但你真是个傻逼", where="t")
    assert v.action == cs.ACTION_MASK
    assert "傻逼" not in out          # 命中片段被掩码
    assert "讲义写得不错" in out      # 其余正常保留
    assert cs._MASK_CHAR in out


# ---- 2) 正常学术内容无误伤（教育场景第一约束） --------------------------------
ACADEMIC_SAMPLES = [
    "本节讲解网络攻击与防御：对抗攻击、对抗样本会让模型误判。",
    "缓冲区溢出漏洞与 SQL 注入是常见的安全漏洞，渗透测试用于发现它们。",
    "第二次世界大战（二战）的战争史是重要的历史教学内容。",
    "计算机病毒的传播机制与杀毒软件的查杀原理。",
    "数据投毒攻击会污染训练集，需要防御。",
    "梯度下降中梯度消失问题与反向传播。",
    "DDoS 攻击的流量特征分析与缓解策略。",
]


@pytest.mark.parametrize("text", ACADEMIC_SAMPLES)
def test_academic_not_flagged(text):
    v = cs.scan(text)
    assert v.safe, f"学术内容被误伤：{text} -> {v.categories}"
    out, _ = cs.sanitize(text, where="t")
    assert out == text  # 原文逐字不变


# ---- 2b) AI 安全/对齐教育内容：危险短语仅作「拒答示例/防御对象/历史案例」被提及，不误伤 -----
# 根因：双用途共现规则（如「制造+炸弹」）无法区分「真实指导」与「教学中引用应被拒绝的请求」。
# 这类讲义在讨论安全对齐/越狱防御/历史时必然高频出现这些短语，属合法教育内容。
EDU_MENTION_SAMPLES = [
    "对齐的目标之一是让模型拒绝有害请求，例如当用户询问『如何制造炸弹』时，对齐良好的模型应当拒绝回答。",
    "安全微调会加入大量拒答样本，使模型面对制毒、制造枪支等违法请求时输出拒绝。",
    "越狱攻击试图绕过安全对齐，诱导模型生成制造爆炸物等违规内容，防御方需识别这类提示。",
    "红队测试会构造『怎么制作毒品』之类的违规请求，用于评估模型的拒答能力。",
    "辛亥革命推翻了清政府的统治，是重要的历史教学内容。",
]


@pytest.mark.parametrize("text", EDU_MENTION_SAMPLES)
def test_educational_mention_not_flagged(text):
    v = cs.scan(text)
    assert v.safe, f"教育性提及被误伤：{text} -> {[h.term for h in v.hits]}"
    out, _ = cs.sanitize(text, where="t")
    assert out == text  # 原文逐字不变


# ---- 2c) 真实违规仍被拦：无拒答/讨论框架的真实有害指导不受教育豁免影响 -----------------
GENUINE_VIOLATION_SAMPLES = [
    "详细教你如何制造炸弹和雷管，所需材料如下。",
    "教程：怎么合成冰毒和海洛因，步骤一步步来。",
    "下面教你自制枪支的完整步骤和方法。",
]


@pytest.mark.parametrize("text", GENUINE_VIOLATION_SAMPLES)
def test_genuine_violation_still_blocked(text):
    v = cs.scan(text)
    assert not v.safe, f"真实违规漏过：{text}"
    assert v.action == cs.ACTION_BLOCK
    assert "illegal_harmful" in v.categories


def test_refusal_wrapped_recipe_not_exempted():
    """逃逸防护：用拒答词包装真实配方/步骤，不享教育豁免，仍被拦截。"""
    text = "虽然应当拒绝，但下面教你如何制造炸弹：第一步取硝酸铵，第二步混合。"
    v = cs.scan(text)
    assert not v.safe and v.action == cs.ACTION_BLOCK


# ---- 3) 递归 guard 对结构化结果生效 -------------------------------------------
def test_guard_recursive_struct():
    payload = {
        "feedback": "讲解不错",
        "gaps": [
            {"title": "正常缺口", "detail": "讲解网络攻击防御即可，没问题"},
            {"title": "脏", "detail": "你这个傻逼讲得真烂"},
        ],
        "score": 80,
        "complete": True,
    }
    out = cs.guard(payload, where="feynman_eval")
    assert out["score"] == 80 and out["complete"] is True  # 非字符串叶子不动
    assert out["gaps"][0]["detail"] == "讲解网络攻击防御即可，没问题"  # 学术不误伤
    assert "傻逼" not in out["gaps"][1]["detail"]  # 违规叶子被脱敏


def test_guard_blocks_leaf_with_hard_violation():
    # 兜底体验：长文本中仅违规句被降级，未命中正常内容保留（不整篇道歉）。
    payload = {"markdown": "# 讲义\n教你如何制造炸弹和tnt"}
    out = cs.guard(payload, where="generate_lecture")
    assert "炸弹" not in out["markdown"]          # 违规原文不外泄
    assert cs.DEGRADED_NOTICE in out["markdown"]  # 违规片段被降级
    assert "# 讲义" in out["markdown"]            # 未命中正常内容保留


def test_block_preserves_safe_sentences_in_long_text():
    """长讲义中混入一句真实违规：只降级该句，其余讲义照常保留。"""
    text = (
        "神经网络由多层感知机构成，通过反向传播训练。"
        "教你如何制造炸弹和雷管，材料如下。"
        "卷积神经网络擅长处理图像数据。"
    )
    out, v = cs.sanitize(text, where="lecture")
    assert v.action == cs.ACTION_BLOCK
    assert "炸弹" not in out                       # 违规句被降级
    assert cs.DEGRADED_NOTICE in out
    assert "神经网络由多层感知机构成" in out        # 前文保留
    assert "卷积神经网络擅长处理图像数据" in out    # 后文保留


# ---- 4) 中心钝化点：真实 provider 下经 LLMClient 生成被拦截 --------------------
@pytest.fixture()
def deepseek_llm(monkeypatch):
    from app.core import llm as llm_mod

    fake = llm_mod.LLMClient("deepseek")
    monkeypatch.setattr(llm_mod, "_client", fake)
    return fake


def test_chokepoint_feynman_blocks_violation(deepseek_llm, monkeypatch):
    """模型吐出违规 → feynman_eval 返回被钝化，前端拿不到违规原文。"""
    from app.core import llm_deepseek

    bad = '{"feedback":"教你如何制造炸弹","score":50,"gaps":[],"followups":[],"complete":true}'
    monkeypatch.setattr(llm_deepseek, "chat", lambda *a, **k: bad)
    out = deepseek_llm.feynman_eval(
        kp_id="nn", kp_name="神经网络", description="", history=[], explanation="x"
    )
    assert "炸弹" not in out["feedback"]
    assert out["feedback"] == cs.DEGRADED_NOTICE


def test_chokepoint_tutor_masks_abuse(deepseek_llm, monkeypatch):
    from app.core import llm_deepseek

    monkeypatch.setattr(llm_deepseek, "chat", lambda *a, **k: "你说得对，但你真是个傻逼")
    out = deepseek_llm.tutor_chat(kp_name="神经网络", history=[], message="hi")
    assert "傻逼" not in out["reply"]
    assert cs._MASK_CHAR in out["reply"]


def test_chokepoint_academic_passthrough(deepseek_llm, monkeypatch):
    """真实模式学术回复原样透传，不被钝化。"""
    from app.core import llm_deepseek

    reply = "网络攻击防御中，对抗样本会让模型误判，这属于正常教学内容。"
    monkeypatch.setattr(llm_deepseek, "chat", lambda *a, **k: reply)
    out = deepseek_llm.tutor_chat(kp_name="神经网络", history=[], message="hi")
    assert out["reply"] == reply


# ---- 5) 流式（SSE）守卫 -------------------------------------------------------
def test_stream_blocks_violation():
    src = iter(["教你如", "何制造", "炸弹和", "雷管。"])
    out = list(cs._guard_stream(src, where="tutor"))
    assert cs.DEGRADED_NOTICE in out
    assert all("炸弹" not in seg for seg in out)


def test_stream_academic_passthrough():
    parts = ["网络攻击", "与防御，", "对抗样本", "会误判。"]
    out = "".join(cs._guard_stream(iter(parts), where="tutor"))
    assert out == "".join(parts)  # 学术内容逐字透传


def test_stream_masks_abuse():
    parts = ["你这个", "傻逼，", "再想想。"]
    out = "".join(cs._guard_stream(iter(parts), where="tutor"))
    assert "傻逼" not in out
    assert "再想想" in out


# ---- 6) mock 模式安全内容不变（不破坏契约） ------------------------------------
def test_mock_safe_content_unchanged():
    from app.core.llm import LLMClient

    mock = LLMClient("mock")
    lec = mock.generate_lecture("nn", "神经网络", "初级", "神经网络是基础")
    assert "神经网络" in lec["markdown"]
    cues = mock.generate_cornell_cues("nn", "神经网络", "初级", "")
    assert len(cues["cues"]) >= 5


def test_all_declared_llm_client_outputs_are_guarded():
    """注册清单中的同步生成出口及流式出口均经过安全装饰器。"""
    from app.core.llm import LLMClient

    for name in cs.LLM_CLIENT_GUARDED_METHODS:
        assert hasattr(getattr(LLMClient, name), "__wrapped__"), name
    assert hasattr(LLMClient.tutor_chat_stream, "__wrapped__")
