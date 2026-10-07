"""内容安全过滤层（生成内容钝化点）。

定位：本平台所有面向学习者的「LLM 生成文本」在返回前必须经此过滤一道。
与 critic（事实接地/幻觉校验）**职责正交**——critic 管「有没有编造」，
本模块管「有没有违规」（政治红线 / 色情低俗 / 暴恐违法 / 辱骂歧视）。

中心钝化点设计：唯一入口 `LLMClient`（app/core/llm.py）的全部公共生成方法，
由本模块 `install_llm_client_guard` 用 `guarded` / `guarded_stream` 统一包裹返回值，避免各接口分散
重复（讲义/对话/费曼/苏格拉底/线索/图解/视频/路径理由/画像叙述/错题强化全覆盖）。

判定策略（**宁可漏判边缘也不错杀正常讲义**，教育场景是第一约束）：
- 明确违规词（色情/辱骂歧视/已定性的政治红线词）→ 子串精确匹配；
- 双用途词（攻击/漏洞/病毒/战争/炸/毒/枪 等学术常见词）**绝不**按裸词命中，
  只在「有害意图动词 + 有害对象」共现的指令型语境下命中（如「如何制造炸弹」），
  「网络攻击防御」「对抗攻击」「二战战争史」「计算机病毒」等学术语境照常放行；
- 白名单二次兜底：命中片段若落在白名单短语内则撤销（再降误伤）。

动作：
- block 类（政治/色情/暴恐/违法）命中 → 整段文本字段替换为降级提示（不让原文外泄）；
- mask 类（辱骂歧视）命中 → 仅对命中片段做掩码（`＊`），其余正常返回；
- 命中均记审计日志（手机号/邮箱已由 logging.PiiMaskingFilter 脱敏）。

provider 双模：词表过滤 provider 无关，mock / 无 Key 可跑通；可选的模型级二次校验
（settings.content_safety_model_check=True 且真实 provider）走 llm_deepseek 轻量分类，
失败/超时一律回落词表结论，绝不因二次校验失败而漏过或误杀。
"""
from __future__ import annotations

import functools
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.config import settings

logger = logging.getLogger("app.core.content_safety")

# ---- 动作与类别 --------------------------------------------------------------
ACTION_ALLOW = "allow"
ACTION_MASK = "mask"
ACTION_BLOCK = "block"

# 类别 → 处置动作。block：整段拦截降级；mask：片段掩码。
CATEGORY_ACTIONS: dict[str, str] = {
    "political": ACTION_BLOCK,        # 政治敏感红线
    "porn": ACTION_BLOCK,             # 色情低俗
    "violence_terror": ACTION_BLOCK,  # 暴恐
    "illegal_harmful": ACTION_BLOCK,  # 违法有害指导
    "abuse_discrimination": ACTION_MASK,  # 辱骂歧视
}

# 命中 block 时对外的统一降级提示（套统一信封的 data 内，前端拿不到违规原文）
DEGRADED_NOTICE = "【内容安全提示】抱歉，这部分生成内容包含不适宜信息，已被安全策略拦截。请调整问题或换个角度再试。"

# 掩码字符（全角星号，避免与 markdown `*` 强调语法相互干扰）
_MASK_CHAR = "＊"

# ---- 词表（子串精确匹配，均为基本无歧义的明确违规词；可经词库文件扩充） ----------
# 说明：这是「种子词表」，可经 settings.content_safety_lexicon_path 追加（每行 `类别:词`）。
# 仅收录基本无语境歧义的词；双用途/学术词一律不进此表，改由下方共现规则处理。
_BUILTIN_TERMS: dict[str, list[str]] = {
    "porn": [
        "做爱", "性交", "口交", "肛交", "裸聊", "黄片", "av女优", "成人电影",
        "情色小说", "性服务", "招嫖", "约炮", "一夜情交易",
    ],
    "abuse_discrimination": [
        "傻逼", "傻屄", "贱人", "废物东西", "去死吧", "你妈死了", "操你妈",
        "智障", "脑残", "支那", "黑鬼", "死胖子", "乡巴佬",
    ],
    "political": [
        "法轮功", "颠覆国家政权", "分裂国家", "暴动推翻",
    ],
}

# ---- 共现规则（双用途词的指令型语境才命中，避免误伤学术内容） --------------------
# 每条：(category, 正则)。正则要求「有害意图动词」与「有害对象」邻近共现。
_CONTEXT_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "illegal_harmful",
        re.compile(
            r"(制造|制作|自制|合成|配制|怎么(做|制|造)|如何(制造|制作|合成|获取)|教程|配方|步骤)"
            r"[^。！？\n]{0,12}"
            r"(炸弹|炸药|爆炸物|tnt|雷管|雷汞|冰毒|海洛因|摇头丸|甲基苯丙胺|制毒|毒品|枪支|枪械|改装枪)",
            re.I,
        ),
    ),
    (
        "violence_terror",
        re.compile(
            r"(煽动|组织|策划|发动|实施)[^。！？\n]{0,10}(恐怖袭击|暴恐|暴力袭击|爆炸袭击|滥杀)",
        ),
    ),
    (
        "political",
        re.compile(r"(推翻|颠覆|武装夺取)[^。！？\n]{0,6}(政府|国家|政权|党)"),
    ),
]

# ---- 白名单：命中片段若落在以下学术短语内则撤销命中（教育场景降误伤的兜底） --------
_ACADEMIC_WHITELIST: tuple[str, ...] = (
    "网络攻击", "攻击者", "攻击面", "攻击向量", "对抗攻击", "对抗样本", "ddos",
    "sql注入", "缓冲区溢出", "渗透测试", "漏洞挖掘", "漏洞修复", "安全漏洞",
    "计算机病毒", "病毒检测", "杀毒", "免疫", "战争史", "二战", "一战",
    "世界大战", "战争与和平", "枪械原理", "梯度", "战斗", "攻防演练",
    "数据投毒", "投毒攻击",
)

# ---- 教育性「提及/拒答/防御/史论」语境标记（共现规则的上下文豁免，治本而非枚举词） -----
# 设计：本模块只处置「平台自身基于 RAG 生成的教育文本」（绝不流经用户输入，
# install_llm_client_guard 仅包裹 LLMClient 生成方法）。对这类可信来源的教学内容，双用途
# 共现命中若所在句子带有「拒答/防御/识别/举例/史论」等框架，说明危险短语是被「讨论/引用为
# 应拒绝的对象」而非「被指导实施」——按上下文豁免，而不是去枚举无穷的学术词。
# 真实有害指导（如「教你如何制造炸弹，材料如下」）不含这类框架 → 仍被拦截。
_EDU_CONTEXT_MARKER = re.compile(
    # 拒答 / 防御 / 安全治理
    r"拒绝|拒答|不予(回答|响应)|不应|不得|禁止|防范|防御|防止|杜绝|识别|检测|过滤|审核|屏蔽|"
    r"安全对齐|对齐|越狱|红队|诱导模型|安全微调|有害(请求|内容|指令)|违法(请求|行为)|"
    r"违规(请求|内容|行为)|不良信息|敏感(内容|信息)|风险内容|"
    # 提及 / 举例
    r"例如|比如|譬如|这类|此类|之类|等违法|等违规|等不良|等危害|视为违|属于违|"
    # 史论 / 教学分析
    r"历史|史上|革命|战争史|起义|案例|分析|研究|教学|讲解|介绍"
)


# 真实「操作配方/步骤」签名：教育性拒答讨论几乎不会出现，用于反制「拒答词包装真实指导」的逃逸。
# 仅在共现已命中时参与判定（无害句不触发），故不会误伤正常「微调步骤如下」类教学。
_RECIPE_SIGNATURE = re.compile(
    r"材料如下|所需材料|原料(是|为|如下)|配方(为|是|如下)|配料|"
    r"第[一二三四五六七八九十1-9]步|步骤(如下|为|是)|按(以下|如下)步骤|具体(做法|步骤)|操作方法|"
    r"取[^。！？\n]{0,6}(克|毫升|份)"
)


def _is_educational_mention(text: str, start: int, end: int) -> bool:
    """共现命中 [start,end) 所在句子是否为教育性「提及/拒答/防御/史论」语境（应豁免）。

    句界以 。！？\\n 及文首文尾界定（与共现规则的 [^。！？\\n] 窗口一致），
    命中短语本身从检测区间剔除，避免「制造炸弹」里的字反向被当作框架。
    若同句出现真实「操作配方/步骤」签名，则视为真实指导、不予豁免（防止逃逸）。
    """
    seps = "。！？\n"
    left = start
    while left > 0 and text[left - 1] not in seps:
        left -= 1
    right = end
    while right < len(text) and text[right] not in seps:
        right += 1
    sentence = text[left:start] + text[end:right]  # 去掉命中短语自身
    if _RECIPE_SIGNATURE.search(sentence):
        return False
    return _EDU_CONTEXT_MARKER.search(sentence) is not None


@dataclass
class Hit:
    category: str
    action: str
    term: str
    start: int
    end: int


@dataclass
class Verdict:
    """单段文本的安全判定结果。"""

    safe: bool = True
    action: str = ACTION_ALLOW  # 该段的最强动作（block > mask > allow）
    hits: list[Hit] = field(default_factory=list)
    categories: set[str] = field(default_factory=set)


# ---- 词表加载（builtin + 可选词库文件） --------------------------------------
def _load_terms() -> dict[str, list[str]]:
    terms: dict[str, list[str]] = {k: list(v) for k, v in _BUILTIN_TERMS.items()}
    path = getattr(settings, "content_safety_lexicon_path", "") or ""
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    cat, _, word = line.partition(":")
                    cat, word = cat.strip(), word.strip()
                    if word and cat in CATEGORY_ACTIONS:
                        terms.setdefault(cat, []).append(word)
            logger.info("内容安全词库已加载扩充：%s", path)
        except OSError as exc:  # 文件不可读 → 仅用 builtin，不影响可用性
            logger.warning("内容安全词库加载失败（用内置词表）：%s", exc)
    return terms


# 进程内缓存（词表静态；改词库重启生效，与热更新无关）
_TERMS: dict[str, list[str]] = _load_terms()


def _in_whitelist(text_low: str, start: int, end: int) -> bool:
    """命中片段 [start,end) 是否被某个白名单短语覆盖（学术语境豁免）。"""
    for phrase in _ACADEMIC_WHITELIST:
        idx = text_low.find(phrase)
        while idx != -1:
            if idx <= start and end <= idx + len(phrase):
                return True
            idx = text_low.find(phrase, idx + 1)
    return False


def _stronger(a: str, b: str) -> str:
    order = {ACTION_ALLOW: 0, ACTION_MASK: 1, ACTION_BLOCK: 2}
    return a if order[a] >= order[b] else b


def scan(text: str) -> Verdict:
    """扫描单段文本，返回安全判定（不修改文本）。"""
    verdict = Verdict()
    if not text or not isinstance(text, str):
        return verdict
    low = text.lower()

    # 1) 词表子串精确匹配
    for cat, words in _TERMS.items():
        action = CATEGORY_ACTIONS[cat]
        for w in words:
            wl = w.lower()
            pos = low.find(wl)
            while pos != -1:
                end = pos + len(wl)
                if not _in_whitelist(low, pos, end):
                    verdict.hits.append(Hit(cat, action, text[pos:end], pos, end))
                    verdict.categories.add(cat)
                    verdict.action = _stronger(verdict.action, action)
                pos = low.find(wl, pos + 1)

    # 2) 共现规则（双用途词的指令型语境）
    for cat, pat in _CONTEXT_RULES:
        action = CATEGORY_ACTIONS[cat]
        for m in pat.finditer(text):
            if _in_whitelist(low, m.start(), m.end()):
                continue
            # 教育性提及/拒答/防御/史论语境 → 危险短语是被讨论的对象而非被指导，豁免。
            # 仅对共现（双用途）命中放宽，明确违规词表不享此豁免。
            if _is_educational_mention(text, m.start(), m.end()):
                continue
            verdict.hits.append(Hit(cat, action, m.group(0), m.start(), m.end()))
            verdict.categories.add(cat)
            verdict.action = _stronger(verdict.action, action)

    verdict.safe = not verdict.hits
    return verdict


def _mask_spans(text: str, hits: list[Hit]) -> str:
    """对 mask 类命中片段做字符掩码（按区间从后往前替换，保持下标稳定）。"""
    chars = list(text)
    for h in sorted(hits, key=lambda x: x.start, reverse=True):
        for i in range(h.start, min(h.end, len(chars))):
            chars[i] = _MASK_CHAR
    return "".join(chars)


# 句界切分：在 。！？\n 之后切，保留分隔符本身（用于 block 的「逐句降级」兜底）。
_SEG_SPLIT = re.compile(r"(?<=[。！？\n])")


def _granular_block(text: str, verdict: Verdict) -> str:
    """长文本含 block 命中时的兜底：仅把违规句替换为降级提示，保留其余正常内容。

    共现规则的命中窗口 [^。！？\\n]{0,N} 不跨句界，故逐句重扫与整体扫描判定一致；
    单句文本（无可保留正常内容）回落整段降级，违规原文不外泄。
    """
    segments = [s for s in _SEG_SPLIT.split(text) if s]
    if len(segments) <= 1:
        return DEGRADED_NOTICE
    parts: list[str] = []
    for seg in segments:
        sv = scan(seg)
        if any(h.action == ACTION_BLOCK for h in sv.hits):
            if not parts or parts[-1] != DEGRADED_NOTICE:  # 连续违规句合并为一条提示
                parts.append(DEGRADED_NOTICE)
        elif sv.hits:  # 仅 mask 类命中：片段掩码后保留
            parts.append(_mask_spans(seg, [h for h in sv.hits if h.action == ACTION_MASK]))
        else:
            parts.append(seg)
    return "".join(parts)


def sanitize(text: str, *, where: str = "") -> tuple[str, Verdict]:
    """按判定处置单段文本：block→逐句降级（保留正常内容）；mask→片段掩码；安全→原样返回。

    返回 (处置后文本, 判定)。命中即记审计日志（含类别与 where，不含违规原文细节）。
    """
    if not settings.content_safety_enabled:
        return text, Verdict()
    verdict = scan(text)
    if verdict.safe:
        return text, verdict

    # 可选模型级二次校验：仅在词表「未命中 block」且开关开启时用于补漏（此处已命中，跳过）
    if verdict.action == ACTION_BLOCK:
        logger.warning(
            "内容安全拦截[%s] 类别=%s 命中=%d 处 → 逐句降级", where or "?",
            ",".join(sorted(verdict.categories)), len(verdict.hits),
        )
        # 兜底体验：不整篇道歉，仅降级违规句、保留其余正常内容（单句则整段降级）。
        return _granular_block(text, verdict), verdict
    # mask
    masked = _mask_spans(text, [h for h in verdict.hits if h.action == ACTION_MASK])
    logger.info(
        "内容安全脱敏[%s] 类别=%s 命中=%d 处 → 掩码", where or "?",
        ",".join(sorted(verdict.categories)), len(verdict.hits),
    )
    return masked, verdict


# ---- 可选：模型级二次校验（轻量分类，失败回落词表） ---------------------------
def model_double_check(text: str) -> Verdict:
    """用现有真实 provider 做一次轻量违规分类（补词表漏判）。

    仅当 settings.content_safety_model_check 开启且 provider 非 mock 时生效；
    任何异常（无 Key/超时/解析失败）→ 返回 safe 的空判定（绝不因二次校验失败而误杀）。
    """
    if not settings.content_safety_model_check:
        return Verdict()
    try:
        from app.core import llm_transport  # 延迟导入，避免循环依赖 & 无 Key 环境 import 报错；经分发通道随「当前模型」走

        system = (
            "你是中文内容安全审核员。判断给定文本是否包含以下任一明确违规内容："
            "政治敏感红线、色情低俗、暴恐或违法有害指导、辱骂歧视。"
            "注意：讨论『网络攻击』『漏洞』『战争史』『计算机病毒』等学术/教学内容属正常，不算违规。"
            '仅输出 JSON：{"violation": true或false, "category": "political|porn|violence_terror|illegal_harmful|abuse_discrimination|none"}。'
        )
        raw = llm_transport.chat(text[:1000], system=system)
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return Verdict()
        import json

        data = json.loads(m.group(0))
        if data.get("violation") and data.get("category") in CATEGORY_ACTIONS:
            cat = data["category"]
            action = CATEGORY_ACTIONS[cat]
            v = Verdict(safe=False, action=action)
            v.categories.add(cat)
            v.hits.append(Hit(cat, action, "<model>", 0, 0))
            return v
    except Exception as exc:  # noqa: BLE001 二次校验是补充，失败一律回落词表
        logger.debug("内容安全模型二次校验跳过：%s", exc)
    return Verdict()


def _sanitize_with_model(text: str, where: str) -> str:
    """词表处置 + 可选模型补漏。词表已 block 的直接返回；否则尝试模型补判。"""
    out, verdict = sanitize(text, where=where)
    if verdict.action == ACTION_BLOCK or not settings.content_safety_model_check:
        return out
    mv = model_double_check(text)
    if not mv.safe:
        if mv.action == ACTION_BLOCK:
            logger.warning("内容安全模型补判拦截[%s] 类别=%s", where, ",".join(mv.categories))
            return DEGRADED_NOTICE
    return out


# ---- 递归 guard：对结构化生成结果的所有字符串叶子统一处置 ----------------------
def guard(value: Any, *, where: str = "") -> Any:
    """递归处置任意生成结果（dict/list/str），对每个字符串叶子做安全处置。

    - 不扫描 dict 的 key（均为契约英文键名，不含中文违规词，且改动会破坏契约）；
    - 非 str/dict/list/tuple 叶子（数字/布尔/None）原样返回；
    - 返回与入参同形的新结构，字段类型不变 → **不破坏接口契约、不破坏 JSON**。
    """
    if not settings.content_safety_enabled:
        return value
    if isinstance(value, str):
        return _sanitize_with_model(value, where)
    if isinstance(value, dict):
        return {k: guard(v, where=where) for k, v in value.items()}
    if isinstance(value, list):
        return [guard(v, where=where) for v in value]
    if isinstance(value, tuple):
        return tuple(guard(v, where=where) for v in value)
    return value


# ---- 装饰器：统一包裹 LLMClient 生成方法 / 流式方法 ----------------------------
def guarded(fn: Callable[..., Any]) -> Callable[..., Any]:
    """包裹一个返回结构化生成结果的方法：返回值统一过 guard。"""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = fn(*args, **kwargs)
        return guard(result, where=fn.__name__)

    return wrapper


def guarded_stream(fn: Callable[..., Iterator[str]]) -> Callable[..., Iterator[str]]:
    """包裹流式生成方法：对增量做滑窗扫描——安全段即时下发，命中 block 截断降级。"""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Iterator[str]:
        source = fn(*args, **kwargs)
        if not settings.content_safety_enabled:
            yield from source
            return
        yield from _guard_stream(source, where=fn.__name__)

    return wrapper


# LLMClient 中所有面向学习者的生成出口。注册职责归属于内容安全模块，调用方只需
# 在类定义完成后安装一次；方法名保持显式清单，新增生成出口时必须主动纳入审查。
LLM_CLIENT_GUARDED_METHODS: tuple[str, ...] = (
    "extract_profile",
    "generate_narrative",
    "generate_lecture",
    "generate_video_script",
    "generate_diagram",
    "generate_flashcards",
    "generate_doc_quiz",
    "generate_overview",
    "generate_reinforcement",
    "tutor_chat",
    "tutor_suggestions",
    "generate_cornell_cues",
    "feynman_eval",
    "extract_portrait",
    "plan_path",
    "complete",
)


def install_llm_client_guard(client_type: type[Any]) -> None:
    """统一包裹 LLMClient 生成出口，包括增量辅导流。"""
    for name in LLM_CLIENT_GUARDED_METHODS:
        setattr(client_type, name, guarded(getattr(client_type, name)))
    client_type.tutor_chat_stream = guarded_stream(client_type.tutor_chat_stream)


# 滑窗保留长度：≥最长共现规则跨度（规则用 [^。！？\n]{0,12} 限制，实测最长约 22 字）。
# 不变式：仅当某 delta 之后已缓冲 ≥HOLD 个字符、且整段缓冲无命中时，才定稿下发该 delta。
# 由 HOLD ≥ 最长违规跨度可证：若某 delta 是一处违规的起点，其完整违规串必已落入这
# HOLD 字符的前瞻窗内并被 scan 命中 → 不会被定稿下发，故安全内容**逐 delta 原样透传**
# （保持苏格拉底打字机/真实 chunk 透传不回归），仅在确有违规时才改写下发。
_STREAM_HOLD = 24


def _guard_stream(source: Iterator[str], *, where: str) -> Iterator[str]:
    pending: list[str] = []  # 已收、未定稿的原始 delta（保持原切分边界）

    for delta in source:
        pending.append(delta)
        buf = "".join(pending)
        v = scan(buf)
        if any(h.action == ACTION_BLOCK for h in v.hits):
            logger.warning("内容安全拦截[%s/stream] 类别=%s → 截断降级",
                           where, ",".join(sorted(v.categories)))
            yield DEGRADED_NOTICE
            return
        if v.hits:  # 仅 mask 类：整段缓冲掩码后作为一条 delta 下发并清空
            masked = _mask_spans(buf, [h for h in v.hits if h.action == ACTION_MASK])
            logger.info("内容安全脱敏[%s/stream] 命中=%d → 掩码", where, len(v.hits))
            if masked:
                yield masked
            pending = []
            continue
        # 无命中：把「其后已缓冲 ≥HOLD 字符」的前置 delta 原样定稿下发
        while pending:
            tail = sum(len(c) for c in pending[1:])
            if tail < _STREAM_HOLD:
                break
            yield pending.pop(0)

    if pending:  # 流末兜底：剩余缓冲整体判定
        buf = "".join(pending)
        v = scan(buf)
        if any(h.action == ACTION_BLOCK for h in v.hits):
            logger.warning("内容安全拦截[%s/stream] → 截断降级", where)
            yield DEGRADED_NOTICE
        elif v.hits:
            masked = _mask_spans(buf, [h for h in v.hits if h.action == ACTION_MASK])
            if masked:
                yield masked
        else:
            yield from pending
