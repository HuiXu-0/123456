"""生成模型注册表（模型管理：界面切换 LLM 模型 + 接入魔搭 ModelScope + 用户自建配置）。

- 注册表：内置模型（默认 DeepSeek + 魔搭清单，settings 可配）+ **用户自建配置**
  （接口文档 21.3+：界面填 显示名/provider/base_url/model_id/api_key，key 加密落库，
  按 user 隔离）；``settings.llm_provider == "mock"`` 时内置部分仅含 mock
  （无 Key 全链路可跑，纪律不变）。
- 「当前模型」两层：
  1. **进程级运行态**（既有 §21.2 语义，内置模型间切换，重启回默认 DeepSeek）；
  2. **用户级 overlay**（新增）：用户把自建配置设为当前 → 只影响本人后续生成
     （UserModelChoice 落库，重启不丢；A 的 key 绝不会被 B 用到）。
  解析顺序：请求上下文有用户且其 overlay 有效 → 用户自建配置；否则 → 进程级运行态。
- 用户身份经 contextvar 注入（HTTP：security.UserContextMiddleware 解 JWT；
  workflow 后台线程：workflow_runner._worker 显式 bind_user）——生成接口签名零改动。
- 切换仅影响 app/core/llm_transport.py 后续分发到哪个上游通道，
  不改任何既有生成接口签名 / 字段。

provider 语义：
  deepseek   → 既有 app.core.llm_deepseek 通道（内置默认）；
  modelscope → app.core.llm_modelscope（OpenAI 兼容，base_url 指向魔搭 API-Inference）；
  mock       → LLMClient 确定性假数据（llm_provider=mock 时的唯一内置条目，仅展示用）。
自建配置（source="custom"）不论 provider 标签，一律走 app.core.llm_userconf
（OpenAI 兼容通道，per-config base_url + 解密后的 key）。
"""
from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass

from app.core import crypto
from app.core.config import settings

logger = logging.getLogger("app.core.model_registry")


@dataclass(frozen=True)
class ModelSpec:
    """注册表中的一个可选生成模型。"""

    id: str  # 注册表内唯一 id（deepseek 用模型名、魔搭用 org/model_id、自建用 umc_xxx）
    label: str  # 界面显示名
    provider: str  # deepseek | modelscope | mock | openai（自建可为任意 OpenAI 兼容）
    base_url: str
    model_id: str  # 上游 API 的 model 参数
    source: str = "builtin"  # builtin | custom（用户自建配置）
    api_key: str = ""  # 仅 custom：解密后的明文 key（只存在于内存，绝不序列化/入日志）


# 进程级当前模型 id（既有运行态；None = 未显式切换 → 注册表首项即默认 DeepSeek/mock）
_current_id: str | None = None

# 请求/线程上下文中的用户 id（per-user overlay 解析用；None = 无用户上下文）
_user_id_ctx: ContextVar[str | None] = ContextVar("model_registry_user_id", default=None)


def bind_user(user_id: str | None) -> Token:
    """把用户 id 绑定到当前上下文（HTTP 中间件 / workflow 线程调用）。返回 token 供恢复。"""
    return _user_id_ctx.set(user_id)


def unbind_user(token: Token) -> None:
    _user_id_ctx.reset(token)


def current_user_id() -> str | None:
    return _user_id_ctx.get()


def _registry() -> list[ModelSpec]:
    """内置模型注册表（每次现算：测试/热配均即时生效，构建成本可忽略）。"""
    if settings.llm_provider == "mock":
        return [
            ModelSpec(
                id="mock",
                label="内置模拟（离线 Mock）",
                provider="mock",
                base_url="",
                model_id="mock",
            )
        ]
    specs = [
        ModelSpec(
            id=settings.deepseek_model,
            label="DeepSeek（官方 · 默认）",
            provider="deepseek",
            base_url=settings.deepseek_base_url,
            model_id=settings.deepseek_model,
        )
    ]
    for mid in (m.strip() for m in settings.modelscope_models.split(",")):
        if not mid:
            continue
        specs.append(
            ModelSpec(
                id=mid,
                label=f"{mid.split('/')[-1]}（魔搭）",
                provider="modelscope",
                base_url=settings.modelscope_base_url,
                model_id=mid,
            )
        )
    return specs


def _available(spec: ModelSpec) -> bool:
    """该模型当前是否具备直连能力（未配 Key 仍可选，调用时自动回落，见 llm_transport）。"""
    if spec.source == "custom":
        return bool(spec.api_key)
    if spec.provider == "deepseek":
        return bool(settings.deepseek_api_key)
    if spec.provider == "modelscope":
        return bool(settings.modelscope_api_key)
    return True  # mock 恒可用


def _spec_from_config(cfg) -> ModelSpec:
    """UserModelConfig 行 → ModelSpec（此刻解密 key；解密失败 → key 为空 → 调用时降级）。"""
    return ModelSpec(
        id=cfg.id,
        label=cfg.label,
        provider=cfg.provider,
        base_url=cfg.base_url,
        model_id=cfg.model_id,
        source="custom",
        api_key=crypto.decrypt_key(cfg.api_key_encrypted),
    )


def _user_overlay_spec(user_id: str) -> ModelSpec | None:
    """该用户的自建「当前模型」（overlay）。无选择/配置已删 → None（回落进程级）。"""
    # 局部导入避免 model_registry ↔ database 启动期循环依赖
    from app.core.database import SessionLocal
    from app.models.entities import UserModelChoice, UserModelConfig

    with SessionLocal() as db:
        choice = db.get(UserModelChoice, user_id)
        if choice is None:
            return None
        cfg = db.get(UserModelConfig, choice.model_key)
        if cfg is None or cfg.user_id != user_id:
            return None  # 配置已删/异常归属 → 视为无 overlay
        return _spec_from_config(cfg)


def current() -> ModelSpec:
    """当前生成模型（生成链路统一入口）。

    解析顺序：上下文用户的自建 overlay（有效时）→ 进程级运行态 → 注册表首项（默认）。
    任何 DB 异常不阻断生成：回落进程级默认（绝不崩）。
    """
    user_id = _user_id_ctx.get()
    if user_id:
        try:
            spec = _user_overlay_spec(user_id)
            if spec is not None:
                return spec
        except Exception:  # noqa: BLE001 overlay 解析失败不阻断生成
            logger.warning("用户 %s 模型 overlay 解析失败，回落默认模型", user_id)
    specs = _registry()
    if _current_id is not None:
        for s in specs:
            if s.id == _current_id:
                return s
    return specs[0]


def set_current(model_id: str) -> ModelSpec:
    """切换进程级当前模型（内置模型间，既有 §21.2 语义）。未知 id → KeyError（→1001）。"""
    global _current_id
    for s in _registry():
        if s.id == model_id:
            _current_id = s.id
            return s
    raise KeyError(model_id)


def reset() -> None:
    """回到默认模型（测试隔离用，仅进程级运行态）。"""
    global _current_id
    _current_id = None


def snapshot() -> dict:
    """接口 21.1/21.2 响应体（无用户上下文视角）：{models: [...], current}。"""
    return _snapshot_models(customs=[], effective=None)


def user_snapshot(db, user_id: str) -> dict:
    """接口 21.1（登录视角，additive）：内置 + 本人自建配置，isCurrent 按本人生效模型。"""
    from app.models.entities import UserModelChoice, UserModelConfig

    customs = (
        db.query(UserModelConfig)
        .filter(UserModelConfig.user_id == user_id)
        .order_by(UserModelConfig.created_at)
        .all()
    )
    effective = None
    choice = db.get(UserModelChoice, user_id)
    if choice is not None and any(c.id == choice.model_key for c in customs):
        effective = choice.model_key
    return _snapshot_models(customs=customs, effective=effective)


def _snapshot_models(*, customs: list, effective: str | None) -> dict:
    """组装 §21.1 契约：既有字段逐字不变，additive 追加 modelId/baseUrl/source/apiKeyMasked。"""
    builtin_cur = current_builtin()
    current_id = effective or builtin_cur.id
    models = [
        {
            "id": s.id,
            "label": s.label,
            "provider": s.provider,
            "available": _available(s),
            "isCurrent": s.id == current_id,
            "modelId": s.model_id,
            "baseUrl": s.base_url,
            "source": "builtin",
        }
        for s in _registry()
    ]
    for cfg in customs:
        models.append(
            {
                "id": cfg.id,
                "label": cfg.label,
                "provider": cfg.provider,
                "available": bool(cfg.api_key_encrypted),
                "isCurrent": cfg.id == current_id,
                "modelId": cfg.model_id,
                "baseUrl": cfg.base_url,
                "source": "custom",
                "apiKeyMasked": crypto.mask_key(cfg.api_key_last4),
            }
        )
    return {"models": models, "current": current_id}


def current_builtin() -> ModelSpec:
    """进程级当前模型（忽略用户 overlay；未切换/失效 → 注册表首项即默认）。"""
    specs = _registry()
    if _current_id is not None:
        for s in specs:
            if s.id == _current_id:
                return s
    return specs[0]
