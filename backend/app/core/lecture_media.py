"""讲义图文增强（图解优先 + 真实图片补充，防幻觉 / mock 占位）。

把「纯文字讲义」加工成「图文并茂」，双来源 + 兜底，**禁止 AI 生成图片**：

1. **图解优先（最稳）**：结构 / 流程类小节复用既有 Mermaid 图解能力
   （``LLMClient.generate_diagram``，mock 确定性、离线可跑），以 ```mermaid 围栏嵌入
   「核心原理」小节末尾，前端 MarkdownRenderer 识别 ```mermaid 走 MermaidDiagram 渲染。
2. **真实图片补充**：适合配真实图处用可插拔的**图片搜索 Provider**
   （``app/services/image_search.py``，默认 Wikimedia Commons 免版权图源，预留 Pexels 兜底）。
   之前走 Tavily 图片搜索，返回的多是带防盗链 / 会过期的 CDN 链接（byteimg），页面加载被拒、
   几乎总是「图片暂不可用」；改用免版权、URL 稳定（``upload.wikimedia.org``，不防盗链）、
   可标注来源的图源。**URL 一律取自搜索结果**（沿用「URL 只取真实候选、防幻觉」原则，绝不让
   LLM 编造图片链接），并标注来源（Wikimedia 文件页链接）。无合适图 → 不插（宁缺毋滥）。
3. **mock 模式不发真实搜索**：用确定性 base64 内联 SVG 占位图（自包含、永不 404、无网络），
   既演示前端图片渲染 + 来源标注 + 裂图兜底，又满足「mock 占位、不发真实请求」。

接口签名不变：增强只作用于 ``/resource/lecture`` 返回的 ``markdown`` 字段内容
（仍是一段 Markdown 字符串），不新增字段、不改路径。幂等：已增强过的 markdown 直接返回。
"""
from __future__ import annotations

import base64
import logging
import re
from typing import Any

from app.core.config import settings

logger = logging.getLogger("app.core.lecture_media")

# 已增强标记（幂等：避免缓存回写 / 二次调用重复插图）
_ENRICHED_MARK = "<!-- media:enriched -->"

# 章节定位（compose_lecture 产出 `## 二、核心原理` / `## 一、概念引入…`；真实模式 LLM
# 产出结构相近，匹配不到则各自走兜底，不强插、不报错）。
_PRINCIPLE_RE = re.compile(r"^#{2,3}\s*.*(原理|机制|结构)")
_INTRO_RE = re.compile(r"^#{2,3}\s*.*(引入|概念|背景|是什么|简介)")
_HEADING_RE = re.compile(r"^#{1,6}\s")


def enrich_lecture(
    markdown: str,
    *,
    kp_id: str,
    kp_name: str,
    description: str,
    llm: Any,
) -> str:
    """为讲义 Markdown 注入图解（图解优先）与一张配图（真实图 / mock 占位）。

    Args:
        markdown: 原始讲义正文（compose_lecture / 工作流产出）。
        kp_id/kp_name/description: 知识点上下文（图解模板按 kp_id 命中、配图按主题搜索）。
        llm: 当前 LLMClient（``is_mock`` 决定走占位还是真实搜索；图解复用其 generate_diagram）。

    Returns:
        增强后的 Markdown；无可插内容时原样返回。已增强（含标记）→ 直接返回（幂等）。
    """
    if not markdown or not markdown.strip():
        return markdown
    if _ENRICHED_MARK in markdown:
        return markdown  # 幂等：已增强过不重复插图

    md = markdown

    # —— 图解优先：嵌入 Mermaid 结构 / 流程示意 ——
    diagram = _diagram_block(kp_id, kp_name, description, llm)
    if diagram:
        md, ok = _insert_after_section(md, _PRINCIPLE_RE, diagram)
        if not ok:  # 未匹配到原理小节 → 末尾追加独立「结构图解」节（保证图解可见）
            md = md.rstrip() + "\n\n## 结构图解\n\n" + diagram

    # —— 真实图片补充 / mock 占位：放在「概念引入」小节末尾 ——
    image = _image_block(kp_name, description, llm)
    if image:
        md, ok = _insert_after_section(md, _INTRO_RE, image)
        # 匹配不到引入小节时不强插（宁缺毋滥；图解已提供视觉信息）

    # 标记置于**末尾**：HTML 注释不渲染，且不影响正文以 `# 标题` 开头的契约（startswith "# "）
    return f"{md}\n\n{_ENRICHED_MARK}" if md != markdown else markdown


# --------------------------------------------------------------------------- #
# 图解块（复用既有 Mermaid 图解能力，离线可跑）
# --------------------------------------------------------------------------- #
def _diagram_block(kp_id: str, kp_name: str, description: str, llm: Any) -> str:
    """复用 ``LLMClient.generate_diagram`` 取 Mermaid 源码，包成讲义内嵌图解块。"""
    try:
        mermaid = str((llm.generate_diagram(kp_id, kp_name, description) or {}).get("mermaid") or "")
    except Exception as exc:  # noqa: BLE001 图解失败不致命，讲义照常返回
        logger.warning("讲义内嵌图解生成失败，跳过图解：%s", exc)
        return ""
    mermaid = mermaid.strip()
    if not mermaid:
        return ""
    caption = f"*图：「{kp_name}」核心结构 / 流程示意（系统生成图解）*"
    return f"```mermaid\n{mermaid}\n```\n\n{caption}"


# --------------------------------------------------------------------------- #
# 配图块（mock 确定性占位 / 真实免版权图源，URL 防幻觉）
# --------------------------------------------------------------------------- #
def _image_block(kp_name: str, description: str, llm: Any) -> str | None:
    """生成一张配图的 Markdown（``![alt](url)`` + 来源标注）。无合适图 → None。"""
    if getattr(llm, "is_mock", False):
        # mock：确定性内联 SVG 占位，自包含、不发任何真实搜索请求
        url = _placeholder_data_uri(kp_name)
        alt = _md_escape(f"{kp_name} 示意图")
        return f"![{alt}]({url})\n\n*示意图（mock 占位，未发起真实搜索）*"

    # 真实模式：免版权图源搜索（默认 Wikimedia Commons），URL 只取自搜索结果（防幻觉），并标注来源
    hit = _search_real_image(kp_name, description)
    if not hit:
        return None  # 无真实可用图 → 不强插（宁缺毋滥）
    url = hit["url"]
    source = hit.get("source") or _domain(url)
    # 来源标注优先指向图源「来源页」（如 Wikimedia 文件页），回落图片直链
    source_url = hit.get("source_url") or url
    # 第三方图片描述文本同样过内容安全（不绕过）：核心讲义/图解已在 LLMClient 层被 guard 包裹，
    # 此处的 alt 来自外部搜索结果，故显式再过一次 guard。
    from app.core import content_safety

    alt_raw = hit.get("description") or hit.get("title") or f"{kp_name} 配图"
    alt = _md_escape(content_safety.guard(alt_raw, where="lecture_image_alt"))
    license_name = (hit.get("license") or "").strip()
    license_suffix = f"（{_md_escape(license_name)}）" if license_name else ""
    return f"![{alt}]({url})\n\n*图片来源：[{_md_escape(source)}]({source_url}){license_suffix}*"


# 知识点名 → 配图检索「候选阶梯」+「贴题判定」。
# 任务②教训：之前直接用完整中文名 / 裸英文词检索，且对返回结果**不做相关性判定**，
# 导致歧义跨域图被插入——「Transformer」→ 电力变压器 / 变形金刚电影，「CNN」→ 有线新闻台，
# 「fine-tuning」→ 乐器调音。改法：
#   ① 检索词一律带领域限定（deep learning / neural network / machine learning），不再用裸歧义词；
#   ② 对命中图做「确定贴题」判定——其标题/描述须含该概念的**正向词**、且不含**跨域排除词**，
#      满足才插；任一候选都判不出贴题图 → 不插（宁缺毋滥）。正/负词大小写无关地匹配 标题+描述。
# 命中即停（每一级都是真实搜索结果，绝不编造 URL）。
_QUERY_SUFFIXES = ("基础", "架构", "原理", "技术", "入门", "进阶", "详解", "讲义", "简介", "概述")

# 每条：(命中知识点名的子串 needles, 域限定查询阶梯 queries, 贴题正向词 positive, 跨域排除词 negative)。
# 子串/正/负词匹配均转小写后做 in 判断（子串匹配，故 "fine-tun" 同时覆盖 fine-tuning/fine-tune）。
_DOMAIN_RELEVANCE: tuple[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("transformer", "注意力", "attention", "自注意力"),
        ("Transformer deep learning architecture", "Transformer neural network model",
         "Self-attention mechanism diagram"),
        ("architecture", "attention", "encoder", "decoder", "neural", "deep learning",
         "machine learning", "self-attention", "model", "embedding", "nlp"),
        ("electric", "power", "voltage", "substation", "grid", "transmission", "winding",
         "autobot", "decepticon", "optimus", "movie", "film", "toy", "robot", "lalaport"),
    ),
    (
        ("cnn", "卷积", "convolution"),
        ("Convolutional neural network", "CNN deep learning architecture",
         "Convolutional neural network diagram"),
        ("convolution", "convolutional", "cnn", "neural", "feature map", "pooling",
         "kernel", "deep learning", "image"),
        ("cable news", "news network", "television", "broadcast", "anchor", "channel", "logo"),
    ),
    (
        ("神经网络", "neural network", "perceptron", "感知机"),
        ("Artificial neural network diagram", "Artificial neural network", "Multilayer perceptron"),
        ("neural", "perceptron", "neuron", "network", "layer", "activation", "deep learning"),
        ("social network", "road network", "power network", "computer network", "telecom"),
    ),
    (
        ("深度学习", "deep learning"),
        ("Deep learning", "Deep neural network diagram"),
        ("deep learning", "deep neural", "neural", "network", "layer"),
        (),
    ),
    (
        ("机器学习", "machine learning"),
        ("Machine learning", "Supervised learning diagram", "Machine learning model"),
        ("machine learning", "learning", "model", "classifier", "regression", "training",
         "dataset", "algorithm"),
        (),
    ),
    (
        ("微调", "fine-tun", "finetune", "lora", "迁移学习", "transfer learning"),
        # 迁移学习概念图优先（Commons 上有清晰的 Transfer Learning 谱系/概念图）；
        # 「Fine-tuning …」检索回的多是论文结果图（无干净示意图），故置后兜底。
        ("Transfer learning diagram", "Fine-tuning deep learning", "LoRA low-rank adaptation"),
        ("fine-tun", "transfer learning", "lora", "pretrain", "pre-train", "language model",
         "neural", "adaptation", "peft", "machine learning"),
        ("guitar", "violin", "piano", "instrument", "music", "tuning fork", "engine",
         "car ", "radio", "antenna"),
    ),
    (
        ("梯度下降", "gradient descent", "梯度"),
        ("Gradient descent", "Gradient descent optimization"),
        ("gradient", "descent", "optimization", "loss", "minimum", "convex"),
        (),
    ),
    # —— GEN 生成式扩散板块（配图为辅、图解为主）：注意顺序——更具体的条目在前，
    # 通配「扩散」在最后，避免「潜在扩散 LDM / Stable Diffusion」落到泛化条目。
    # 「diffusion」歧义极强（物理扩散/渗透/创新扩散理论），负词从严（宁缺毋滥）。
    (
        ("controlnet",),
        ("ControlNet Stable Diffusion", "ControlNet neural network architecture"),
        ("controlnet", "stable diffusion", "diffusion", "neural", "generative", "image"),
        (),
    ),
    (
        ("stable diffusion", "文生图", "潜在扩散", "ldm"),
        ("Stable Diffusion architecture diagram", "Latent diffusion model",
         "Stable Diffusion generated"),
        ("stable diffusion", "latent diffusion", "diffusion model", "text-to-image",
         "generative", "denois", "neural"),
        ("osmosis", "molecul", "gas diffusion", "innovation", "heat", "membrane"),
    ),
    (
        ("u-net", "unet", "去噪网络"),
        ("U-Net architecture", "U-Net convolutional neural network"),
        ("u-net", "unet", "convolution", "segmentation", "architecture", "neural",
         "encoder", "decoder"),
        (),
    ),
    (
        ("vae", "变分自编码", "autoencoder", "自编码"),
        ("Variational autoencoder diagram", "Autoencoder structure neural network"),
        ("autoencoder", "variational", "latent", "neural", "encoder", "decoder",
         "reconstruction"),
        (),
    ),
    (
        ("gan", "生成对抗", "adversarial"),
        ("Generative adversarial network diagram", "GAN machine learning architecture"),
        ("adversarial", "gan", "generator", "discriminator", "neural", "generative",
         "deep learning"),
        (),
    ),
    (
        ("生成式模型", "生成模型", "generative model"),
        ("Generative model machine learning", "Generative artificial intelligence diagram"),
        ("generative", "machine learning", "neural", "gan", "diffusion", "autoencoder",
         "model"),
        (),
    ),
    (
        ("扩散", "diffusion", "ddpm", "去噪"),
        ("Denoising diffusion probabilistic model", "Diffusion model machine learning",
         "Diffusion model generative AI"),
        ("diffusion model", "denois", "ddpm", "generative", "machine learning",
         "neural", "latent", "stable diffusion"),
        ("osmosis", "brownian", "molecul", "gas", "heat", "innovation", "chemical",
         "membrane", "physics", "cell", "solvent"),
    ),
)

# 未命中领域规格时的最小跨域噪声排除（仅挡最明确的无关图，避免过度抑制）。
_GLOBAL_OFFTOPIC = ("autobot", "decepticon", "optimus", "lalaport", "movie poster")

# 非示意性噪声（即便含正向词也判不贴题）：Commons 上常见的玩梗/周边/极小众应用图——
# 如「Machine Learning cookies.jpg」「fine-tuning of AI drone racing」。讲义图解已是专业主力，
# 这类图既不专业也易误导，一律不插（宁缺毋滥），让候选回落到真正的标准示意图或不插。
_NOISE_OFFTOPIC = (
    "cookie", "latte", "coffee", "mug", "t-shirt", "tshirt", "sticker", "meme",
    "cartoon", "drone racing", "keychain", "plush", "logo", "icon",
)


def _relevance_spec(kp_name: str):
    """按子串命中知识点名 → 该领域 (needles, queries, positive, negative) 规格；未命中 → None。"""
    low = (kp_name or "").lower()
    for spec in _DOMAIN_RELEVANCE:
        if any(n.lower() in low for n in spec[0]):
            return spec
    return None


def _image_query_candidates(kp_name: str, spec) -> list[str]:
    """配图检索候选阶梯（去重保序）。

    命中领域规格 → 域限定英文查询优先（最可靠、最贴题），其后附完整名 / 去后缀核心兜底；
    未命中规格 → 仅用完整名 + 去教学性后缀的核心概念。
    """
    name = (kp_name or "").strip()
    candidates: list[str] = []

    def _add(q: str) -> None:
        q = (q or "").strip()
        if q and q not in candidates:
            candidates.append(q)

    if spec is not None:
        for q in spec[1]:  # 域限定查询优先
            _add(q)
    _add(name)
    core = name
    for suf in _QUERY_SUFFIXES:
        if core.endswith(suf) and len(core) > len(suf):
            core = core[: -len(suf)]
    _add(core)
    return candidates


def _is_on_topic(hit: dict[str, Any], spec) -> bool:
    """判定一张命中图是否「确定贴题」。

    命中领域规格：标题+描述须含至少一个正向词、且不含任何跨域排除词；
    未命中规格：仅挡最明确的全局跨域噪声（宽松，保留非核心知识点的配图能力）。
    """
    text = f"{hit.get('title') or ''} {hit.get('description') or ''}".lower()
    if spec is None:
        return not any(bad in text for bad in _GLOBAL_OFFTOPIC)
    _needles, _queries, positive, negative = spec
    if any(neg in text for neg in negative) or any(n in text for n in _NOISE_OFFTOPIC):
        return False
    return any(pos in text for pos in positive)


def _search_real_image(kp_name: str, description: str) -> dict[str, Any] | None:
    """经可插拔图片搜索 Provider 取一张**确定贴题**的真实配图；URL 必须为搜索返回的真实 http(s) 链接。

    URL 一律取自图源结果（防幻觉，绝不由 LLM 编造）；返回 ``source`` / ``source_url`` 用于来源标注。
    按域限定查询阶梯逐级检索，对每张命中图做贴题判定（正向词命中且无跨域排除词），命中即停；
    所有候选都判不出贴题图 → 返回 None（宁缺毋滥，上层只用图解，不插不相关真实图）。
    """
    from app.services import image_search

    provider = image_search.get_provider()
    if not getattr(provider, "online", False):
        return None  # 无图源能力 → 不插真实图
    spec = _relevance_spec(kp_name)
    candidates = _image_query_candidates(kp_name, spec)
    if not candidates:
        return None
    for query in candidates:
        try:
            hits = provider.search_images(query, max_results=settings.image_search_max_results)
        except Exception as exc:  # noqa: BLE001 搜索失败不致命 → 试下一个候选 / 不插图
            logger.warning("讲义配图搜索失败（query=%s），跳过该候选：%s", query, exc)
            continue
        for h in hits or []:
            url = str((h or {}).get("url") or "").strip()
            # 防幻觉二次校验：仅接受真实 http(s) URL（链接来自 Provider，非 LLM 编造）
            if not (url.startswith("http://") or url.startswith("https://")):
                continue
            # 贴题判定：不确定贴题就跳过（宁缺毋滥），避免插入歧义跨域图（电力变压器/新闻台等）
            if not _is_on_topic(h or {}, spec):
                logger.info("讲义配图：命中图判为不贴题，跳过（query=%s，title=%s）",
                            query, (h or {}).get("title"))
                continue
            if query != candidates[0]:
                logger.info("讲义配图：首选查询未得贴题图，回落候选「%s」命中", query)
            return {
                "url": url,
                "source": (h or {}).get("source") or _domain(url),
                "source_url": (h or {}).get("source_url") or "",
                "title": (h or {}).get("title") or "",
                "description": (h or {}).get("description") or "",
                "license": (h or {}).get("license") or "",
            }
    return None


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _insert_after_section(markdown: str, heading_re: re.Pattern[str], block: str) -> tuple[str, bool]:
    """把 block 插到 heading_re 命中小节的末尾（下一个标题前）。未命中 → 原样 + False。"""
    lines = markdown.split("\n")
    start = next((i for i, ln in enumerate(lines) if heading_re.match(ln)), None)
    if start is None:
        return markdown, False
    end = next((j for j in range(start + 1, len(lines)) if _HEADING_RE.match(lines[j])), len(lines))
    new_lines = lines[:end] + ["", block, ""] + lines[end:]
    return "\n".join(new_lines), True


def _placeholder_data_uri(kp_name: str) -> str:
    """确定性内联 SVG 占位图（base64 data URI，自包含、离线、无网络）。"""
    label = _xml_escape((kp_name or "知识点").strip()[:16])
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='720' height='360' viewBox='0 0 720 360'>"
        "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
        "<stop offset='0' stop-color='#eff6ff'/><stop offset='1' stop-color='#dbeafe'/>"
        "</linearGradient></defs>"
        "<rect width='720' height='360' rx='18' fill='url(#g)'/>"
        "<rect x='12' y='12' width='696' height='336' rx='14' fill='none' "
        "stroke='#93c5fd' stroke-width='2' stroke-dasharray='8 6'/>"
        "<circle cx='360' cy='150' r='46' fill='none' stroke='#2563eb' stroke-width='4'/>"
        "<path d='M338 150 h44 M360 128 v44' stroke='#2563eb' stroke-width='4' stroke-linecap='round'/>"
        f"<text x='360' y='250' text-anchor='middle' font-family='sans-serif' "
        f"font-size='30' font-weight='700' fill='#1e3a8a'>{label}</text>"
        "<text x='360' y='292' text-anchor='middle' font-family='sans-serif' "
        "font-size='18' fill='#3b82f6'>示意图 · 占位</text>"
        "</svg>"
    )
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def _domain(url: str) -> str:
    """从 URL 提取来源域名（展示用）。"""
    u = (url or "").split("//", 1)[-1]
    return u.split("/", 1)[0] or "web"


def _md_escape(text: str) -> str:
    """转义 Markdown 图片 alt 文本里会破坏语法的字符。"""
    return str(text or "").replace("]", "）").replace("[", "（").replace("\n", " ").strip()


def _xml_escape(text: str) -> str:
    """转义内联 SVG 文本中的 XML 特殊字符。"""
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
