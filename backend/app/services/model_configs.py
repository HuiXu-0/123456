"""用户自建模型配置服务（模型管理 21.3+，接口文档第 21 章增量）。

覆盖：
- 21.3 新增/编辑/删除自建模型配置（api_key 经 crypto Fernet 加密落库，无明文）；
- 21.2 per-user 语义扩展：切换到自建配置只写本人 UserModelChoice overlay；
- 21.4 测试连通性（既可测未保存的表单值，也可测已保存配置）。

安全红线（CC-model-management-page.md §3）在本层强制：
- **归属校验**：任何按 id 的读写先查 user_id 归属，非本人一律 1004/404（不泄露存在性）；
- **脱敏返回**：对外 dict 只含 apiKeyMasked（****后四位），永不返回明文/密文 key；
- **日志红线**：本层不打任何含 key 的日志；上游异常串由 llm_userconf 统一 redact。

校验失败以 ModelConfigError 抛出（带业务码 + HTTP status），由 API 层转统一信封。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core import crypto
from app.models.entities import UserModelChoice, UserModelConfig

# 界面可选 provider 类型（均走 OpenAI 兼容通道；modelscope/deepseek 仅作 base_url 预填提示）
PROVIDERS: tuple[str, ...] = ("openai", "modelscope", "deepseek")


class ModelConfigError(Exception):
    """模型配置领域错误：携带业务码（1.3）与 HTTP status，供 API 层套信封。"""

    def __init__(self, code: int, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate(label: str, provider: str, base_url: str, model_id: str) -> None:
    if not label.strip():
        raise ModelConfigError(1001, "显示名不能为空", 400)
    if provider not in PROVIDERS:
        raise ModelConfigError(1001, f"provider 须为 {'/'.join(PROVIDERS)} 之一", 400)
    if not base_url.strip().lower().startswith(("http://", "https://")):
        raise ModelConfigError(1001, "base_url 须为 http(s) 地址", 400)
    if not model_id.strip():
        raise ModelConfigError(1001, "model_id 不能为空", 400)


def _to_dict(cfg: UserModelConfig) -> dict:
    """对外形态（脱敏）：只含 masked key，明文/密文永不出层。"""
    return {
        "id": cfg.id,
        "label": cfg.label,
        "provider": cfg.provider,
        "baseUrl": cfg.base_url,
        "modelId": cfg.model_id,
        "apiKeyMasked": crypto.mask_key(cfg.api_key_last4),
        "createdAt": cfg.created_at.isoformat() if cfg.created_at else None,
        "updatedAt": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


def get_owned(db: Session, user_id: str, config_id: str) -> UserModelConfig:
    """按 id 取本人配置；不存在或非本人 → 1004/404（不泄露他人配置存在性）。"""
    cfg = db.get(UserModelConfig, config_id)
    if cfg is None or cfg.user_id != user_id:
        raise ModelConfigError(1004, "模型配置不存在", 404)
    return cfg


def create_config(
    db: Session,
    user_id: str,
    *,
    label: str,
    provider: str,
    base_url: str,
    model_id: str,
    api_key: str,
) -> dict:
    """新增自建模型配置（21.3）。api_key 必填，加密落库。"""
    _validate(label, provider, base_url, model_id)
    if not api_key.strip():
        raise ModelConfigError(1001, "api_key 不能为空", 400)
    api_key = api_key.strip()
    cfg = UserModelConfig(
        id="umc_" + uuid.uuid4().hex[:12],
        user_id=user_id,
        label=label.strip(),
        provider=provider,
        base_url=base_url.strip().rstrip("/"),
        model_id=model_id.strip(),
        api_key_encrypted=crypto.encrypt_key(api_key),
        api_key_last4=api_key[-4:],
    )
    db.add(cfg)
    db.commit()
    return _to_dict(cfg)


def update_config(
    db: Session,
    user_id: str,
    config_id: str,
    *,
    label: str,
    provider: str,
    base_url: str,
    model_id: str,
    api_key: str | None = None,
) -> dict:
    """编辑本人配置（21.3）。api_key 为空/缺省 → 保留原 key（界面不回显明文的配套语义）。"""
    cfg = get_owned(db, user_id, config_id)
    _validate(label, provider, base_url, model_id)
    cfg.label = label.strip()
    cfg.provider = provider
    cfg.base_url = base_url.strip().rstrip("/")
    cfg.model_id = model_id.strip()
    if api_key and api_key.strip():
        key = api_key.strip()
        cfg.api_key_encrypted = crypto.encrypt_key(key)
        cfg.api_key_last4 = key[-4:]
    cfg.updated_at = _utcnow()
    db.commit()
    return _to_dict(cfg)


def delete_config(db: Session, user_id: str, config_id: str) -> None:
    """删除本人配置（21.3）。若正是本人当前模型 → 一并清 overlay（回落默认，绝不悬空）。"""
    cfg = get_owned(db, user_id, config_id)
    choice = db.get(UserModelChoice, user_id)
    if choice is not None and choice.model_key == config_id:
        db.delete(choice)
    db.delete(cfg)
    db.commit()


def set_user_current(db: Session, user_id: str, model_id: str) -> bool:
    """21.2 per-user 语义：目标为本人自建配置 → 写 overlay，返回 True；
    否则返回 False（由调用方走既有内置切换）。"""
    cfg = db.get(UserModelConfig, model_id)
    if cfg is None or cfg.user_id != user_id:
        return False
    choice = db.get(UserModelChoice, user_id)
    if choice is None:
        db.add(UserModelChoice(user_id=user_id, model_key=model_id, updated_at=_utcnow()))
    else:
        choice.model_key = model_id
        choice.updated_at = _utcnow()
    db.commit()
    return True


def clear_user_current(db: Session, user_id: str) -> None:
    """清除本人 overlay（切回内置模型时调用）。"""
    choice = db.get(UserModelChoice, user_id)
    if choice is not None:
        db.delete(choice)
        db.commit()


def resolve_test_target(
    db: Session,
    user_id: str,
    *,
    config_id: str | None,
    base_url: str | None,
    model_id: str | None,
    api_key: str | None,
) -> dict:
    """测试连通性（21.4）目标解析：configId（用库中解密 key，可被表单值覆盖）或纯表单值。

    返回 {base_url, model_id, api_key, label}（内存态，调用后即弃；不落任何日志）。
    """
    if config_id:
        cfg = get_owned(db, user_id, config_id)
        return {
            "base_url": (base_url or cfg.base_url).strip().rstrip("/"),
            "model_id": (model_id or cfg.model_id).strip(),
            "api_key": (api_key or "").strip() or crypto.decrypt_key(cfg.api_key_encrypted),
            "label": cfg.label,
        }
    if not (base_url and base_url.strip()) or not (model_id and model_id.strip()):
        raise ModelConfigError(1001, "缺少 baseUrl / modelId（或改传 configId）", 400)
    if not (api_key and api_key.strip()):
        raise ModelConfigError(1001, "api_key 不能为空", 400)
    return {
        "base_url": base_url.strip().rstrip("/"),
        "model_id": model_id.strip(),
        "api_key": api_key.strip(),
        "label": model_id.strip(),
    }
