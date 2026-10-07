"""B5-b 契约测试：真实生成替换（RAG → 生成 Agent → 审核 Agent + deepseek provider）。

验收口径（执行方案 B5 后半 + 接口文档 8.2/4.1/4.2/15.3）：
- 15.3 逐句接地：句子与来源切片 embedding 相似度，低于阈值句占比 = hallucinationRate；
  无任何 RAG 来源（纯模板/兜底）→ 置 0；
- deepseek provider：经 app.core.llm_deepseek.chat（openai 兼容 SDK），
  调用失败 → LLMGenerationError → 路由映射 code 2001 / HTTP 500；
- /resource/lecture 真实模式：B3 检索 → generator → critic（接地口径）→
  sources 来自检索 chunk（document_title/source_location/score→confidence）；
- /profile/parse|narrative 真实模式：LLM 抽取/叙述 + 防幻觉清洗
  （枚举校验、level 0-100 截断、sourceId 必须取材料 id 否则置 null）；
- llm_provider=mock 时三接口行为与 B2 逐字等价（确定性输出，前端演示兜底不破坏）。

deepseek 路径全部 monkeypatch llm_deepseek.chat —— 无 Key 即可跑（Mock-first 纪律）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app

AGENT_IDS = ["diagnosis", "generation", "critic"]


@pytest.fixture(scope="module")
def client():
    # with 触发 lifespan：建表 + 幂等种子
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db(client):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    res = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['data']['token']}"}


@pytest.fixture(scope="module")
def learner_headers(client) -> dict[str, str]:
    return _login(client, "learner_001", "123456")


@pytest.fixture()
def deepseek_llm(monkeypatch):
    """把进程内 LLM 单例切到 deepseek provider（测试后自动还原）。"""
    from app.core import llm as llm_mod

    fake = llm_mod.LLMClient("deepseek")
    monkeypatch.setattr(llm_mod, "_client", fake)
    return fake


# ---- 15.3 逐句接地（grounding） ------------------------------------------------

def test_grounding_grounded_sentences_zero_rate():
    """句子逐字来自来源切片 → 全部接地，幻觉率 0。"""
    from app.rag.grounding import sentence_grounding

    ctx = ["神经网络由多层神经元组成，每个神经元做加权求和。反向传播通过链式法则逐层计算梯度。"]
    text = "神经网络由多层神经元组成，每个神经元做加权求和。反向传播通过链式法则逐层计算梯度。"
    result = sentence_grounding(text, ctx)
    assert result["totalSentences"] >= 2
    assert result["hallucinationRate"] == 0.0
    assert result["ungroundedSentences"] == []


def test_grounding_ungrounded_sentences_detected():
    """与来源完全无关的句子 → 被标记未接地，幻觉率 > 0。"""
    from app.rag.grounding import sentence_grounding

    ctx = ["神经网络由多层神经元组成，每个神经元做加权求和后经激活函数输出。"]
    text = (
        "神经网络由多层神经元组成，每个神经元做加权求和后经激活函数输出。"
        "明朝永乐年间郑和七下西洋的宝船编队规模空前庞大。"
    )
    result = sentence_grounding(text, ctx)
    assert result["totalSentences"] == 2
    assert 0.0 < result["hallucinationRate"] <= 1.0
    assert any("郑和" in s for s in result["ungroundedSentences"])


def test_grounding_no_context_returns_zero():
    """15.3：无任何 RAG 来源（纯模板/兜底）→ 幻觉率按约定置 0。"""
    from app.rag.grounding import sentence_grounding

    result = sentence_grounding("任意一句没有来源的生成内容，应按约定置零。", [])
    assert result["hallucinationRate"] == 0.0
    assert result["ungroundedSentences"] == []


def test_grounding_skips_code_blocks():
    """代码块不参与逐句统计（非自然语言句子，不应计为幻觉）。"""
    from app.rag.grounding import sentence_grounding

    ctx = ["神经网络由多层神经元组成，每个神经元做加权求和后经激活函数输出。"]
    text = (
        "神经网络由多层神经元组成，每个神经元做加权求和后经激活函数输出。\n\n"
        "```python\nimport numpy as np\nz = np.dot(x, w) + b\n```\n"
    )
    result = sentence_grounding(text, ctx)
    assert result["totalSentences"] == 1
    assert result["hallucinationRate"] == 0.0


# ---- deepseek provider：complete（diagnosis / generation） ----------------------

def test_deepseek_generation_returns_markdown(deepseek_llm, monkeypatch):
    from app.core import llm_deepseek

    monkeypatch.setattr(
        llm_deepseek, "chat", lambda prompt, system=None: "# 讲义\n\n真实生成的内容。"
    )
    out = deepseek_llm.complete("生成 prompt", agent_id="generation")
    assert out["markdown"] == "# 讲义\n\n真实生成的内容。"


def test_deepseek_diagnosis_parses_json_with_fallback(deepseek_llm, monkeypatch):
    from app.core import llm_deepseek

    # ① 返回合法 JSON（含包裹文本）→ 解析出结构化字段
    monkeypatch.setattr(
        llm_deepseek,
        "chat",
        lambda prompt, system=None: (
            '诊断结果如下：{"weakKpIds": ["attention", "transformer"], '
            '"summary": "注意力机制为最薄弱环节", "reasoning": "依据画像"}'
        ),
    )
    out = deepseek_llm.complete("诊断 prompt", agent_id="diagnosis")
    assert out["weakKpIds"] == ["attention", "transformer"]
    assert out["summary"] == "注意力机制为最薄弱环节"

    # ② 非 JSON 输出 → 降级兜底：summary 取自由文本，不抛错
    monkeypatch.setattr(
        llm_deepseek, "chat", lambda prompt, system=None: "学习者在注意力机制方面较薄弱。"
    )
    out = deepseek_llm.complete("诊断 prompt", agent_id="diagnosis")
    assert out["weakKpIds"] == []
    assert "注意力机制" in out["summary"]


def test_deepseek_without_key_raises_llm_error():
    """无 DEEPSEEK_API_KEY 时真实调用抛 LLMGenerationError（不裸抛连接异常）。"""
    from app.core.config import settings
    from app.core.llm import LLMGenerationError
    from app.core.llm_deepseek import chat

    if settings.deepseek_api_key:
        pytest.skip("环境已配置真实 Key，跳过无 Key 用例")
    with pytest.raises(LLMGenerationError):
        chat("测试 prompt")


# ---- profile：extract_profile（mock 等价 + 真实清洗） ---------------------------

def test_extract_profile_mock_equals_b2():
    """mock provider：extract_profile 与 B2 的 parse_skills + 固定字段逐字等价。"""
    from app.core.llm import LLMClient

    llm = LLMClient("mock")
    text = "我熟悉机器学习和 transformer 结构"
    extracted = llm.extract_profile(text, source="text")
    assert extracted["education"] == "硕士"
    assert extracted["major"] == "人工智能"
    assert extracted["goal"] == "职业培训"
    assert extracted["skills"] == llm.parse_skills(text, source="text")


def test_extract_profile_deepseek_clamps_and_fills(deepseek_llm, monkeypatch):
    """真实抽取：level 截断 0-100、非法枚举回落、6 维齐全、source 统一。"""
    from app.core import llm_deepseek
    from app.core.llm import ABILITY_DIMENSIONS

    monkeypatch.setattr(
        llm_deepseek,
        "chat",
        lambda prompt, system=None: (
            '{"education": "硕士", "major": "炼丹学", "goal": "职业培训", '
            '"skills": [{"name": "机器学习基础", "level": 150}, '
            '{"name": "不存在的维度", "level": 80}, '
            '{"name": "Transformer", "level": -5}]}'
        ),
    )
    out = deepseek_llm.extract_profile("简历文本：精通机器学习。", source="resume")
    assert out["education"] == "硕士"
    assert out["major"] == "其他"  # 非法枚举 → 其他
    assert out["goal"] == "职业培训"
    skills = {s["name"]: s for s in out["skills"]}
    assert list(skills) == list(ABILITY_DIMENSIONS)  # 固定 6 维、顺序一致
    assert skills["机器学习基础"]["level"] == 100  # 150 → 截断
    assert skills["Transformer"]["level"] == 0  # -5 → 截断
    assert all(s["source"] == "resume" for s in out["skills"])


def test_extract_profile_deepseek_empty_text_no_llm_call(deepseek_llm, monkeypatch):
    """无材料（manual）时不得调用 LLM 编造经历（4.1 防幻觉约束）——返回确定性基线。"""
    from app.core import llm_deepseek

    def _boom(prompt, system=None):
        raise AssertionError("无材料时不应调用 LLM")

    monkeypatch.setattr(llm_deepseek, "chat", _boom)
    out = deepseek_llm.extract_profile("", source="manual")
    assert len(out["skills"]) == 6
    assert all(s["source"] == "manual" for s in out["skills"])


# ---- narrative：真实叙述 + sourceId 清洗 ---------------------------------------

def test_narrative_deepseek_sanitizes_segments(deepseek_llm, monkeypatch):
    """真实叙述：恰好两段；非法 sourceId 置 null；非法 tone 丢弃（4.2 契约）。"""
    from app.core import llm_deepseek

    monkeypatch.setattr(
        llm_deepseek,
        "chat",
        lambda prompt, system=None: (
            '{"paragraphs": [['
            '{"text": "该学习者具备 "}, '
            '{"text": "人工智能", "tone": "key", "sourceId": "m1"}, '
            '{"text": " 背景。", "sourceId": "m99"}], ['
            '{"text": "Transformer 偏弱", "tone": "loud", "sourceId": null}]]}'
        ),
    )
    draft = {
        "education": {"value": "硕士", "source": "resume"},
        "major": {"value": "人工智能", "source": "resume"},
        "goal": {"value": "职业培训", "source": "text"},
        "skills": [{"name": "机器学习基础", "level": 85, "source": "resume"}],
    }
    materials = [{"id": "m1", "label": "我的简历.pdf", "kind": "doc"}]
    paragraphs = deepseek_llm.generate_narrative(draft, materials, None)
    assert len(paragraphs) == 2
    seg_key = paragraphs[0][1]
    assert seg_key["tone"] == "key" and seg_key["sourceId"] == "m1"
    assert paragraphs[0][2]["sourceId"] is None  # m99 不在材料表 → 置 null
    assert "tone" not in paragraphs[1][0]  # 非法 tone "loud" → 丢弃


# ---- critic：真实模式走 15.3 接地口径 -------------------------------------------

def test_critic_real_mode_uses_grounding(db, deepseek_llm):
    """deepseek 模式 critic 不调 LLM：逐句接地计算 score / hallucinationRate。"""
    from app.agents.critic_agent import run_critic

    chunk = "神经网络由多层神经元组成，每个神经元做加权求和后经激活函数输出。"
    res = run_critic(
        db,
        draft_content=chunk,
        rag_context=[{"id": "c1", "content": chunk, "metadata": {}, "score": 0.9}],
    )
    out = res["output"]
    assert out["hallucinationRate"] == 0.0
    assert out["validationScore"] == 1.0
    assert out["passed"] is True
    assert res["name"] == "内容审核校验Agent"  # 11.2 name 与模板表逐字一致
    assert res["prompt"]  # 渲染后 prompt 仍记录（trace/promptExcerpt 可用）


# ---- lecture：真实模式全链路（RAG → 生成 → 审核 → 真实 sources） -----------------

def test_lecture_real_mode_workflow_sources_and_rate(db, deepseek_llm, monkeypatch):
    from app.core import llm_deepseek
    from app.models.entities import ResourceCache
    from app.services import resource as resource_service

    grounded_md = "神经网络由多层神经元组成。反向传播通过链式法则逐层计算梯度。"
    fake_chunks = [
        {
            "id": "doc_777#0",
            "content": "神经网络由多层神经元组成。反向传播通过链式法则逐层计算梯度。",
            "metadata": {
                "document_id": "doc_777",
                "document_title": "神经网络教程",
                "source_location": "第一章 / 段落 1",
            },
            "score": 0.91,
        }
    ]
    monkeypatch.setattr(
        resource_service,
        "_retrieve_for_lecture",
        lambda kp_id, kp_name, difficulty: fake_chunks,
    )

    calls = {"n": 0}

    def fake_chat(prompt, system=None):
        calls["n"] += 1
        if "诊断" in prompt or (system and "诊断" in system):
            return '{"weakKpIds": ["nn"], "summary": "神经网络基础薄弱", "reasoning": "画像"}'
        return grounded_md

    monkeypatch.setattr(llm_deepseek, "chat", fake_chat)

    def _purge_case_rows():
        """只清理本用例的缓存行（kp=nn + provider 后缀），不碰 mock 与真实演示缓存。"""
        db.query(ResourceCache).filter(
            ResourceCache.kp_id == "nn",
            ResourceCache.difficulty == "高级",
            ResourceCache.kind == "lecture@deepseek",
        ).delete()
        db.commit()

    _purge_case_rows()

    payload = resource_service.generate_lecture(db, "u_10001", "nn", "高级")
    assert payload["kpId"] == "nn" and payload["difficulty"] == "高级"
    # 真实生成内容为正文主体（非 B2 模板讲义）；图文增强在其后追加结构图解（图解优先）
    assert payload["markdown"].startswith(grounded_md)
    assert "```mermaid" in payload["markdown"]
    assert payload["hallucinationRate"] == 0.0  # 全部句子接地
    # sources 来自检索 chunk：document_title/source_location → title，score → confidence
    assert payload["sources"] == [
        {"title": "神经网络教程 · 第一章 / 段落 1", "type": "文档", "confidence": 0.91}
    ]
    assert calls["n"] >= 2  # diagnosis + generation 至少各一次真实调用

    # 缓存按 provider 隔离：不污染 mock 的 kind=lecture 行
    # （限定 difficulty：dev 库可能存有其它难度档的真实演示缓存，B8 起属正常态）
    row = (
        db.query(ResourceCache)
        .filter(
            ResourceCache.kp_id == "nn",
            ResourceCache.difficulty == "高级",
            ResourceCache.kind == "lecture@deepseek",
        )
        .one_or_none()
    )
    assert row is not None and row.payload == payload

    # 第二次命中缓存：LLM 不再被调用
    before = calls["n"]
    again = resource_service.generate_lecture(db, "u_10001", "nn", "高级")
    assert again == payload and calls["n"] == before

    # 测后清理：假 chunk 缓存行不得遗留在开发库（会污染真实模式 API 回包）
    _purge_case_rows()


# ---- mock 模式三接口与 B2 等价（前端演示兜底） ----------------------------------

def test_lecture_mock_mode_unchanged(client, db, learner_headers):
    """mock：三档讲义契约与 B2 逐字一致（确定性 markdown + 占位 sources/rate）。

    B10 起 /workflow/execute 会把工作流产物覆盖写入 kind=lecture 缓存（mock 下
    markdown 与 B2 逐字一致，但 sources 取真实检索映射、并带 workflowId）。本用例
    断言的是「直出生成」契约，故先清掉 nn 三档讲义缓存，确保走确定性新生成。
    """
    from app.core import lecture_media
    from app.core.llm import (
        _LECTURE_HALLUCINATION_RATE,
        _LECTURE_SOURCES,
        LLMClient,
        get_llm,
    )
    from app.models.entities import ResourceCache

    db.query(ResourceCache).filter(
        ResourceCache.kp_id == "nn", ResourceCache.kind == "lecture"
    ).delete()
    db.commit()

    for difficulty in ("入门", "初级", "高级"):
        res = client.post(
            "/api/v1/resource/lecture",
            headers=learner_headers,
            json={"kpId": "nn", "difficulty": difficulty},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["code"] == 0
        data = body["data"]
        assert data["sources"] == _LECTURE_SOURCES
        assert data["hallucinationRate"] == _LECTURE_HALLUCINATION_RATE
        # markdown = B2 确定性产出 + 图文增强（确定性：嵌入 mermaid 图解 + mock 占位配图）
        desc = _kp_description(client, learner_headers)
        core = LLMClient._lecture_markdown("神经网络基础", difficulty, desc)
        expected = lecture_media.enrich_lecture(
            core, kp_id="nn", kp_name="神经网络基础", description=desc, llm=get_llm()
        )
        assert data["markdown"] == expected
        # 图文并茂：结构图解（mermaid）+ mock 确定性占位配图（不发真实搜索）
        assert "```mermaid" in data["markdown"]
        assert "data:image/svg+xml" in data["markdown"]


def _kp_description(client, headers) -> str:
    meta = client.get(
        "/api/v1/resource/knowledge-point/nn", headers=headers
    ).json()["data"]
    return meta["description"]


def test_parse_mock_mode_unchanged(client, learner_headers):
    """mock：parse 无材料 → source=manual 基线，不编造（与 B2 等价）。"""
    res = client.post("/api/v1/profile/parse", headers=learner_headers)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["education"] == {"value": "硕士", "source": "manual"}
    assert [s["level"] for s in data["skills"]] == [85, 72, 68, 45, 30, 20]
    assert all(s["source"] == "manual" for s in data["skills"])


# ---- 错误映射：LLM 失败 → code 2001 / HTTP 500 ---------------------------------

def test_api_maps_llm_failure_to_2001(client, learner_headers, deepseek_llm, monkeypatch):
    from app.core import llm_deepseek
    from app.core.llm import LLMGenerationError

    def _boom(prompt, system=None):
        raise LLMGenerationError("上游超时")

    monkeypatch.setattr(llm_deepseek, "chat", _boom)
    res = client.post(
        "/api/v1/profile/narrative",
        headers=learner_headers,
        json={
            "draft": {
                "education": {"value": "硕士", "source": "resume"},
                "major": {"value": "人工智能", "source": "resume"},
                "goal": {"value": "职业培训", "source": "text"},
                "skills": [],
            },
            "materials": [{"id": "m1", "label": "我的简历.pdf", "kind": "doc"}],
            "targetJob": None,
        },
    )
    assert res.status_code == 500
    body = res.json()
    assert body["code"] == 2001
    assert "traceId" in body


# ── 讲义配图关键词「候选阶梯」单测（缓存重生成修复：纯函数、零网络） ────────────────
# 修复点：真实模式配图原直接用完整知识点名（「神经网络基础」「CNN架构」），在 Wikimedia
# Commons File 命名空间常 0 命中 → 几乎只剩 mermaid 兜底。改为「完整名→去后缀核心→标准
# 英文术语」逐级回落（先具体后宽泛、命中即停、保证贴题），把 6 个核心 KP 命中率 1/6→6/6。
def test_image_query_candidates_domain_qualified_ladder():
    from app.core.lecture_media import _image_query_candidates, _relevance_spec

    # 命中领域规格 → 域限定英文查询优先（最可靠贴题），其后附完整名 / 去后缀核心兜底。
    nn_spec = _relevance_spec("神经网络基础")
    nn = _image_query_candidates("神经网络基础", nn_spec)
    assert nn[:3] == [
        "Artificial neural network diagram",
        "Artificial neural network",
        "Multilayer perceptron",
    ]
    assert nn[-2:] == ["神经网络基础", "神经网络"]  # 完整名 + 去「基础」后缀核心兜底

    # 去重保序：机器学习核心与完整名相同时不重复
    ml = _image_query_candidates("机器学习", _relevance_spec("机器学习"))
    assert ml[0] == "Machine learning" and ml[-1] == "机器学习" and ml.count("机器学习") == 1

    # CNN/Transformer 大小写无关命中规格，查询均带领域限定（不再用裸歧义词）
    assert _relevance_spec("CNN架构") is not None
    assert _relevance_spec("Transformer架构") is not None
    assert "Transformer deep learning architecture" in _image_query_candidates(
        "Transformer架构", _relevance_spec("Transformer架构")
    )

    # 未命中规格 → 仅完整名 + 去后缀核心
    misc = _image_query_candidates("区块链原理", _relevance_spec("区块链原理"))
    assert misc == ["区块链原理", "区块链"]

    # 空名 → 空候选（上层据此不插真实图）
    assert _image_query_candidates("", _relevance_spec("")) == []
    assert _image_query_candidates("   ", _relevance_spec("   ")) == []


def test_image_relevance_gate_rejects_cross_domain():
    """贴题判定（任务②核心）：歧义跨域图被拒、确定贴题图被收，未知概念仅挡最明确噪声。"""
    from app.core.lecture_media import _is_on_topic, _relevance_spec

    tf = _relevance_spec("Transformer架构")
    # 电力变压器 / 变形金刚电影 → 跨域排除词命中 → 判不贴题（不插）
    assert _is_on_topic({"title": "Power supply transformer 2098", "description": "electric"}, tf) is False
    assert _is_on_topic({"title": "Transformers One at Lalaport Kadoma", "description": "movie"}, tf) is False
    # 标准架构图 → 正向词命中且无排除词 → 确定贴题（插）
    assert _is_on_topic({"title": "The-Transformer-model-architecture.png", "description": ""}, tf) is True

    cnn = _relevance_spec("CNN架构")
    assert _is_on_topic({"title": "CNN cable news anchor", "description": "television"}, cnn) is False
    assert _is_on_topic({"title": "Typical Convolutional neural network.png", "description": ""}, cnn) is True

    ft = _relevance_spec("大模型微调技术")
    assert _is_on_topic({"title": "Guitar tuning pegs", "description": "instrument"}, ft) is False
    assert _is_on_topic({"title": "LoRA fine-tuning of a language model", "description": ""}, ft) is True

    # 无正向词命中（描述空泛）也判不贴题 → 宁缺毋滥不插
    assert _is_on_topic({"title": "IMG_2098.jpg", "description": ""}, tf) is False

    # 未命中规格（spec=None）→ 仅挡最明确全局噪声，其余放行（保留非核心知识点配图能力）
    assert _is_on_topic({"title": "Some chart", "description": "data"}, None) is True
    assert _is_on_topic({"title": "Optimus Prime autobot", "description": ""}, None) is False
